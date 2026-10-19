"""Application initialization and stop signals own real IO/Agent lifetimes."""
from __future__ import annotations

import asyncio

import pytest
from xdog.agent.session_input import SessionInput
from xdog.claw.config import ClawConfig
from xdog.claw.core.inputs.consumer import InputService
from xdog.claw.core.inputs.models import InputEnvelope, InputScope
from xdog.claw.core.inputs.store import InputStore
from xdog.claw.core.runtime.orchestrator import Orchestrator
from xdog.claw.core.types import Group, UserInput

from packages.claw.tests.eyes_helpers import ScriptedModel
from packages.claw.tests.test_gateway_inputs import gateway as gateway


async def test_request_stop_refuses_new_intake_and_interrupts_execution_before_async_join(tmp_path):
    entered, cancelled = asyncio.Event(), asyncio.Event()
    async def execute(record, admit):
        await admit()
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    store = await InputStore.open(tmp_path / 'private' / 'inputs.sqlite3')
    service = InputService(store, execute)
    scope = InputScope('main', tmp_path)
    item = InputEnvelope(scope, SessionInput('one', 'human', 'TUI', 'check', (), {}))
    try:
        await service.submit(item)
        await entered.wait()
        service.request_stop()
        with pytest.raises(RuntimeError, match='closing'):
            await service.submit(InputEnvelope(scope, SessionInput('two', 'human', 'TUI', 'no', (), {})))
        await asyncio.wait_for(cancelled.wait(), 2)
        await service.close()
    finally:
        await service.close()


async def test_cancelled_initializer_finishes_all_scopes_before_publishing_owner(tmp_path, monkeypatch):
    model = ScriptedModel()
    orch = Orchestrator(ClawConfig(data_dir=str(tmp_path / 'data')), model='test/dummy', stream_fn=model)
    for name in ('first', 'second'):
        orch.register_group(Group(id=name, workspace=str(tmp_path / name), enabled_tools=()))
    entered, release = asyncio.Event(), asyncio.Event()
    activate = InputService.activate
    async def held(service, scope, **kwargs):
        await activate(service, scope, **kwargs)
        if scope.group_id == 'first':
            entered.set()
            await release.wait()
    monkeypatch.setattr(InputService, 'activate', held)
    opening = asyncio.create_task(orch.input_service())
    try:
        await entered.wait()
        opening.cancel()
        await asyncio.sleep(0)
        assert not opening.done(), 'Caller cancellation abandoned an admitted application initialization'
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await opening
        service = await orch.input_service()
        for name in ('first', 'second'):
            assert await service.store.namespace(InputScope(name, tmp_path / name))
        assert model.calls == []
    finally:
        release.set()
        await asyncio.gather(opening, return_exceptions=True)
        await orch.stop()


async def test_failed_initialization_does_not_publish_half_ready_service_or_run_pending_input(tmp_path):
    from xdog.claw.core.runtime.input_adapter import freeze_input
    path = tmp_path / 'data' / 'inputs' / 'inbox.sqlite3'
    store = await InputStore.open(path)
    await store.enqueue(freeze_input(UserInput(
        group_id='first', content='must wait for ready'), tmp_path / 'first', message_id='pending'))
    await store._disk(lambda db: db.execute("CREATE TRIGGER refuse_second BEFORE INSERT ON scopes "
        "WHEN NEW.group_id='second' BEGIN SELECT RAISE(ABORT,'scope unavailable'); END"))
    await store.close()
    model = ScriptedModel()
    orch = Orchestrator(ClawConfig(data_dir=str(tmp_path / 'data')), model='test/dummy', stream_fn=model)
    for name in ('first', 'second'):
        orch.register_group(Group(id=name, workspace=str(tmp_path / name), enabled_tools=()))
    try:
        with pytest.raises(Exception, match='scope unavailable'):
            await orch.input_service()
        assert orch._inputs is None and model.calls == []
        reopened = await InputStore.open(path)
        try:
            assert (await reopened.get(InputScope('first', tmp_path / 'first'), 'pending')).status == 'pending'
        finally:
            await reopened.close()
    finally:
        await orch.stop()


async def test_gateway_stop_signals_agent_before_waiting_for_blocked_transport_cleanup(gateway, monkeypatch):
    import threading

    from xdog.claw.core.runtime.source_protocol import SourceSender

    from packages.claw.tests.test_gateway_inputs import receive, send
    server, entered, release_model, _contexts, socket_path = gateway
    reader, writer = await asyncio.open_unix_connection(socket_path)
    cleanup_entered, agent_aborted = asyncio.Event(), asyncio.Event()
    release_cleanup = threading.Event()
    close_source = SourceSender.close
    async def held_close(sender):
        cleanup_entered.set()
        await asyncio.to_thread(release_cleanup.wait)
        await close_source(sender)
    monkeypatch.setattr(SourceSender, 'close', held_close)
    closing = None
    try:
        await send(writer, dict(type='message', group_id='main', run_id='shutdown-owner', content='HOLD'))
        await receive(reader, 'run_ack')
        await asyncio.wait_for(entered.wait(), 2)
        agent = server._orchestrator._runtimes['main']._active_session.agent
        abort = agent.abort
        def observed_abort():
            abort()
            agent_aborted.set()
        monkeypatch.setattr(agent, 'abort', observed_abort)
        closing = asyncio.create_task(server.stop())
        await asyncio.wait_for(cleanup_entered.wait(), 2)
        waiter = asyncio.create_task(agent_aborted.wait())
        done, _ = await asyncio.wait([waiter], timeout=2)
        try:
            assert waiter in done and agent.cancellation_requested, 'Transport cleanup postponed application stop'
            assert not closing.done()
        finally:
            waiter.cancel()
            await asyncio.gather(waiter, return_exceptions=True)
    finally:
        release_cleanup.set()
        release_model.set()
        if closing is not None:
            await asyncio.wait_for(closing, 5)
        writer.close()
        await writer.wait_closed()
