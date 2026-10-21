"""Trusted execution/receipt hints, exact ACP targets, and actual user command scope."""
from __future__ import annotations

import asyncio
import json

import pytest
from xdog.agent.session_input import SessionInput
from xdog.coding.core.capability_ports import SessionCapabilityHost
from xdog.coding.core.sessions.runtime import SessionRuntime
from xdog.coding.core.slash_commands import execute_command

from packages.coding.tests.test_child_agents import create


@pytest.fixture(autouse=True)
def no_business_providers(monkeypatch):
    monkeypatch.setattr('xdog.agent.capabilities.runtime.discover', lambda scope: ())


async def owner(tmp_path, *, block=False):
    session, calls = create(tmp_path, block=block)
    runtime = SessionRuntime(session, bound=True)
    runtime.start(setup_pending=True)
    await runtime.activate_inputs()
    host = await SessionCapabilityHost.open(session)
    return session, calls, runtime, host


async def until(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(.001)


async def test_settled_requires_actual_history_and_both_receipts_then_idle_available(tmp_path, monkeypatch):
    session, _, runtime, host = await owner(tmp_path)
    events = []
    cancel = host.subscribe_events(events.append)
    entered, release = asyncio.Event(), asyncio.Event()
    original = runtime.controller.store.transition_batch
    async def delayed(ids, status, **kwargs):
        if status == 'succeeded':
            entered.set()
            await release.wait()
        return await original(ids, status, **kwargs)
    monkeypatch.setattr(runtime.controller.store, 'transition_batch', delayed)
    try:
        runtime.controller.publish('execution.settled', {'status': 'succeeded'})
        assert not [e for e in events if e['type'] == 'execution.settled']
        ticket = runtime.controller.submit(SessionInput('one', 'acp', 'human user', 'inspect', (), {}), peer_id='test')
        await asyncio.wrap_future(ticket.accepted)
        await asyncio.wait_for(entered.wait(), 3)
        assert not [e for e in events if e['type'] == 'execution.settled']
        persisted = await asyncio.to_thread(session.session_manager.load_session, session.session_id)
        assert persisted.messages and runtime.controller.store.records()[0].status == 'running'
        assert json.loads(runtime.controller.store.path.read_text())[0]['status'] == 'running'
        release.set()
        assert (await asyncio.wrap_future(ticket.result)).status == 'succeeded'
        await until(lambda: any(e['type'] == 'owner.available' and e['snapshot']['state'] == 'idle'
                                for e in events[1:]))
        settled, = [e for e in events if e['type'] == 'execution.settled']
        assert settled['automatic'] is True and settled['generation'] == runtime.controller.generation
        assert runtime.controller.store.records()[0].status == 'succeeded'
        assert not session._user_operation_authorized
        events[-1]['snapshot']['state'] = 'forged'
        assert host.execution_snapshot()['state'] == 'idle'
        cancel()
        count = len(events)
        await asyncio.wrap_future(runtime.controller.control(lambda: asyncio.sleep(0), name='ordinary'))
        assert len(events) == count
    finally:
        release.set()
        await session.aclose()


async def test_failed_terminal_persistence_is_unavailable_not_false_settled(tmp_path, monkeypatch):
    session, _, runtime, host = await owner(tmp_path)
    events = []
    host.subscribe_events(events.append)
    original = runtime.controller.store.transition_batch
    async def fail(ids, status, **kwargs):
        if status == 'succeeded':
            raise OSError('terminal commit failed')
        return await original(ids, status, **kwargs)
    monkeypatch.setattr(runtime.controller.store, 'transition_batch', fail)
    try:
        ticket = runtime.controller.submit(SessionInput('one', 'human', 'actual SDK user', 'inspect', (), {}),
                                           peer_id='test')
        assert (await asyncio.wrap_future(ticket.result)).status == 'failed'
        assert not [e for e in events if e['type'] == 'execution.settled']
        assert host.execution_snapshot()['state'] == 'unavailable'
    finally:
        await session.aclose()


async def test_cancel_settles_actual_attempt_and_close_publishes_retiring(tmp_path):
    session, calls, runtime, host = await owner(tmp_path, block=True)
    events = []
    host.subscribe_events(events.append)
    try:
        ticket = runtime.controller.submit(SessionInput('one', 'acp', 'human', 'inspect', (), {}), peer_id='test')
        await until(lambda: bool(calls))
        assert await asyncio.wrap_future(runtime.controller.cancel('one', peer_id='test', wait=True))
        assert (await asyncio.wrap_future(ticket.result)).status == 'cancelled'
        assert any(e['type'] == 'execution.settled' and e['status'] == 'cancelled' for e in events)
        await until(lambda: host.execution_snapshot()['state'] == 'idle')
    finally:
        await session.aclose()
    assert any(e['type'] == 'owner.retiring' and e['snapshot']['state'] == 'closing' for e in events)
    assert await host.acp_target() is None


async def test_exact_live_acp_target_enqueues_without_waiting_for_busy_owner_result(tmp_path):
    from acp import PROTOCOL_VERSION, connect_to_agent
    session, calls, runtime, host = await owner(tmp_path)
    try:
        target = await host.acp_target()
        assert target == {'endpoint': str(runtime.record.address), 'session_id': session.session_id,
            'cwd': str(tmp_path), 'inbox_id': host.namespace, 'branch_id': session.session_data.active_branch}
        class Client:
            async def session_update(self, **kwargs):
                pass
        async def held():
            reader, writer = await asyncio.open_unix_connection(target['endpoint'])
            conn = connect_to_agent(Client(), writer, reader)
            try:
                await conn.initialize(protocol_version=PROTOCOL_VERSION)
                result = await conn.ext_method('xdog/enqueue', {'session_id': target['session_id'],
                    'cwd': target['cwd'], 'message_id': 'automatic', 'instruction': '/custom',
                    'source': 'human user', 'evidence': [], 'provenance': {'authorized': True}})
                assert result['status'] == 'queued' and not calls
            finally:
                await conn.close()
                writer.close()
                await writer.wait_closed()
        await asyncio.wait_for(asyncio.wrap_future(runtime.controller.control(held, name='held-control')), 3)
        await until(lambda: bool(calls) and host.execution_snapshot()['state'] == 'idle')
        session.session_data.active_branch = 'different'
        assert await host.acp_target() is None and host.execution_snapshot()['state'] == 'unavailable'
    finally:
        await session.aclose()
    assert runtime.server is None and not runtime.record.address.exists()


async def test_plain_sdk_has_no_receiver_and_does_not_create_controller(tmp_path):
    session, _ = create(tmp_path)
    host = await SessionCapabilityHost.open(session)
    try:
        assert await host.acp_target() is None and session._sdk_controller is None
        assert host.execution_snapshot()['state'] == 'unavailable'
    finally:
        await session.aclose()


@pytest.mark.parametrize('fail', [False, True])
async def test_real_native_slash_callback_has_user_scope_across_await_and_restores(tmp_path, fail):
    session, _ = create(tmp_path)
    observations = []
    async def command(args):
        observations.append(session._user_operation_authorized)
        await asyncio.sleep(0)
        observations.append(session._user_operation_authorized)
        if fail:
            raise ValueError('native command failed')
        return 'actual command'
    remove = session.extensions.bind_native_commands('native_test', {'custom': ('Test', command)}, valid=lambda: True)
    try:
        if fail:
            with pytest.raises(ValueError, match='native command failed'):
                await execute_command('custom', '', session)
        else:
            assert (await execute_command('custom', '', session)).output == 'actual command'
        assert observations == [True, True] and not session._user_operation_authorized
    finally:
        remove()
        await session.aclose()


async def test_cancelled_endpoint_open_waiter_and_close_join_actual_socket_publication(tmp_path, monkeypatch):
    from xdog.coding.acp import server
    session, _, runtime, host = await owner(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    original = server.serve_local
    async def delayed(current):
        result = await original(current)
        entered.set()
        await release.wait()
        return result
    monkeypatch.setattr(server, 'serve_local', delayed)
    opening = asyncio.create_task(host.acp_target())
    closing = None
    try:
        await asyncio.wait_for(entered.wait(), 3)
        opening.cancel()
        closing = asyncio.create_task(session.aclose())
        await asyncio.sleep(.01)
        assert not opening.done() and not closing.done()
        assert session._lease is not None and session._lease._fd >= 0
        assert await host.acp_target() is None
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await opening
        await closing
        assert runtime.server is None and not runtime.record.address.exists() and session._lease is None
    finally:
        release.set()
        await asyncio.gather(opening, return_exceptions=True)
        if closing is not None:
            await closing
        else:
            await session.aclose()
