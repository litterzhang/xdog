"""Durable native execution facts, actual start ordering, and cold owner recovery."""
from __future__ import annotations

import asyncio
import json
import threading
import time

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.agent.session_input import SessionInput
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.capability_ports import SessionCapabilityHost
from xdog.coding.core.sessions.controller import SessionController
from xdog.coding.core.sessions.models import TurnResult
from xdog.coding.core.sessions.runtime import SessionRuntime

from packages.coding.tests.test_child_agents import create


@pytest.fixture(autouse=True)
def no_business_providers(monkeypatch):
    monkeypatch.setattr('xdog.agent.capabilities.runtime.discover', lambda scope: ())


async def owner(tmp_path):
    session, calls = create(tmp_path)
    runtime = SessionRuntime(session, bound=True)
    runtime.start(setup_pending=True)
    await runtime.activate_inputs()
    return session, calls, runtime, await SessionCapabilityHost.open(session)


async def reopen(session):
    data = await asyncio.to_thread(session.session_manager.load_session, session.session_id)
    def forbidden_stream(*args, **kwargs):
        pytest.fail("cold query must not call a model")
    agent = Agent(forbidden_stream, config=AgentConfig(model="test/model"))
    restored = AgentSession(agent=agent, session_data=data, session_manager=session.session_manager,
        settings=session.settings, tool_registry=None, bash=None, working_dir=session.working_dir,
        permissions=session.permissions)
    return restored, await SessionCapabilityHost.open(restored)


async def submit(runtime, identifier='human'):
    ticket = runtime.controller.submit(SessionInput(identifier, 'human', 'user', 'inspect', (), {}), peer_id='test')
    await asyncio.wrap_future(ticket.accepted)
    return await asyncio.wrap_future(ticket.result)


async def test_direct_human_turn_matches_settled_and_survives_cold_owner_without_controller(tmp_path, monkeypatch):
    session, calls, runtime, host = await owner(tmp_path)
    events = []
    cancel = host.subscribe_events(events.append)
    try:
        assert await host.latest_execution() is None
        outcome = await submit(runtime)
        expected = await host.latest_execution()
        assert expected['execution_id'] == outcome.batch_id
        assert expected['status'] == 'succeeded' and expected['message_ids'] == ['human']
        assert expected['start_order'] == 1
        assert 0 < expected['started_at'] <= expected['settled_at'] <= time.time()
        settled, = [event for event in events if event['type'] == 'execution.settled']
        assert settled['execution_id'] == outcome.batch_id
        path = runtime.controller.store.path
        before = path.read_bytes()
        cancel()
    finally:
        await session.aclose()
    restored, cold = await reopen(session)
    try:
        def forbidden(*args, **kwargs):
            pytest.fail('read-only recovery must not construct a controller or Agent')
        monkeypatch.setattr(SessionController, '__init__', forbidden)
        monkeypatch.setattr(cold, 'prepare_parent', forbidden)
        count = len(calls)
        assert await cold.latest_execution() == expected
        assert path.read_bytes() == before
        assert len(calls) == count and restored._sdk_controller is None and restored.runtime is None
        result = await cold.latest_execution()
        result['message_ids'].append('forged')
        assert await cold.latest_execution() == expected
    finally:
        await restored.aclose()


async def test_raw_running_is_not_reinterpreted_and_actual_owner_persists_interruption(tmp_path):
    session, calls, runtime, host = await owner(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    async def execute(batch):
        entered.set()
        await release.wait()
        return TurnResult('succeeded', None)
    runtime.controller._execute_batch = execute
    ticket = runtime.controller.submit(SessionInput('crashed', 'human', 'user', 'inspect', (), {}), peer_id='test')
    await asyncio.wait_for(entered.wait(), 3)
    path = runtime.controller.store.path
    started = path.read_bytes()
    current = await host.latest_execution()
    assert current['status'] == 'running'
    release.set()
    await asyncio.wrap_future(ticket.result)
    await session.aclose()
    # Restore the exact durable running ledger captured before simulated process death.
    path.write_bytes(started)
    restored, cold = await reopen(session)
    try:
        # A different generation is not durable proof of process death.
        assert await cold.latest_execution() == current
        assert path.read_bytes() == started and not calls
        recovered = SessionRuntime(restored, bound=True)
        recovered.start(setup_pending=True)
        # The real store's recovery interpretation becomes durable on its next
        # accepted input. Keep intake paused so no new execution/model starts.
        ticket = recovered.controller.submit(
            SessionInput('pending', 'human', 'user', 'inspect', (), {}), peer_id='test')
        await asyncio.wrap_future(ticket.accepted)
        owned = await SessionCapabilityHost.open(restored)
        fact = await owned.latest_execution()
        assert fact['execution_id'] == current['execution_id'] and fact['status'] == 'interrupted'
        assert fact['started_at'] == current['started_at']
        assert fact['settled_at'] > current['started_at']
        assert not calls
    finally:
        await restored.aclose()


@pytest.mark.parametrize('status', ['failed', 'cancelled'])
async def test_real_failed_or_cancelled_execution_has_persisted_settlement(tmp_path, status):
    session, _, runtime, host = await owner(tmp_path)
    async def execute(batch):
        return TurnResult(status, None)
    runtime.controller._execute_batch = execute
    try:
        assert (await submit(runtime)).status == status
        fact = await host.latest_execution()
        assert fact['status'] == status and fact['settled_at'] >= fact['started_at']
    finally:
        await session.aclose()


async def test_pre_start_failure_also_gets_batch_id_but_is_not_fabricated_as_started(tmp_path, monkeypatch):
    session, _, runtime, host = await owner(tmp_path)
    async def failed_before_start(batch):
        raise RuntimeError('preparation did not reach durable running')
    monkeypatch.setattr(runtime.inbox, 'starting', failed_before_start)
    try:
        outcome = await submit(runtime)
        assert outcome.status == 'failed' and outcome.batch_id
        row, = json.loads(runtime.controller.store.path.read_bytes())
        assert row['batch_id'] == outcome.batch_id and row['status'] == 'failed'
        assert await host.latest_execution() is None
    finally:
        await session.aclose()
    restored, cold = await reopen(session)
    try:
        assert await cold.latest_execution() is None
    finally:
        await restored.aclose()


@pytest.mark.parametrize("status", ["succeeded", "failed"])
async def test_multiple_native_batches_use_persistent_start_order(tmp_path, status):
    session, _, runtime, host = await owner(tmp_path)
    try:
        await submit(runtime, 'one')
        async def execute(batch):
            return TurnResult(status, None)
        runtime.controller._execute_batch = execute
        second = await submit(runtime, 'two')
        fact = await host.latest_execution()
        assert fact['execution_id'] == second.batch_id and fact['message_ids'] == ['two']
        assert fact['start_order'] == 2 and fact['settled_at'] >= fact['started_at']
        assert fact['status'] == status
    finally:
        await session.aclose()


async def test_branch_namespace_and_storage_failures_are_not_empty(tmp_path):
    session, _, runtime, host = await owner(tmp_path)
    try:
        await submit(runtime)
        path = runtime.controller.store.path
        raw = path.read_bytes()
        data = json.loads(raw)
        data[0]['branch_id'] = 'other'
        path.write_text(json.dumps(data))
        assert await host.latest_execution() is None
        path.write_bytes(raw)
        session.session_data.active_branch = 'other'
        with pytest.raises(ValueError, match='owner'):
            await host.latest_execution()
        session.session_data.active_branch = host.branch
        namespace = host._inbox.root / '.store-id'
        original = namespace.read_bytes()
        namespace.write_text(json.dumps({'version': 1, 'id': 'a' * 32}))
        with pytest.raises(ValueError, match='namespace'):
            await host.latest_execution()
        namespace.write_bytes(original)
        path.write_text('corrupt')
        with pytest.raises(ValueError):
            await host.latest_execution()
        path.write_bytes(raw)
        namespace.unlink()
        with pytest.raises(FileNotFoundError):
            await host.latest_execution()
        assert not namespace.exists()
        namespace.write_bytes(original)
    finally:
        await session.aclose()


async def test_owned_offloop_read_joins_cancellation_and_fences_navigation(tmp_path, monkeypatch):
    session, _ = create(tmp_path)
    host = await SessionCapabilityHost.open(session)
    from xdog.coding.core import capability_ports
    original = capability_ports._read
    entered, release = threading.Event(), threading.Event()
    def delayed(*args, **kwargs):
        assert threading.get_ident() != owner_thread
        entered.set()
        assert release.wait(3)
        return original(*args, **kwargs)
    owner_thread = threading.get_ident()
    monkeypatch.setattr(capability_ports, '_read', delayed)
    try:
        pending = asyncio.create_task(host.latest_execution())
        assert await asyncio.to_thread(entered.wait, 3)
        pending.cancel()
        await asyncio.sleep(.01)
        assert not pending.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await pending
        entered.clear()
        release.clear()
        pending = asyncio.create_task(host.latest_execution())
        assert await asyncio.to_thread(entered.wait, 3)
        session.session_data.active_branch = 'other'
        release.set()
        with pytest.raises(ValueError, match='owner'):
            await pending
        session.session_data.active_branch = host.branch
        host.retire()
        assert await host.latest_execution() is None
    finally:
        release.set()
        await session.aclose()


async def test_real_human_priority_reverses_intake_but_latest_uses_actual_start_order(tmp_path):
    session, _, runtime, host = await owner(tmp_path)
    controller = runtime.controller
    controller._inputs_ready = False
    controller.max_messages_per_turn = 1
    executed = []
    async def execute(batch):
        executed.extend(batch.request_ids)
        return TurnResult('succeeded', None)
    controller._execute_batch = execute
    try:
        automatic = controller.submit(SessionInput('automatic', 'acp', 'external', 'inspect', (), {}), peer_id='test')
        await asyncio.wrap_future(automatic.accepted)
        human = controller.submit(SessionInput('human', 'human', 'user', 'inspect', (), {}), peer_id='test')
        await asyncio.wrap_future(human.accepted)
        controller._inputs_ready = True
        controller._pump()
        await asyncio.gather(asyncio.wrap_future(automatic.result), asyncio.wrap_future(human.result))
        rows = json.loads(controller.store.path.read_bytes())
        assert [row['input']['id'] for row in rows] == ['automatic', 'human']
        assert executed == ['human', 'automatic']
        assert [row['start_order'] for row in rows] == [2, 1]
        expected = await host.latest_execution()
        assert expected['message_ids'] == ['automatic'] and expected['start_order'] == 2
    finally:
        await session.aclose()
    restored, cold = await reopen(session)
    try:
        assert await cold.latest_execution() == expected
    finally:
        await restored.aclose()


async def test_legacy_multiple_batches_are_unavailable_without_backfill_then_new_facts_work(tmp_path):
    session, _, runtime, host = await owner(tmp_path)
    await submit(runtime, 'old-one')
    await submit(runtime, 'old-two')
    path = runtime.controller.store.path
    await session.aclose()
    rows = json.loads(path.read_bytes())
    for row in rows:
        for field in ('start_order', 'started_at', 'settled_at'):
            del row[field]
    path.write_text(json.dumps(rows))
    before = path.read_bytes()
    restored, cold = await reopen(session)
    try:
        assert await cold.latest_execution() == {
            'availability': 'unavailable', 'reason': 'legacy_execution_metadata_missing'}
        assert path.read_bytes() == before
        runtime = SessionRuntime(restored, bound=True)
        runtime.start(setup_pending=True)
        await runtime.activate_inputs()
        fresh = await SessionCapabilityHost.open(restored)
        async def execute(batch):
            return TurnResult('succeeded', None)
        runtime.controller._execute_batch = execute
        outcome = await submit(runtime, 'new')
        fact = await fresh.latest_execution()
        assert fact['execution_id'] == outcome.batch_id and fact['message_ids'] == ['new']
        assert fact['settled_at'] >= fact['started_at']
        persisted = json.loads(path.read_bytes())
        assert all('settled_at' not in row and 'started_at' not in row for row in persisted[:2])
    finally:
        await restored.aclose()


@pytest.mark.parametrize('fail', [False, True])
async def test_inflight_read_retirement_never_hides_actual_storage_error(tmp_path, monkeypatch, fail):
    session, _ = create(tmp_path)
    host = await SessionCapabilityHost.open(session)
    entered, release = threading.Event(), threading.Event()
    from xdog.coding.core.sessions.store import InputStore
    original = InputStore.latest_execution
    def delayed(*args):
        entered.set()
        assert release.wait(3)
        if fail:
            raise OSError('real execution receipt read failure')
        return original(*args)
    monkeypatch.setattr(InputStore, 'latest_execution', delayed)
    pending = asyncio.create_task(host.latest_execution())
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        host.retire()
        release.set()
        with pytest.raises(OSError if fail else RuntimeError, match='real execution' if fail else 'closing'):
            await pending
        # A later query observes known retirement before starting any IO.
        assert await host.latest_execution() is None
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)
        await session.aclose()


async def test_failed_settlement_commit_does_not_publish_a_fake_terminal_fact(tmp_path, monkeypatch):
    session, _, runtime, host = await owner(tmp_path)
    original = runtime.controller.store._commit
    def failing(records):
        if any(row.status == 'succeeded' for row in records):
            raise OSError('actual terminal commit failure')
        return original(records)
    monkeypatch.setattr(runtime.controller.store, '_commit', failing)
    try:
        result = await submit(runtime)
        assert result.status == 'failed'
        fact = await host.latest_execution()
        assert fact['status'] == 'running' and 'settled_at' not in fact
        assert fact['started_at'] > 0
    finally:
        await session.aclose()


async def test_cold_controller_recovers_latest_of_multiple_batches_without_model_replay(tmp_path):
    session, _, runtime, host = await owner(tmp_path)
    await submit(runtime, 'completed-before-crash')
    entered, release = asyncio.Event(), asyncio.Event()
    async def execute(batch):
        entered.set()
        await release.wait()
        return TurnResult('succeeded', None)
    runtime.controller._execute_batch = execute
    ticket = runtime.controller.submit(SessionInput('crashed', 'human', 'user', 'inspect', (), {}), peer_id='test')
    await asyncio.wait_for(entered.wait(), 3)
    path = runtime.controller.store.path
    raw = path.read_bytes()
    before = await host.latest_execution()
    release.set()
    await asyncio.wrap_future(ticket.result)
    await session.aclose()
    path.write_bytes(raw)
    restored, _ = await reopen(session)
    recovered = SessionRuntime(restored, bound=True)
    recovered.start(setup_pending=True)
    try:
        assert recovered.controller._recovery_future is not None
        await asyncio.wrap_future(recovered.controller._recovery_future)
        cold = await SessionCapabilityHost.open(restored)
        fact = await cold.latest_execution()
        assert fact['status'] == 'interrupted' and fact['execution_id'] == before['execution_id']
        assert fact['start_order'] == 2 and fact['started_at'] == before['started_at']
        assert fact['settled_at'] >= fact['started_at']
        # A second cold read/reopen preserves the committed interruption time.
        assert await cold.latest_execution() == fact
    finally:
        await restored.aclose()
    second, cold = await reopen(session)
    try:
        assert await cold.latest_execution() == fact
    finally:
        await second.aclose()
