"""An observer owns its journal, never the application's receipt transaction."""
from __future__ import annotations

import sqlite3
from contextlib import asynccontextmanager

import pytest
from xdog.agent.session_input import SessionInput
from xdog.claw.core.inputs.models import InputEnvelope, InputScope
from xdog.claw.core.inputs.store import InputStore
from xdog.claw.core.outputs.targets import ReplyReference
from xdog.claw.core.types import UserInput


def canonical(identifier='event'):
    return SessionInput(identifier, 'acp', 'Eyes', 'Inspect deployment and report', (), {'delivery_id': identifier})


@asynccontextmanager
async def open_journals(tmp_path):
    from xdog.claw.core.observers.journal import ObserverJournal
    core = await InputStore.open(tmp_path / 'core' / 'inputs.sqlite3')
    scope = InputScope('main', tmp_path)
    await core.bind_scope(scope)
    observers = await ObserverJournal.open(tmp_path / 'observers' / 'state.sqlite3', core)
    try:
        yield core, observers, scope
    finally:
        await observers.close()
        await core.close()


async def record(journals, **kwargs):
    _core, observers, scope = journals
    kwargs.setdefault('reply', ReplyReference(scope, 'journal-test-recipient',
        UserInput(group_id=scope.group_id, sender='sink', channel='tui')))
    return await observers.create(scope.group_id, scope.workspace, 'review', {'version': 2}, **kwargs)


async def test_observer_journal_has_independent_lease_schema_and_namespace(tmp_path):
    async with open_journals(tmp_path) as journals:
        core, observers, scope = journals
        created = await record(journals)
        assert created.inbox_id == await core.namespace(scope)
        assert (await observers.get('main', scope.workspace, created.id)).definition == {'version': 2}
        with sqlite3.connect(core.path) as db:
            names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert not names & {'observers', 'outbox', 'managed_observers', 'managed_occurrences'}
        from xdog.claw.core.observers.journal import ObserverJournal
        with pytest.raises(BlockingIOError):
            await ObserverJournal.open(observers.path, core)
        await observers.close()
        assert (await core.enqueue(InputEnvelope(scope, canonical())))[0].status == 'pending'


async def test_cursor_and_whole_immutable_outbox_rollback_together(tmp_path):
    async with open_journals(tmp_path) as journals:
        _core, observers, scope = journals
        created = await record(journals)
        await observers._disk(lambda db: db.execute("CREATE TRIGGER refuse_cursor BEFORE UPDATE ON observers "
            "BEGIN SELECT RAISE(ABORT,'cursor failure'); END"))
        with pytest.raises(sqlite3.IntegrityError, match='cursor failure'):
            await observers.save(created, {'cursor': 1}, (canonical(),))
        assert await observers.message(created, 'event') is None
        assert (await observers.get('main', scope.workspace, created.id)).state == {}
        await observers._disk(lambda db: db.execute('DROP TRIGGER refuse_cursor'))
        await observers.save(created, {'cursor': 1}, (canonical(),))
        await observers.save(created, {'cursor': 2}, (canonical(),))
        assert (await observers.get('main', scope.workspace, created.id)).state == {'cursor': 2}
        different = SessionInput('event', 'acp', 'Eyes', 'different', (), {})
        with pytest.raises(ValueError, match='identity|content'):
            await observers.save(created, {'cursor': 3}, (different,))
        assert (await observers.message(created, 'event')).instruction == 'Inspect deployment and report'


@pytest.mark.parametrize('started', [False, True])
async def test_stop_collection_never_withdraws_an_already_accepted_core_input(tmp_path, started):
    async with open_journals(tmp_path) as journals:
        core, observers, scope = journals
        created = await record(journals)
        await observers.save(created, {'pending': 'event'}, (canonical(),))
        await core.enqueue(InputEnvelope(scope, canonical()))
        if started:
            await core.start(await core.claim(scope))
        await observers.cancel('main', scope.workspace, created.id)
        actual = await core.get(scope, 'event')
        assert actual.status == ('running' if started else 'pending')
        assert not (await observers.get('main', scope.workspace, created.id)).enabled
        if not started:
            assert (await core.cancel(scope, 'event')).status == 'cancelled'


async def test_cancel_before_intake_stops_unsent_outbox_without_blacklisting_an_external_id(tmp_path):
    async with open_journals(tmp_path) as journals:
        core, observers, scope = journals
        created = await record(journals)
        await observers.save(created, {'pending': 'event'}, (canonical(),))
        await observers.cancel('main', scope.workspace, created.id)
        assert await core.get(scope, 'event') is None
        history = await observers.history('main', scope.workspace, created.id)
        assert history[0]['cancelled'] and history[0]['delivery_status'] == 'not-required'
        # A later explicit external submission is not subject to hidden observer policy in core.
        assert (await core.enqueue(InputEnvelope(scope, canonical())))[0].status == 'pending'


async def test_notifications_are_separate_from_collection_and_execution(tmp_path):
    async with open_journals(tmp_path) as journals:
        core, observers, scope = journals
        created = await record(journals)
        await observers.save(created, {'pending': 'event'}, (canonical(),))
        await core.enqueue(InputEnvelope(scope, canonical()))
        running = await core.start(await core.claim(scope), session_id='context')
        await observers.cancel('main', scope.workspace, created.id)
        finished = await core.finish(running, 'succeeded', response='original report')
        await observers.sync(created)
        assert await observers.begin_delivery(finished) is True
        await observers.finish_delivery(finished, status='delivered')
        history = await observers.history('main', scope.workspace, created.id)
        assert history[0]['execution_status'] == 'succeeded' and history[0]['delivery_status'] == 'delivered'
        assert history[0]['attempt_id'] == running.attempt_id


async def test_explicit_notification_cancel_does_not_stop_source_or_core_work(tmp_path):
    async with open_journals(tmp_path) as journals:
        core, observers, scope = journals
        created = await record(journals)
        await observers.save(created, {'pending': 'event'}, (canonical(),))
        await core.enqueue(InputEnvelope(scope, canonical()))
        running = await core.start(await core.claim(scope))
        await observers.cancel_notifications('main', scope.workspace, created.id)
        assert (await observers.get('main', scope.workspace, created.id)).enabled
        finished = await core.finish(running, 'succeeded', response='keep in history')
        await observers.sync(created)
        assert await observers.begin_delivery(finished) is False
        assert (await core.get(scope, 'event')).response == 'keep in history'


async def test_reopen_recovers_known_not_sent_report_but_not_unknown_send(tmp_path):
    async with open_journals(tmp_path) as journals:
        from xdog.claw.core.observers.journal import ObserverJournal
        core, observers, scope = journals
        created = await record(journals)
        await observers.save(created, {}, (canonical(),))
        await core.enqueue(InputEnvelope(scope, canonical()))
        running = await core.start(await core.claim(scope))
        finished = await core.finish(running, 'succeeded', response='done')
        await observers.sync(created)
        path = observers.path
        await observers.close()
        restored = await ObserverJournal.open(path, core)
        try:
            assert [r.envelope.message.id for r in await restored.pending_reports()] == ['event']
            assert await restored.begin_delivery(finished)
        finally:
            await restored.close()
        restored = await ObserverJournal.open(path, core)
        try:
            assert await restored.pending_reports() == ()
            status = await restored.get('main', scope.workspace, created.id)
            assert not status.enabled and status.recovery == 'needs_review'
            assert (await core.get(scope, 'event')).status == 'succeeded'
        finally:
            await restored.close()


async def test_retry_attempt_cannot_rebind_an_old_occurrence_or_resend_its_report(tmp_path):
    async with open_journals(tmp_path) as journals:
        core, observers, scope = journals
        created = await record(journals)
        await observers.save(created, {}, (canonical(),))
        await core.enqueue(InputEnvelope(scope, canonical()))
        first = await core.start(await core.claim(scope))
        failed = await core.finish(first, 'failed', error='inspect effects')
        await observers.sync(created)
        assert await observers.begin_delivery(failed)
        await observers.finish_delivery(failed, status='delivered')
        await core.retry(scope, 'event')
        second = await core.start(await core.claim(scope))
        await core.finish(second, 'succeeded', response='explicit second attempt')
        await observers.sync(created)
        history = await observers.history('main', scope.workspace, created.id)
        assert history[0]['attempt_id'] == first.attempt_id
        assert history[0]['execution_status'] == 'failed'
        assert await observers.pending_reports() == ()


async def test_namespace_change_halts_observer_without_cancelling_core_input(tmp_path):
    async with open_journals(tmp_path) as journals:
        core, observers, scope = journals
        created = await record(journals)
        await observers.save(created, {}, (canonical(),))
        await core.enqueue(InputEnvelope(scope, canonical()))
        await core._disk(lambda db: db.execute("UPDATE scopes SET namespace=?", ('f' * 32,)))
        await observers.sync(created)
        actual = await observers.get('main', scope.workspace, created.id)
        assert not actual.enabled and actual.recovery == 'needs_review'
        assert (await core.get(scope, 'event')).status == 'pending'


async def test_scope_and_outbox_binding_are_not_claimed_by_payload_provenance(tmp_path):
    async with open_journals(tmp_path) as journals:
        core, observers, scope = journals
        created = await record(journals, owner='owned-feature', notify_only=True)
        fake = SessionInput('forged', 'acp', 'Eyes', 'pretend owner', (),
                            {'owner': 'owned-feature', 'notify_only': True})
        accepted, _ = await core.enqueue(InputEnvelope(scope, fake))
        assert await observers.occurrence(accepted) is None
        assert await observers.get('other', scope.workspace, created.id) is None


async def test_unfinished_outbox_cannot_be_dropped_or_overlapped(tmp_path):
    async with open_journals(tmp_path) as journals:
        _core, observers, _scope = journals
        created = await record(journals)
        await observers.save(created, {'pending': 'event'}, (canonical(),))
        with pytest.raises(ValueError, match='unfinished|overlapping'):
            await observers.save(created, {'pending': None})
        with pytest.raises(ValueError, match='unfinished|overlapping'):
            await observers.save(created, {'pending': 'second'}, (canonical('second'),))


async def test_source_cancel_during_unknown_enqueue_cannot_drop_report_for_late_core_ack(tmp_path):
    async with open_journals(tmp_path) as journals:
        core, observers, scope = journals
        created = await record(journals)
        await observers.save(created, {'pending': 'event'}, (canonical(),))
        assert await observers.transport_started(created, 'event')
        await observers.cancel('main', scope.workspace, created.id)
        await core.enqueue(InputEnvelope(scope, canonical()))
        await observers.transport_finished(created, 'event', status='queued')
        running = await core.start(await core.claim(scope))
        finished = await core.finish(running, 'succeeded', response='late accepted report')
        await observers.sync(created)
        history = await observers.history('main', scope.workspace, created.id)
        assert not history[0]['cancelled'] and history[0]['transport_status'] == 'accepted'
        assert await observers.begin_delivery(finished)
        await observers.finish_delivery(finished, status='delivered')
        assert (await core.get(scope, 'event')).status == 'succeeded'


@pytest.mark.parametrize('column,value', [('group_id', 'other'), ('revision', 99)])
async def test_invalid_outbox_binding_is_refused_before_any_recovery_write(tmp_path, column, value):
    from xdog.claw.core.observers.journal import ObserverJournal
    async with open_journals(tmp_path) as journals:
        core, observers, scope = journals
        created = await record(journals)
        await observers.save(created, {}, (canonical(),))
        path = observers.path
        await observers.close()
        with sqlite3.connect(path) as db:
            db.execute(f'UPDATE outbox SET {column}=?', (value,))
        original = path.read_bytes()
        with pytest.raises(ValueError):
            await ObserverJournal.open(path, core)
        assert path.read_bytes() == original


async def test_late_error_cannot_reenable_or_relabel_explicitly_cancelled_observer(tmp_path):
    async with open_journals(tmp_path) as journals:
        _core, observers, scope = journals
        created = await record(journals)
        await observers.cancel('main', scope.workspace, created.id)
        await observers.fail(created, 'old transport failed after cancel')
        actual = await observers.get('main', scope.workspace, created.id)
        assert not actual.enabled and actual.recovery == 'cancelled'
        await observers.fail(created, 'old transport unknown', review=True)
        actual = await observers.get('main', scope.workspace, created.id)
        assert actual.recovery == 'cancelled'


async def test_report_recovery_uses_original_attempt_not_a_later_explicit_retry(tmp_path):
    async with open_journals(tmp_path) as journals:
        core, observers, scope = journals
        created = await record(journals)
        await observers.save(created, {}, (canonical(),))
        await core.enqueue(InputEnvelope(scope, canonical()))
        first = await core.start(await core.claim(scope))
        failed = await core.finish(first, 'failed', error='first attempt failed')
        await observers.sync(created)
        await core.retry(scope, 'event')
        second = await core.start(await core.claim(scope))
        succeeded = await core.finish(second, 'succeeded', response='second attempt report')
        await observers.sync(created)
        assert not await observers.begin_delivery(succeeded), 'A new attempt stole an old report binding'
        reports = await observers.pending_reports()
        assert len(reports) == 1 and reports[0].attempt_id == first.attempt_id
        assert reports[0].error == 'first attempt failed' and not reports[0].response
        assert await observers.begin_delivery(failed)
        await observers.finish_delivery(succeeded, status='delivered')
        assert (await observers.history('main', scope.workspace, created.id))[0]['delivery_status'] == 'sending'
        await observers.finish_delivery(failed, status='delivered')
        assert await observers.pending_reports() == ()
