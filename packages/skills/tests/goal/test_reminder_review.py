"""Bounded race regressions for reminder coalescing, without runtime implementation edits."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from xdog.skills.goal.automation import _EnqueueHandler
from xdog.skills.goal.host import OperationContext
from xdog.skills.goal.models import Task
from xdog.skills.goal.runtime import GoalRuntime

from packages.skills.tests.goal.test_runtime import close, setup

USER = OperationContext(True)


async def receipt_states(monkeypatch):
    import xdog.agent.acp.client as client
    states = {}

    async def status(target, identifier=None):
        return {'messages': [{'message_id': identifier, 'status': states.get(identifier, 'succeeded')}]}

    monkeypatch.setattr(client, 'inbox_status', status)
    return states


@pytest.mark.parametrize('fence', ['pause', 'owner_retired'])
async def test_no_new_acp_send_after_local_claim_loses_its_execution_fence(tmp_path, monkeypatch, fence):
    """The destination has not seen this message when the fence is withdrawn."""
    runtime, parent, host, state, sent, authorized, listeners = await setup(tmp_path, monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()
    original = runtime.service.store.claim_reminder
    original_valid = host.valid
    pending = None

    async def claim(identifier, goal):
        result = await original(identifier, goal)
        if result:
            entered.set()
            await release.wait()  # DB claim completed, but enqueue has not been called.
        return result

    monkeypatch.setattr(runtime.service.store, 'claim_reminder', claim)
    try:
        await runtime.service.create('One', 'No post-fence send', context=USER)
        await runtime.flush()
        pending = asyncio.create_task(runtime._wake('execution:original'))
        await asyncio.wait_for(entered.wait(), 2)
        assert not sent
        if fence == 'pause':
            await parent.extensions.command('goal', 'pause Stop before destination intake')
            assert (await runtime.service.current()).status == 'paused'
            assert runtime._observer is None
        else:
            host.valid = lambda: False
        release.set()
        await asyncio.wait_for(pending, 2)
        assert not sent, 'A locally claimed but never-enqueued reminder crossed its withdrawn fence'
    finally:
        release.set()
        if pending is not None:
            await asyncio.gather(pending, return_exceptions=True)
        host.valid = original_valid
        await close(runtime, parent)


async def test_resume_epoch_keeps_old_accepted_pending_receipt_as_backpressure(tmp_path, monkeypatch):
    runtime, parent, host, state, sent, authorized, listeners = await setup(tmp_path, monkeypatch)
    facts = await receipt_states(monkeypatch)
    try:
        goal = await runtime.service.create('One', 'Only one outstanding main-owner reminder', context=USER)
        await runtime.flush()
        await runtime._wake('execution:first')
        first = sent[0]
        facts[first.id] = 'pending'
        await runtime._task_changed(goal.id, SimpleNamespace(id='later-change'))
        await runtime.flush()
        assert len(sent) == 1
        await parent.extensions.command('goal', 'pause Explicit stop')
        await parent.extensions.command('goal', 'resume')
        await runtime.flush()
        # Pause does not withdraw accepted input. A new epoch must not pretend
        # that the original message disappeared while it is still executable.
        assert len(sent) == 1, 'Resume hid an accepted/pending original and admitted a second reminder'
        facts[first.id] = 'succeeded'
        listeners[0]({'type': 'owner.available'})
        await runtime.flush()
        assert len(sent) == 2 and sent[1].provenance['wake_through'] > first.provenance['wake_through']
    finally:
        await close(runtime, parent)


async def test_task_delivery_is_not_acknowledged_when_revision_race_drops_its_cause(tmp_path, monkeypatch):
    runtime, parent, host, state, sent, authorized, listeners = await setup(tmp_path, monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()
    original = runtime.service.store.signal
    pending = None

    async def signal(goal, trigger):
        if trigger == 'tasks:changed-during-plan':
            entered.set()
            await release.wait()
        return await original(goal, trigger)

    monkeypatch.setattr(runtime.service.store, 'signal', signal)
    try:
        goal = await runtime.service.create('One', 'Do not lose detector delivery', context=USER)
        await runtime.flush()
        before = await runtime.service.store.wake_state(goal.id)
        runtime.service.files(goal).write(Task('worker', 'Actual external result', status='completed'),
                                         expected_version=0)
        state['state'] = 'running'  # Busy coalescing must persist the cause, not enqueue immediately.
        handler = _EnqueueHandler({}, 'unused', lambda delivery: runtime._task_changed(goal.id, delivery))
        pending = asyncio.create_task(handler.deliver(SimpleNamespace(id='changed-during-plan')))
        await asyncio.wait_for(entered.wait(), 2)
        await runtime.service.plan('A concurrent legitimate main-Agent plan revision')
        release.set()
        result = await asyncio.wait_for(pending, 2)
        await runtime.flush()
        after = await runtime.service.store.wake_state(goal.id)
        assert result.status == 'queued'
        assert after['sequence'] > before['sequence'], (
            'Eyes returned queued/coalesced but a current/remindable Goal revision race discarded the cause')
    finally:
        release.set()
        if pending is not None:
            await asyncio.gather(pending, return_exceptions=True)
        await close(runtime, parent)


async def test_fast_worker_change_in_initial_baseline_is_reconciled(tmp_path, monkeypatch):
    import xdog.skills.goal.automation as automation
    from xdog.skills.goal.files import TaskFiles
    runtime, parent, host, state, sent, authorized, listeners = await setup(tmp_path, monkeypatch)
    await receipt_states(monkeypatch)

    class FastBaseline:
        async def start(self, target, directory, config, goal_id, on_change):
            # No detector event: simulate a worker finishing inside initial baseline.
            TaskFiles(tmp_path, goal_id).write(Task('fast', 'Finished before watching', status='completed'),
                                             expected_version=0)
            return {'status': 'watching'}

        async def aclose(self):
            pass

    monkeypatch.setattr(automation, 'TaskObserver', FastBaseline)
    try:
        goal = await runtime.service.create('One', 'Reconcile initial baseline', context=USER)
        await asyncio.wait_for(runtime.flush(), 2)
        wake = await runtime.service.store.wake_state(goal.id)
        assert wake['manifest'] and len(sent) == 1
        assert any(cause.startswith('task-snapshot:') for cause in sent[0].provenance['triggers'])
    finally:
        await close(runtime, parent)


async def test_unknown_reopen_requires_explicit_epoch_and_never_reuses_unknown_id(tmp_path, monkeypatch):
    import xdog.agent.acp.client as client
    runtime, parent, host, state, sent, authorized, listeners = await setup(tmp_path, monkeypatch)
    attempts = []

    async def enqueue(target, message):
        attempts.append(message)
        return {'status': 'outcome-unknown' if len(attempts) == 1 else 'queued'}

    monkeypatch.setattr(client, 'enqueue', enqueue)
    try:
        goal = await runtime.service.create('One', 'Explicit recovery only', context=USER)
        await runtime.flush()
        await runtime._wake('execution:unknown')
        original_id = attempts[0].id
        assert (await runtime.service.current()).wait_for == 'host'
        await runtime.aclose()
        runtime = await GoalRuntime.open(host)
        runtime.attach(parent)
        for _ in range(3):
            await runtime._available()
        assert len(attempts) == 1
        waiting = await runtime.service.current()
        await runtime.service.change('activate', waiting.id, version=waiting.version,
                                     reason='User inspected original unknown outcome', context=USER)
        await runtime._available()
        await asyncio.wait_for(runtime.flush(), 2)
        assert len(attempts) == 2 and attempts[1].id != original_id
        old = next(row for row in await runtime.service.store.reminders() if row['id'] == original_id)
        assert old['status'] == 'unknown' and (await runtime.service.current()).id == goal.id
    finally:
        await close(runtime, parent)


async def test_repeated_failure_signals_do_not_create_an_automatic_retry_loop(tmp_path, monkeypatch):
    runtime, parent, host, state, sent, authorized, listeners = await setup(tmp_path, monkeypatch)
    try:
        await runtime.service.create('One', 'Stop on failure', context=USER)
        await runtime.flush()
        await runtime._settled('original-failure', 'failed')
        waiting = await runtime.service.current()
        for _ in range(10):
            await runtime._settled('original-failure', 'failed')
            await runtime._available()
        await asyncio.wait_for(runtime.flush(), 2)
        assert await runtime.service.current() == waiting
        assert waiting.wait_for == 'host' and not sent
        assert runtime._observer is None
    finally:
        await close(runtime, parent)


@pytest.mark.parametrize('transition', ['create', 'resume'])
async def test_atomic_goal_state_cause_survives_reopen_without_post_commit_callback(
        tmp_path, monkeypatch, transition):
    runtime, parent, host, state, sent, authorized, listeners = await setup(tmp_path, monkeypatch)
    await receipt_states(monkeypatch)
    try:
        await runtime.flush()
        runtime.service.on_change = lambda: None  # Simulate losing all post-commit callbacks.
        goal = await runtime.service.create('One', 'Durable initial or recovery cause', context=USER)
        if transition == 'resume':
            await runtime.service.change('pause', context=USER)
            goal = await runtime.service.change('resume', context=USER)
        wake = await runtime.service.store.wake_state(goal.id)
        cause = 'goal-state:' + str(goal.version)
        assert cause in wake['causes'] and not wake['reminders'] and not sent
        await runtime.aclose()
        runtime = await GoalRuntime.open(host)
        runtime.attach(parent)
        await runtime._available()
        await asyncio.wait_for(runtime.flush(), 2)
        assert len(sent) == 1 and cause in sent[0].provenance['triggers']
    finally:
        await close(runtime, parent)


async def test_fast_worker_cause_not_yet_consumed_survives_entering_task_wait(tmp_path, monkeypatch):
    runtime, parent, host, state, sent, authorized, listeners = await setup(tmp_path, monkeypatch)
    await receipt_states(monkeypatch)
    try:
        goal = await runtime.service.create('One', 'Worker can finish before main Agent sets its wait', context=USER)
        await runtime.flush()
        state['state'] = 'running'
        runtime.service.files(goal).write(Task('fast', 'Already finished', status='completed'), expected_version=0)
        await runtime._task_changed(goal.id, SimpleNamespace(id='finished-before-wait'))
        await runtime.flush()
        assert not sent  # Main turn still running; this cause has not been delivered/consumed.
        await runtime.service.change('wait', until='tasks', reason='Await the work just delegated')
        state['state'] = 'idle'
        await runtime._settled('main-entered-task-wait', 'succeeded')
        for _ in range(2):
            await runtime._available()
        await runtime.flush()
        assert len(sent) == 1, 'Entering task wait discarded an undelivered fast-worker cause'
        assert 'tasks:finished-before-wait' in sent[0].provenance['triggers']
    finally:
        await close(runtime, parent)


@pytest.mark.parametrize('fence', ['pause', 'owner_retired'])
async def test_final_snapshot_read_cannot_return_through_a_new_pause_or_owner_fence(tmp_path, monkeypatch, fence):
    runtime, parent, host, state, sent, authorized, listeners = await setup(tmp_path, monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()
    original_current = runtime.service.store.current
    original_claim = runtime.service.store.claim_reminder
    original_valid = host.valid
    armed = False
    pending = None

    async def claim(identifier, goal):
        nonlocal armed
        result = await original_claim(identifier, goal)
        armed = result
        return result

    async def current():
        nonlocal armed
        if not armed:
            return await original_current()
        armed = False
        stale = await original_current()
        entered.set()
        await release.wait()
        return stale  # A completed pause must invalidate this old read, not just a new DB query.

    monkeypatch.setattr(runtime.service.store, 'claim_reminder', claim)
    monkeypatch.setattr(runtime.service.store, 'current', current)
    try:
        await runtime.service.create('One', 'Fence the final snapshot itself', context=USER)
        await runtime.flush()
        pending = asyncio.create_task(runtime._wake('execution:final-read'))
        await asyncio.wait_for(entered.wait(), 2)
        assert not sent
        if fence == 'pause':
            # Direct service mutation: do not rely on the native command's control counter.
            await runtime.service.change('pause', context=USER, reason='Stop during the final read')
        else:
            host.valid = lambda: False
        release.set()
        await asyncio.wait_for(pending, 2)
        assert not sent
        assert (await runtime.service.store.reminders())[0]['status'] == 'unavailable'
    finally:
        release.set()
        if pending is not None:
            await asyncio.gather(pending, return_exceptions=True)
        host.valid = original_valid
        await close(runtime, parent)
