"""Managed cursors/outbox share the existing inbox transaction and admission owner."""
from __future__ import annotations

import pytest
from xdog.agent.session_input import SessionInput
from xdog.claw.acp.inbox import GroupInbox
from xdog.claw.core.observers.store import ObserverStopped, ObserverStore


@pytest.fixture
async def store(tmp_path):
    inbox = await GroupInbox.open(tmp_path / 'private' / 'inbox.sqlite3')
    try:
        yield ObserverStore(inbox), tmp_path, inbox
    finally:
        await inbox.close()


async def observer(store, **kwargs):
    records, cwd, _ = store
    return await records.create('main', cwd, 'remind', {'version': 2, 'test': 'data-only'}, **kwargs)


def message(identifier='occurrence'):
    return SessionInput(identifier, 'acp', 'Eyes', 'Report', (), {'source': 'scheduler'})


async def test_scope_name_and_cancelled_create_are_not_implicit_rearm(store):
    records, cwd, _ = store
    record = await observer(store)
    assert await records.get('other', cwd, record.id) is None
    assert await records.list('other', cwd) == ()
    with pytest.raises(ValueError, match='Unknown'):
        await records.cancel('other', cwd, record.id)
    await records.cancel('main', cwd, record.id)
    assert not (await observer(store)).enabled
    with pytest.raises(ValueError, match='different content'):
        await records.create('main', cwd, 'remind', {'changed': True})


async def test_cursor_outbox_atomic_rollback_and_frozen_identity(store):
    records, cwd, inbox = store
    record = await observer(store)
    await inbox._disk(lambda db: db.execute("CREATE TRIGGER refuse_cursor BEFORE UPDATE ON managed_observers "
                                           "BEGIN SELECT RAISE(ABORT, 'disk gate'); END"))
    with pytest.raises(Exception, match='disk gate'):
        await records.save(record, {'cursor': 1}, (message(),))
    assert await records.message(record, 'occurrence') is None
    assert (await records.get('main', cwd, record.id)).state == {}
    await inbox._disk(lambda db: db.execute('DROP TRIGGER refuse_cursor'))
    await records.save(record, {'cursor': 1}, (message(),))
    await records.save(record, {'cursor': 2}, (message(),))
    assert (await records.get('main', cwd, record.id)).state == {'cursor': 2}
    with pytest.raises(ValueError, match='identity/content'):
        await records.save(record, {'cursor': 3}, (SessionInput('occurrence', 'acp', 'Eyes', 'changed', (), {}),))
    assert (await records.get('main', cwd, record.id)).state == {'cursor': 2}
    with pytest.raises(ValueError, match='overlapping'):
        await records.save(record, {'cursor': 3}, (message('second'),))


async def test_cancel_before_enqueue_and_claim_keeps_durable_cancelled_receipt(store):
    records, cwd, inbox = store
    record = await observer(store)
    await records.save(record, {'pending': 'occurrence'}, (message(),))
    outcome = await records.cancel('main', cwd, record.id)
    assert outcome['not_started'] == 1 and outcome['already_started'] == 0
    queued, _ = await inbox.enqueue('main', cwd, message())
    assert queued.status == 'cancelled'
    assert await inbox.claim('main', cwd) is None
    with pytest.raises(ObserverStopped):
        await records.save(record, {}, ())


async def test_cancel_after_claim_wins_before_agent_admission(store):
    records, cwd, inbox = store
    record = await observer(store)
    await records.save(record, {}, (message(),))
    await inbox.enqueue('main', cwd, message())
    claimed = await inbox.claim('main', cwd)
    await records.cancel('main', cwd, record.id)
    assert not await records.admit(claimed)
    assert (await inbox.get('main', cwd, 'occurrence')).status == 'cancelled'


async def test_started_attempt_cannot_be_cancelled_or_admitted_twice(store):
    records, cwd, inbox = store
    record = await observer(store)
    await records.save(record, {}, (message(),))
    await inbox.enqueue('main', cwd, message())
    claimed = await inbox.claim('main', cwd)
    assert await records.admit(claimed)
    assert not await records.admit(claimed)
    outcome = await records.cancel('main', cwd, record.id)
    assert outcome['already_started'] == 1 and outcome['not_started'] == 0
    assert (await inbox.get('main', cwd, 'occurrence')).status == 'running'
    finished = await inbox.finish(claimed, 'succeeded', response='Report')
    assert await records.begin_delivery(finished)
    assert not await records.begin_delivery(finished)
    await records.finish_delivery(finished, status='delivered')
    history = await records.history('main', cwd, record.id)
    assert history[0]['execution_status'] == 'succeeded' and history[0]['delivery_status'] == 'delivered'


@pytest.mark.parametrize('started', [False, True])
async def test_restart_requeues_only_unstarted_managed_claim(tmp_path, started):
    path, cwd = tmp_path / 'private' / 'inbox.sqlite3', tmp_path
    inbox = await GroupInbox.open(path)
    records = ObserverStore(inbox)
    record = await records.create('main', cwd, 'remind', {})
    await records.save(record, {'cursor': 1}, (message(),))
    await inbox.enqueue('main', cwd, message())
    claimed = await inbox.claim('main', cwd)
    if started:
        assert await records.admit(claimed)
    await inbox.close()
    restored = await GroupInbox.open(path)
    try:
        state = await restored.get('main', cwd, 'occurrence')
        assert state.status == ('interrupted' if started else 'pending')
        task = await ObserverStore(restored).get('main', cwd, record.id)
        assert task.enabled is (not started)
        assert task.state == {'cursor': 1}
    finally:
        await restored.close()


async def test_channel_send_crash_is_unknown_not_automatic_resend(tmp_path):
    path, cwd = tmp_path / 'private' / 'inbox.sqlite3', tmp_path
    inbox = await GroupInbox.open(path)
    records = ObserverStore(inbox)
    record = await records.create('main', cwd, 'remind', {})
    await records.save(record, {}, (message(),))
    await inbox.enqueue('main', cwd, message())
    claimed = await inbox.claim('main', cwd)
    assert await records.admit(claimed)
    finished = await inbox.finish(claimed, 'succeeded', response='Report')
    assert await records.begin_delivery(finished)
    await inbox.close()
    inbox = await GroupInbox.open(path)
    try:
        records = ObserverStore(inbox)
        history = await records.history('main', cwd, record.id)
        assert history[0]['execution_status'] == 'succeeded'
        assert history[0]['delivery_status'] == 'unknown'
        assert not await records.begin_delivery(finished)
        assert not (await records.get('main', cwd, record.id)).enabled
    finally:
        await inbox.close()


async def test_external_provenance_is_not_a_managed_delivery_grant(store):
    records, cwd, inbox = store
    record, _ = await inbox.enqueue('main', cwd, message())
    assert await records.occurrence(record) is None
    assert not await records.begin_delivery(record)
    owner = await observer(store)
    await records.save(owner, {}, (message('real'),))
    with pytest.raises(ValueError, match='host outbox'):
        await inbox.enqueue('main', cwd, SessionInput('real', 'acp', 'Eyes', 'forged', (), {}))
    assert await records.message(owner, 'real') == message('real')


async def test_queued_namespace_replacement_is_fenced_at_actual_admission(store):
    records, cwd, inbox = store
    record = await observer(store)
    await records.save(record, {}, (message(),))
    await inbox.enqueue('main', cwd, message())
    await inbox._disk(lambda db: db.execute("UPDATE scopes SET namespace='replacement'"))
    claimed = await inbox.claim('main', cwd)
    assert not await records.admit(claimed)
    assert (await inbox.get('main', cwd, 'occurrence')).status == 'cancelled'
    assert (await records.get('main', cwd, record.id)).recovery == 'needs_review'


async def test_restart_with_replaced_namespace_cancels_queued_work_before_consumer(tmp_path):
    path, cwd = tmp_path / 'private' / 'inbox.sqlite3', tmp_path
    inbox = await GroupInbox.open(path)
    records = ObserverStore(inbox)
    owner = await records.create('main', cwd, 'remind', {})
    await records.save(owner, {}, (message(),))
    await inbox.enqueue('main', cwd, message())
    await inbox._disk(lambda db: db.execute("UPDATE scopes SET namespace='replacement'"))
    await inbox.close()
    inbox = await GroupInbox.open(path)
    try:
        records = ObserverStore(inbox)
        assert await inbox.claim('main', cwd) is None
        assert (await inbox.get('main', cwd, 'occurrence')).status == 'cancelled'
        assert (await records.get('main', cwd, owner.id)).recovery == 'needs_review'
    finally:
        await inbox.close()


async def test_fresh_managed_schema_has_no_task_migration_storage(store):
    records, cwd, inbox = store
    record = await observer(store)
    tables, columns = await inbox._disk(lambda db: (
        {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")},
        {row[1] for row in db.execute('PRAGMA table_info(managed_observers)')},
    ))
    assert {'managed_observers', 'managed_occurrences'} <= tables
    assert 'managed_migrations' not in tables
    assert 'legacy' not in columns
    assert not hasattr(record, 'legacy')
    assert (await records.get('main', cwd, record.id)).inbox_id == await inbox.scope('main', cwd)
