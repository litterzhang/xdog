"""Original-group started receipts survive restart, retries and context rotation."""
from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from types import SimpleNamespace

import pytest
from xdog.agent.session_input import SessionInput
from xdog.claw.core.inputs.consumer import InputOutcome, InputService
from xdog.claw.core.inputs.models import InputEnvelope, InputScope
from xdog.claw.core.inputs.store import InputStore
from xdog.claw.core.runtime.group_capability_ports import GroupCapabilityHost


async def owner(tmp_path, *, identity='main', store=None):
    store = store or await InputStore.open(tmp_path / 'inputs' / 'journal.sqlite3')
    scope = InputScope(identity, tmp_path / identity)
    await store.bind_scope(scope)
    async def forbidden(*args):
        pytest.fail('a read-only recovery query must not execute an input')
    service = InputService(store, forbidden)
    runtime = SimpleNamespace(group=SimpleNamespace(id=identity), workspace_dir=scope.workspace,
                              data_dir=tmp_path, _closing=False, _active_session=None,
                              prepare_session=forbidden)
    return store, service, runtime, await GroupCapabilityHost.open(runtime, service)


async def enqueue(store, scope, identifier):
    record, _ = await store.enqueue(InputEnvelope(scope, SessionInput(identifier, 'human', 'user', 'inspect', (), {})))
    return record


async def started(store, scope, identifier, status='succeeded'):
    await enqueue(store, scope, identifier)
    record = await store.claim(scope)
    record = await store.start(record, session_id='original')
    if status != 'running':
        record = await store.finish(record, status)
    return record


@pytest.mark.parametrize('status', ['succeeded', 'failed', 'running'])
async def test_cold_started_receipts_include_failure_interruption_and_do_not_prepare_agent(tmp_path, status):
    store, service, runtime, host = await owner(tmp_path)
    assert await host.latest_execution() is None
    record = await started(store, host._scope, 'original', status)
    before = await host.latest_execution()
    assert before['execution_id'] == record.attempt_id and before['status'] == status
    assert before['message_ids'] == ['original'] and 0 < before['started_at'] <= time.time()
    if status != 'running':
        assert before['settled_at'] >= before['started_at']
    else:
        assert 'settled_at' not in before
    await service.close()
    reopened, service, runtime, cold = await owner(tmp_path)
    try:
        expected = await cold.latest_execution()
        assert expected['execution_id'] == before['execution_id']
        assert expected['status'] == ('interrupted' if status == 'running' else status)
        assert expected['started_at'] == before['started_at']
        assert expected['settled_at'] >= expected['started_at']
        if status != 'running':
            assert expected == before
        result = await cold.latest_execution()
        result['message_ids'].append('forged')
        assert await cold.latest_execution() == expected
        assert runtime._active_session is None
        assert (await reopened.get(cold._scope, 'original')).status == expected['status']
    finally:
        await service.close()


async def test_start_order_uses_attempts_not_input_order_and_retains_retry_history(tmp_path):
    store, service, _, host = await owner(tmp_path)
    scope = host._scope
    try:
        first = await started(store, scope, 'first', 'failed')
        second = await started(store, scope, 'second')
        await store.retry(scope, 'first')
        # Explicitly retried pending input retains the earlier started fact.
        assert (await host.latest_execution())['execution_id'] == second.attempt_id
        retry = await store.claim(scope)
        await store.reject(retry, 'preparation failed')
        assert (await host.latest_execution())['execution_id'] == second.attempt_id
        await store.retry(scope, 'first')
        retry = await store.start(await store.claim(scope), session_id='rotated-context')
        await store.finish(retry, 'failed')
        fact = await host.latest_execution()
        assert fact['execution_id'] == retry.attempt_id and fact['status'] == 'failed'
        assert fact['message_ids'] == ['first'] and fact['settled_at'] >= fact['started_at']
        assert retry.attempt_id not in {first.attempt_id, second.attempt_id}
        await store.retry(scope, 'first')
        assert (await host.latest_execution())['execution_id'] == retry.attempt_id
        # A later claim is not started and cannot hide the last failed execution.
        await store.claim(scope)
        assert (await host.latest_execution())['execution_id'] == retry.attempt_id
    finally:
        await service.close()


async def test_no_started_fact_for_pending_claimed_cancelled_or_preparation_failure(tmp_path):
    store, service, _, host = await owner(tmp_path)
    try:
        await enqueue(store, host._scope, 'unstarted')
        assert await host.latest_execution() is None
        claim = await store.claim(host._scope)
        assert await host.latest_execution() is None
        await store.reject(claim, 'preparation failed')
        assert await host.latest_execution() is None
        await enqueue(store, host._scope, 'withdrawn')
        await store.cancel(host._scope, 'withdrawn')
        assert await host.latest_execution() is None
    finally:
        await service.close()


async def test_settled_event_identity_matches_cold_query(tmp_path):
    store = await InputStore.open(tmp_path / 'inputs' / 'journal.sqlite3')
    scope = InputScope('main', tmp_path / 'main')
    await store.bind_scope(scope)
    async def execute(record, admit):
        assert await admit('actual-session')
        return InputOutcome(response='done')
    service = InputService(store, execute)
    runtime = SimpleNamespace(group=SimpleNamespace(id='main'), workspace_dir=scope.workspace,
                              data_dir=tmp_path, _closing=False)
    host = await GroupCapabilityHost.open(runtime, service)
    events = []
    service.subscribe_events(scope, events.append)
    try:
        await service.submit(InputEnvelope(scope, SessionInput('real', 'human', 'user', 'inspect', (), {})))
        await service.wait(scope, 'real')
        await asyncio.sleep(0)
        settled, = [event for event in events if event['type'] == 'execution.settled']
        result = await host.latest_execution()
        assert result['execution_id'] == settled['execution_id']
        assert result['status'] == settled['status'] == 'succeeded'
    finally:
        await service.close()


async def test_group_workspace_namespace_storage_and_close_fences(tmp_path):
    store, service, runtime, host = await owner(tmp_path)
    try:
        original = await started(store, host._scope, 'mine')
        other = InputScope('other', host.workspace)
        await store.bind_scope(other)
        await started(store, other, 'not-mine')
        elsewhere = InputScope(host.identity, tmp_path / 'elsewhere')
        await store.bind_scope(elsewhere)
        await started(store, elsewhere, 'not-my-workspace')
        assert (await host.latest_execution())['execution_id'] == original.attempt_id
        host.namespace = 'a' * 32
        with pytest.raises(ValueError, match='namespace'):
            await host.latest_execution()
        host.namespace = await store.namespace(host._scope)
        runtime.group.id = 'other'
        with pytest.raises(ValueError, match='owner'):
            await host.latest_execution()
        runtime.group.id = host.identity
        # A real SQL/storage error must propagate, not become an empty snapshot.
        await store._disk(lambda db: db.execute('ALTER TABLE attempts RENAME TO broken'))
        with pytest.raises(sqlite3.DatabaseError):
            await host.latest_execution()
        await store._disk(lambda db: db.execute('ALTER TABLE broken RENAME TO attempts'))
        host.retire()
        assert await host.latest_execution() is None
    finally:
        await service.close()


@pytest.mark.parametrize('cancel', [False, True])
async def test_offloop_work_is_owned_and_rechecks_owner_after_read(tmp_path, monkeypatch, cancel):
    store, service, runtime, host = await owner(tmp_path)
    entered, release = threading.Event(), threading.Event()
    original = store._transaction
    owner_thread = threading.get_ident()
    def delayed(operation):
        assert threading.get_ident() != owner_thread
        entered.set()
        assert release.wait(3)
        return original(operation)
    monkeypatch.setattr(store, '_transaction', delayed)
    pending = asyncio.create_task(host.latest_execution())
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        if cancel:
            pending.cancel()
        else:
            runtime.workspace_dir = tmp_path / 'changed'
        await asyncio.sleep(.01)
        assert not pending.done()
        release.set()
        with pytest.raises(asyncio.CancelledError if cancel else ValueError):
            await pending
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)
        await service.close()


async def test_existing_v3_without_time_columns_keeps_null_history_and_new_attempts_work(tmp_path):
    store, service, _, host = await owner(tmp_path)
    old = await started(store, host._scope, 'legacy')
    path = store.path
    await service.close()
    # Exact pre-extension v3 schema: no historical timestamps are available.
    with sqlite3.connect(path) as db:
        db.execute('ALTER TABLE attempts DROP COLUMN started_at')
        db.execute('ALTER TABLE attempts DROP COLUMN settled_at')
    store, service, _, host = await owner(tmp_path)
    try:
        assert await host.latest_execution() == {
            'availability': 'unavailable', 'reason': 'legacy_execution_metadata_missing'}
        attempts = await store.attempts(host._scope, 'legacy')
        assert attempts[0].attempt_id == old.attempt_id
        assert attempts[0].started_at is None and attempts[0].settled_at is None
        fresh = await started(store, host._scope, 'new', 'failed')
        fact = await host.latest_execution()
        assert fact['execution_id'] == fresh.attempt_id and fact['status'] == 'failed'
        assert fact['settled_at'] >= fact['started_at'] > 0
        old_again = (await store.attempts(host._scope, 'legacy'))[0]
        assert old_again.started_at is None and old_again.settled_at is None
    finally:
        await service.close()


@pytest.mark.parametrize('fail', [False, True])
async def test_inflight_retirement_does_not_suppress_sql_work_error(tmp_path, monkeypatch, fail):
    store, service, runtime, host = await owner(tmp_path)
    entered, release = threading.Event(), threading.Event()
    original = store._transaction
    def delayed(operation):
        entered.set()
        assert release.wait(3)
        if fail:
            raise sqlite3.OperationalError('real execution receipt storage failure')
        return original(operation)
    monkeypatch.setattr(store, '_transaction', delayed)
    pending = asyncio.create_task(host.latest_execution())
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        runtime._closing = True
        release.set()
        with pytest.raises(sqlite3.OperationalError if fail else RuntimeError,
                           match='real execution' if fail else 'closing'):
            await pending
        assert await host.latest_execution() is None
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)
        await service.close()


async def test_corrupt_execution_times_are_errors_not_legacy_unavailable(tmp_path):
    store, service, _, host = await owner(tmp_path)
    await started(store, host._scope, 'one')
    try:
        await store._disk(lambda db: db.execute("UPDATE attempts SET settled_at='not-a-time'"))
        with pytest.raises(ValueError, match='timestamps'):
            await host.latest_execution()
    finally:
        await service.close()


async def test_failed_settlement_transaction_keeps_running_fact_without_settlement_time(tmp_path, monkeypatch):
    store, service, _, host = await owner(tmp_path)
    record = await started(store, host._scope, 'original', 'running')
    original = store._transaction
    def fail_commit(operation):
        def fail(db):
            operation(db)
            raise OSError('actual execution settlement transaction failed')
        return original(fail)
    monkeypatch.setattr(store, '_transaction', fail_commit)
    try:
        with pytest.raises(OSError, match='settlement transaction'):
            await store.finish(record, 'succeeded')
        monkeypatch.setattr(store, '_transaction', original)
        fact = await host.latest_execution()
        assert fact['status'] == 'running' and 'settled_at' not in fact
    finally:
        monkeypatch.setattr(store, '_transaction', original)
        await service.close()
