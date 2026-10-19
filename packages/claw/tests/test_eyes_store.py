"""Managed state has its own lease; explicit input control belongs to core."""
from __future__ import annotations

import pytest
from xdog.agent.session_input import SessionInput
from xdog.claw.core.inputs.models import InputScope
from xdog.claw.core.inputs.store import InputStore
from xdog.claw.core.observers.journal import ObserverJournal
from xdog.claw.core.observers.models import ObserverStopped
from xdog.claw.core.outputs.targets import ReplyReference
from xdog.claw.core.types import UserInput

from packages.claw.tests.core_inputs import external_envelope


def message(identifier='occurrence', instruction='Report'):
    return SessionInput(identifier, 'acp', 'Eyes', instruction, (), {'source': 'scheduler'})


@pytest.fixture
async def store(tmp_path):
    core = await InputStore.open(tmp_path / 'core' / 'inputs.sqlite3')
    await core.bind_scope(InputScope('main', tmp_path))
    journal = await ObserverJournal.open(tmp_path / 'observers' / 'state.sqlite3', core)
    try:
        yield journal, tmp_path, core
    finally:
        await journal.close()
        await core.close()


async def observer(store, **kwargs):
    journal, cwd, _ = store
    kwargs.setdefault('reply', ReplyReference(InputScope('main', cwd), 'store-test-sink',
                                               UserInput(group_id='main', sender='sink', channel='tui')))
    return await journal.create('main', cwd, 'remind', {'version': 2}, **kwargs)


async def test_scope_name_and_cancelled_create_are_not_implicit_rearm(store):
    journal, cwd, _core = store
    original = await observer(store)
    assert await journal.get('other', cwd, original.id) is None
    assert await journal.list('other', cwd) == ()
    with pytest.raises(ValueError, match='Unknown'):
        await journal.cancel('other', cwd, original.id)
    await journal.cancel('main', cwd, original.id)
    assert not (await observer(store)).enabled
    with pytest.raises(ValueError, match='different content'):
        await journal.create('main', cwd, 'remind', {'changed': True})


async def test_cursor_outbox_atomic_rollback_and_frozen_identity(store):
    journal, cwd, _core = store
    original = await observer(store)
    await journal._disk(lambda db: db.execute("CREATE TRIGGER refuse_cursor BEFORE UPDATE ON observers "
        "BEGIN SELECT RAISE(ABORT,'disk gate'); END"))
    with pytest.raises(Exception, match='disk gate'):
        await journal.save(original, {'cursor': 1}, (message(),))
    assert await journal.message(original, 'occurrence') is None
    assert (await journal.get('main', cwd, original.id)).state == {}
    await journal._disk(lambda db: db.execute('DROP TRIGGER refuse_cursor'))
    await journal.save(original, {'cursor': 1}, (message(),))
    await journal.save(original, {'cursor': 2}, (message(),))
    with pytest.raises(ValueError, match='identity/content'):
        await journal.save(original, {'cursor': 3}, (message(instruction='different'),))
    assert (await journal.get('main', cwd, original.id)).state == {'cursor': 2}
    with pytest.raises(ValueError, match='overlapping'):
        await journal.save(original, {}, (message('second'),))


async def test_cancel_before_enqueue_stops_source_not_arbitrary_external_intake(store):
    journal, cwd, core = store
    original = await observer(store)
    await journal.save(original, {}, (message(),))
    outcome = await journal.cancel('main', cwd, original.id)
    assert outcome['withdrawn_inputs'] == 0
    assert await core.get(InputScope('main', cwd), 'occurrence') is None
    with pytest.raises(ObserverStopped):
        await journal.save(original, {}, ())
    accepted, _ = await core.enqueue(external_envelope('main', cwd, message()))
    assert accepted.status == 'pending'


async def test_cancel_after_claim_requires_separate_explicit_core_withdrawal(store):
    journal, cwd, core = store
    original = await observer(store)
    await journal.save(original, {}, (message(),))
    await core.enqueue(external_envelope('main', cwd, message()))
    claimed = await core.claim(InputScope('main', cwd))
    await journal.cancel('main', cwd, original.id)
    assert (await core.get(InputScope('main', cwd), 'occurrence')).status == 'claimed'
    await core.cancel(InputScope('main', cwd), 'occurrence')
    with pytest.raises(ValueError, match='attempt'):
        await core.start(claimed)


async def test_started_attempt_cannot_be_cancelled_or_admitted_twice(store):
    journal, cwd, core = store
    original = await observer(store)
    await journal.save(original, {}, (message(),))
    await core.enqueue(external_envelope('main', cwd, message()))
    claimed = await core.claim(InputScope('main', cwd))
    running = await core.start(claimed)
    with pytest.raises(ValueError, match='attempt'):
        await core.start(claimed)
    await journal.cancel('main', cwd, original.id)
    with pytest.raises(ValueError, match='running'):
        await core.cancel(InputScope('main', cwd), 'occurrence')
    finished = await core.finish(running, 'succeeded', response='Report')
    await journal.sync(original)
    assert await journal.binding(finished.envelope) is not None
    await journal.mirror_delivery(finished, status='delivered')
    row, = await journal.history('main', cwd, original.id)
    assert row['execution_status'] == 'succeeded' and row['delivery_status'] == 'delivered'


@pytest.mark.parametrize('started', [False, True])
async def test_restart_requeues_only_unstarted_managed_claim(store, started):
    journal, cwd, core = store
    original = await observer(store)
    await journal.save(original, {'cursor': 1}, (message(),))
    await core.enqueue(external_envelope('main', cwd, message()))
    claimed = await core.claim(InputScope('main', cwd))
    if started:
        await core.start(claimed)
    await journal.close()
    await core.close()
    reopened = await InputStore.open(core.path)
    restored = await ObserverJournal.open(journal.path, reopened)
    try:
        assert (await reopened.get(InputScope('main', cwd), 'occurrence')).status == (
            'interrupted' if started else 'pending')
        await restored.sync(original)
        actual = await restored.get('main', cwd, original.id)
        assert actual.enabled is (not started) and actual.state == {'cursor': 1}
    finally:
        await restored.close()
        await reopened.close()


async def test_observer_reopen_preserves_unknown_core_delivery_projection_without_recovery_replay(store):
    journal, cwd, core = store
    original = await observer(store)
    await journal.save(original, {}, (message(),))
    await core.enqueue(external_envelope('main', cwd, message()))
    running = await core.start(await core.claim(InputScope('main', cwd)))
    finished = await core.finish(running, 'succeeded', response='Report')
    await journal.sync(original)
    await journal.mirror_delivery(finished, status='unknown', error='Core output send outcome unknown')
    await journal.close()
    restored = await ObserverJournal.open(journal.path, core)
    try:
        row, = await restored.history('main', cwd, original.id)
        assert row['execution_status'] == 'succeeded' and row['delivery_status'] == 'unknown'
        assert await restored.pending_reports() == ()
        assert (await core.get(InputScope('main', cwd), 'occurrence')).response == 'Report'
    finally:
        await restored.close()


async def test_external_provenance_is_not_a_managed_delivery_grant(store):
    journal, cwd, core = store
    accepted, _ = await core.enqueue(external_envelope('main', cwd, message()))
    assert await journal.occurrence(accepted) is None
    assert await journal.binding(accepted.envelope) is None
    original = await observer(store)
    await journal.save(original, {}, (message('real'),))
    await core.enqueue(external_envelope('main', cwd, message('real', 'forged')))
    with pytest.raises(ValueError, match='identity'):
        await journal.sync(original)
    assert await journal.message(original, 'real') == message('real')


async def test_namespace_replacement_halts_source_not_already_accepted_input(store):
    journal, cwd, core = store
    original = await observer(store)
    await journal.save(original, {}, (message(),))
    await core.enqueue(external_envelope('main', cwd, message()))
    await core._disk(lambda db: db.execute('UPDATE scopes SET namespace=?', ('f' * 32,)))
    await journal.sync(original)
    assert (await journal.get('main', cwd, original.id)).recovery == 'needs_review'
    assert (await core.get(InputScope('main', cwd), 'occurrence')).status == 'pending'


async def test_restart_with_replaced_namespace_never_cancels_core_by_observer_policy(store):
    journal, cwd, core = store
    original = await observer(store)
    await journal.save(original, {}, (message(),))
    await core.enqueue(external_envelope('main', cwd, message()))
    await core._disk(lambda db: db.execute('UPDATE scopes SET namespace=?', ('f' * 32,)))
    await journal.close()
    restored = await ObserverJournal.open(journal.path, core)
    try:
        await restored.sync(original)
        assert (await core.get(InputScope('main', cwd), 'occurrence')).status == 'pending'
        assert (await restored.get('main', cwd, original.id)).recovery == 'needs_review'
    finally:
        await restored.close()


async def test_fresh_observer_schema_contains_no_input_or_migration_storage(store):
    journal, cwd, core = store
    original = await observer(store)
    tables, columns = await journal._disk(lambda db: (
        {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")},
        {r[1] for r in db.execute('PRAGMA table_info(observers)')},
    ))
    assert {'observers', 'outbox'} <= tables and not tables & {'inputs', 'attempts', 'managed_migrations'}
    assert 'legacy' not in columns and not hasattr(original, 'legacy')
    assert original.inbox_id == await core.namespace(InputScope('main', cwd))
