"""Actual Core commits, group exclusion, endpoint ownership and native command authority."""
from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.agent.session_input import SessionInput
from xdog.ai.types import AssistantMessage, TextContent
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.acp.server import ClawAcpServer
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.inputs.consumer import InputOutcome, InputService
from xdog.claw.core.inputs.models import InputEnvelope, InputScope
from xdog.claw.core.inputs.store import InputStore
from xdog.claw.core.runtime.gateway import GatewayServer
from xdog.claw.core.runtime.group_controller import GroupController
from xdog.claw.core.runtime.orchestrator import Orchestrator
from xdog.claw.core.types import Group


@pytest.fixture(autouse=True)
def no_business_providers(monkeypatch):
    monkeypatch.setattr('xdog.agent.capabilities.runtime.discover', lambda scope: ())


def stream(*args, **kwargs):
    return EventStream.empty(AssistantMessage(content=(TextContent(text='actual response'),)))


async def test_actual_agent_history_and_receipt_commit_precede_settled_idle_snapshot(tmp_path, monkeypatch):
    scope = InputScope('main', tmp_path)
    controller = GroupController('main', asyncio.Semaphore(1))
    agent = Agent(stream, config=AgentConfig(model='test/dummy'))
    history = tmp_path / 'actual-history.json'
    async def execute(record, admit):
        assert await admit('actual-session')
        response = await agent.prompt(record.envelope.message.instruction)
        async for _ in response:
            pass
        await asyncio.to_thread(history.write_text, json.dumps([m.role for m in agent.messages]))
        return InputOutcome(response='actual response', session_id='actual-session')
    store = await InputStore.open(tmp_path / 'inputs' / 'journal.sqlite3')
    service = InputService(store, execute,
        execution_scope=lambda record: controller.acquire(input_key=(record.envelope.message.id, record.attempt_id)),
        snapshot=lambda owner: {'state': 'running' if controller.busy else 'idle', 'user_pending': False,
                               'children_active': False, 'generation': controller.generation})
    controller.subscribe_available(lambda: service.publish_available(scope))
    events = []
    service.subscribe_events(scope, events.append)
    entered, release = asyncio.Event(), asyncio.Event()
    original = store.finish
    async def delayed(*args, **kwargs):
        entered.set()
        await release.wait()
        return await original(*args, **kwargs)
    monkeypatch.setattr(store, 'finish', delayed)
    try:
        message = SessionInput('original', 'acp', 'human user', 'inspect', (), {'approved': True})
        await service.submit(InputEnvelope(scope, message))
        await asyncio.wait_for(entered.wait(), 3)
        assert history.exists() and (await store.get(scope, message.id)).status == 'running'
        assert not [e for e in events if e['type'] == 'execution.settled']
        assert not service.execution_snapshot(scope)['user_pending']
        release.set()
        result = await service.wait(scope, message.id)
        assert result.status == 'succeeded'
        await asyncio.sleep(0)
        settled, = [e for e in events if e['type'] == 'execution.settled']
        assert settled['execution_id'] == result.attempt_id and settled['automatic'] is True
        assert settled['snapshot']['state'] == 'idle' and not controller.busy
        assert any(e['type'] == 'owner.available' and e['snapshot']['state'] == 'idle' for e in events)
        settled['snapshot']['state'] = 'forged'
        assert service.execution_snapshot(scope)['state'] == 'idle'
    finally:
        release.set()
        await service.close()
        await agent.aclose()


async def test_shutdown_joins_cancellation_before_terminal_hint_and_pending_human_is_host_bound(tmp_path):
    scope = InputScope('main', tmp_path)
    entered, cleanup, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def execute(record, admit):
        assert await admit('original')
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleanup.set()
            await release.wait()
        return InputOutcome()
    store = await InputStore.open(tmp_path / 'inputs' / 'journal.sqlite3')
    service = InputService(store, execute)
    events = []
    service.subscribe_events(scope, events.append)
    message = SessionInput('original', 'human', 'actual native user', 'inspect', (), {})
    try:
        await service.submit(InputEnvelope(scope, message, human_authorized=True))
        await asyncio.wait_for(entered.wait(), 3)
        assert service.execution_snapshot(scope)['user_pending']
        closing = asyncio.create_task(service.close())
        await asyncio.wait_for(cleanup.wait(), 3)
        assert not closing.done() and not [e for e in events if e['type'] == 'execution.settled']
        assert any(e['type'] == 'owner.retiring' for e in events)
        release.set()
        await closing
        settled, = [e for e in events if e['type'] == 'execution.settled']
        assert settled['status'] == 'interrupted' and settled['automatic'] is False
        assert settled['snapshot']['state'] == 'closing'
    finally:
        release.set()
        await service.close()
    reopened = await InputStore.open(store.path)
    try:
        assert (await reopened.get(scope, message.id)).status == 'interrupted'
    finally:
        await reopened.close()


async def app_owner(tmp_path):
    app = Orchestrator(ClawConfig(data_dir=str(tmp_path / 'data')), model='test/dummy', stream_fn=stream)
    app.register_group(Group(id='main', workspace=str(tmp_path / 'work')))
    await app.start()
    runtime = app._runtimes['main']
    return app, runtime, runtime._capability_host


async def test_private_endpoint_target_fences_close_and_stays_group_scoped_across_context_rotation(tmp_path):
    app, runtime, host = await app_owner(tmp_path)
    server = successor = None
    try:
        generation = host.execution_snapshot()['generation']
        assert await host.acp_target() is None and runtime._active_session is None
        with tempfile.TemporaryDirectory(prefix='ports-') as short:
            endpoint = Path(short) / 'acp.sock'
            server = await ClawAcpServer.start(app, endpoint)
            target = await host.acp_target()
            assert target == {'endpoint': str(endpoint), 'session_id': 'group:main',
                'cwd': str(runtime.workspace_dir), 'inbox_id': host.namespace, 'branch_id': ''}
            parent = await host.prepare_parent()
            runtime.reset_session()
            fresh = await host.prepare_parent()
            assert fresh.agent is not parent.agent and not parent.valid()
            assert host.execution_snapshot()['generation'] == generation and await host.acp_target() == target
            await server.close()
            assert await host.acp_target() is None
            successor = await ClawAcpServer.start(app, endpoint)
            await server.close()  # A repeated old close cannot retire a new endpoint.
            assert await host.acp_target() == target
            await successor.close()
            assert await host.acp_target() is None
    finally:
        if successor is not None:
            await successor.close()
        if server is not None:
            await server.close()
        await app.stop()
    assert host.execution_snapshot()['state'] == 'closing'


@pytest.mark.parametrize('fail', [False, True])
async def test_native_user_command_scope_restores_after_await_and_failure(tmp_path, fail):
    app, runtime, host = await app_owner(tmp_path)
    observations = []
    parent = await host.prepare_parent()
    session = runtime._active_session
    async def callback(args):
        observations.append(parent.user_authorized())
        await asyncio.sleep(0)
        observations.append(parent.user_authorized())
        if fail:
            raise ValueError('actual native command failure')
        return 'actual command'
    remove = session.extensions.bind_native_commands('native_test', {'custom': ('Test', callback)}, valid=parent.valid)
    try:
        if fail:
            with pytest.raises(ValueError, match='actual native command failure'):
                await app.manage_extensions('main', 'command', 'custom', '', session.meta.session_id)
        else:
            result = await app.manage_extensions('main', 'command', 'custom', '', session.meta.session_id)
            assert result['output'] == 'actual command'
        assert observations == [True, True] and not parent.user_authorized()
        assert host.execution_snapshot()['state'] == 'idle'
    finally:
        remove()
        await app.stop()


@pytest.mark.parametrize('scope,hard,available', [
    ('group', True, True), ('group', False, True), ('application', False, True),
    ('group', False, False), ('application', False, False), ('group', True, False),
])
async def test_gateway_default_acp_is_independent_of_optional_capability_requirements(
        tmp_path, monkeypatch, scope, hard, available):
    class Binding:
        tools = ()
        async def flush(self):
            pass
        async def aclose(self):
            pass
    class Provider:
        name = 'arbitrary'
        scopes = (scope,)
        tool_names = ()
        requirements = ('acp',) if hard else ()
        optional_requirements = () if hard else ('acp',)
        async def open(self, host):
            return Binding()
    monkeypatch.setattr('xdog.agent.capabilities.runtime.discover',
                        lambda owner: (Provider(),) if owner == scope else ())
    if not available:
        monkeypatch.setattr('xdog.claw.core.runtime.gateway.find_spec', lambda name: None)
    monkeypatch.setattr('xdog.claw.core.runtime.gateway._build_model_and_options',
                        lambda config: ('test/dummy', stream))
    with tempfile.TemporaryDirectory(prefix='group-ports-') as short:
        root = Path(short)
        gateway = GatewayServer(ClawConfig(data_dir=str(root / 'data'), socket_path=str(root / 'native.sock'),
            pid_file=str(root / 'native.pid'), groups=(GroupDef(id='main', workspace=str(tmp_path)),)))
        try:
            if not available:
                with pytest.raises(RuntimeError, match='requires the ACP SDK'):
                    await gateway.start()
                assert not (root / 'native.sock').exists()
                return
            await gateway.start()
            assert gateway._application_capabilities.tools == ()
            assert gateway._acp_server is not None
            runtime = gateway._orchestrator._runtimes['main']
            target = await runtime._capability_host.acp_target()
            assert target['endpoint'] == str(gateway._acp_server.path)
            assert target['session_id'] == 'group:main'
        finally:
            await gateway.stop()


async def test_endpoint_detach_failure_still_closes_socket_and_retires_published_target(tmp_path):
    app, _, host = await app_owner(tmp_path)
    with tempfile.TemporaryDirectory(prefix='ports-close-') as short:
        endpoint = Path(short) / 'acp.sock'
        server = await ClawAcpServer.start(app, endpoint)
        release = server._release_endpoint
        def failed():
            release()
            raise OSError('injected detach failure')
        server._release_endpoint = failed
        try:
            with pytest.raises(OSError, match='injected detach failure'):
                await server.close()
            assert not endpoint.exists() and await host.acp_target() is None
            assert server._endpoint is None
        finally:
            await app.stop()


@pytest.mark.parametrize('operation', ['namespace', 'target'])
@pytest.mark.parametrize('retiring', [False, True])
async def test_acp_target_joins_inflight_io_and_only_suppresses_actual_retirement(tmp_path, monkeypatch,
                                                                               operation, retiring):
    app, _, host = await app_owner(tmp_path)
    inputs = await app.input_service()
    entered, release = asyncio.Event(), asyncio.Event()
    async def failing(*args):
        entered.set()
        await release.wait()
        raise RuntimeError('injected actual namespace/endpoint failure')
    if operation == 'namespace':
        monkeypatch.setattr(inputs.store, 'namespace', failing)
    else:
        monkeypatch.setattr(inputs, 'acp_target', failing)
    pending = asyncio.create_task(host.acp_target())
    try:
        await asyncio.wait_for(entered.wait(), 3)
        if retiring:
            app.request_stop()
        assert not pending.done()
        release.set()
        if retiring:
            assert await pending is None
        else:
            with pytest.raises(RuntimeError, match='injected actual'):
                await pending
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)
        await app.stop()
