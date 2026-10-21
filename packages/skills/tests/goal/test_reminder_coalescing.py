"""One durable Goal coalescer; original receipts, not timers, control continuation."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from xdog.skills.goal.host import OperationContext
from xdog.skills.goal.runtime import GoalRuntime

from packages.skills.tests.goal.test_runtime import close, setup

USER = OperationContext(True)


async def receipt_facts(monkeypatch):
    import xdog.agent.acp.client as client
    states = {}

    async def status(target, identifier=None):
        state = states.get(identifier, 'succeeded')
        return {'messages': [] if state is None else [{'message_id': identifier, 'status': state}]}

    monkeypatch.setattr(client, 'inbox_status', status)
    return states


async def test_busy_execution_and_many_eyes_changes_merge_into_one_reminder(tmp_path, monkeypatch):
    runtime, parent, host, state, sent, authorized, listeners = await setup(tmp_path, monkeypatch)
    await receipt_facts(monkeypatch)
    try:
        goal = await runtime.service.create('One', 'One original owner', context=USER)
        state['state'] = 'running'
        for index in range(12):
            await runtime._task_changed(goal.id, SimpleNamespace(id=f'file-change-{index}'))
        listeners[0]({'type': 'execution.settled', 'execution_id': 'done', 'status': 'succeeded'})
        await runtime.flush()
        assert not sent
        state['state'] = 'idle'
        listeners[0]({'type': 'owner.available'})
        await runtime.flush()
        assert len(sent) == 1
        assert {f'tasks:file-change-{i}' for i in range(12)} <= set(sent[0].provenance['triggers'])
        assert 'execution:done' in sent[0].provenance['triggers']
        assert 'goal(action="report")' in sent[0].instruction
        assert 'not a blind loop' in sent[0].instruction
    finally:
        await close(runtime, parent)


async def test_accepted_pending_reminder_backpressures_new_changes_without_losing_them(tmp_path, monkeypatch):
    runtime, parent, host, state, sent, authorized, listeners = await setup(tmp_path, monkeypatch)
    facts = await receipt_facts(monkeypatch)
    try:
        goal = await runtime.service.create('One', 'Retain later changes', context=USER)
        await runtime._wake('first')
        facts[sent[0].id] = 'pending'
        await runtime._task_changed(goal.id, SimpleNamespace(id='later'))
        await runtime.flush()
        assert len(sent) == 1 and runtime.delivery['status'] == 'pending'
        facts[sent[0].id] = 'succeeded'
        listeners[0]({'type': 'owner.available'})
        await runtime.flush()
        assert len(sent) == 2 and sent[0].id != sent[1].id
        assert sent[1].provenance['wake_through'] > sent[0].provenance['wake_through']
        # The same detector delivery cannot manufacture another cause or new input.
        await runtime._task_changed(goal.id, SimpleNamespace(id='later'))
        await runtime.flush()
        assert len(sent) == 2
    finally:
        await close(runtime, parent)


async def test_process_reopen_recovers_deferred_cause_without_in_memory_execution(tmp_path, monkeypatch):
    runtime, parent, host, state, sent, authorized, listeners = await setup(tmp_path, monkeypatch)
    await receipt_facts(monkeypatch)
    try:
        await runtime.service.create('One', 'Durable wake', context=USER)
        state['state'] = 'running'
        await runtime._wake('execution:durable-original')
        assert not sent
        await runtime.aclose()
        runtime = await GoalRuntime.open(host)
        runtime.attach(parent)
        assert '_last_execution' not in vars(runtime) and '_pending_wake' not in vars(runtime)
        state['state'] = 'idle'
        listeners[0]({'type': 'owner.available'})
        await runtime.flush()
        assert len(sent) == 1
        assert 'execution:durable-original' in sent[0].provenance['triggers']
    finally:
        await close(runtime, parent)


async def test_known_not_sent_retries_exact_original_id_and_body(tmp_path, monkeypatch):
    import xdog.agent.acp.client as client
    runtime, parent, host, state, sent, authorized, listeners = await setup(tmp_path, monkeypatch)
    attempts = []

    async def enqueue(target, message):
        attempts.append(message)
        return {'status': 'unavailable' if len(attempts) == 1 else 'queued'}

    monkeypatch.setattr(client, 'enqueue', enqueue)
    try:
        await runtime.service.create('One', 'Exact retry', context=USER)
        await runtime._wake('execution:first')
        assert runtime.delivery['status'] == 'unavailable'
        assert (await runtime.service.current()).status == 'active'
        await runtime._available()
        assert len(attempts) == 2 and attempts[0] == attempts[1]
        assert (await runtime.service.store.reminders())[0]['status'] == 'accepted'
    finally:
        await close(runtime, parent)


async def test_unknown_original_after_restart_waits_and_never_sends_new_id(tmp_path, monkeypatch):
    runtime, parent, host, state, sent, authorized, listeners = await setup(tmp_path, monkeypatch)
    try:
        goal = await runtime.service.create('One', 'Inspect unknown', context=USER)
        await runtime.service.store.signal(goal, 'execution:first')
        await runtime.service.store.reminder(goal, {'id': 'original', 'instruction': 'original',
            'provenance': {'goal_id': goal.id, 'goal_version': goal.version}})
        assert await runtime.service.store.claim_reminder('original', goal)
        await runtime.aclose()
        runtime = await GoalRuntime.open(host)
        runtime.attach(parent)
        await runtime._available()
        assert not sent
        current = await runtime.service.current()
        assert current.status == 'waiting' and current.wait_for == 'host'
        assert runtime.delivery['message_id'] == 'original'
    finally:
        await close(runtime, parent)


@pytest.mark.parametrize('fact_status', ['failed', 'interrupted', 'succeeded'])
async def test_startup_uses_actual_persisted_execution_fact(tmp_path, monkeypatch, fact_status):
    runtime, parent, host, state, sent, authorized, listeners = await setup(tmp_path, monkeypatch)
    await receipt_facts(monkeypatch)
    try:
        goal = await runtime.service.create('One', 'Read actual facts', context=USER)
        host.latest_execution = AsyncMock(return_value={'execution_id': 'original-started', 'status': fact_status,
                                                       'settled_at': goal.created_at + 1, 'message_ids': ['input']})
        await runtime._available()
        current = await runtime.service.current()
        assert len(sent) == (1 if fact_status == 'succeeded' else 0)
        assert current.status == ('active' if fact_status == 'succeeded' else 'waiting')
        await runtime._available()
        assert len(sent) <= 1
    finally:
        await close(runtime, parent)


async def test_pause_stops_owned_eyes_and_all_automatic_paths_until_explicit_resume(tmp_path, monkeypatch):
    runtime, parent, host, state, sent, authorized, listeners = await setup(tmp_path, monkeypatch)
    await receipt_facts(monkeypatch)
    try:
        goal = await runtime.service.create('One', 'Stop while paused', context=USER)
        await runtime.flush()
        observer = runtime._observer
        assert observer is not None
        # Model self-pause is a reduction in execution, never authority to resume.
        before = (await runtime.service.store.wake_state(goal.id))['sequence']
        await runtime.service.change('pause', reason='Repeated no progress; need a new approach')
        await runtime.flush()
        assert observer.closed and runtime._observer is None
        assert runtime.automation['status'] == 'paused'
        await runtime._task_changed(goal.id, SimpleNamespace(id='stale-delivery'))
        listeners[0]({'type': 'execution.settled', 'execution_id': 'paused-done', 'status': 'succeeded'})
        listeners[0]({'type': 'owner.available'})
        await runtime.flush()
        assert not sent and (await runtime.service.store.wake_state(goal.id))['sequence'] == before
        with pytest.raises(ValueError, match='user'):
            await runtime.service.change('resume')
        await parent.extensions.command('goal', 'resume')
        await runtime.flush()
        assert runtime._observer is not None and runtime._observer is not observer
        assert len(sent) == 1
    finally:
        await close(runtime, parent)


async def test_explicit_resume_does_not_reapply_old_failed_execution_fact(tmp_path, monkeypatch):
    import time
    runtime, parent, host, state, sent, authorized, listeners = await setup(tmp_path, monkeypatch)
    await receipt_facts(monkeypatch)
    try:
        await runtime.service.create('One', 'User explicitly chooses to resume', context=USER)
        settled_at = time.time()
        host.latest_execution = AsyncMock(return_value={'execution_id': 'old-failure', 'status': 'failed',
                                                       'settled_at': settled_at, 'message_ids': ['old']})
        await runtime._available()
        assert (await runtime.service.current()).wait_for == 'host'
        await parent.extensions.command('goal', 'resume')
        await runtime.flush()
        await runtime._available()
        assert (await runtime.service.current()).status == 'active' and len(sent) == 1
    finally:
        await close(runtime, parent)


async def test_pause_resume_reconciles_task_edits_without_replaying_paused_ticks(tmp_path, monkeypatch):
    runtime, parent, host, state, sent, authorized, listeners = await setup(tmp_path, monkeypatch)
    await receipt_facts(monkeypatch)
    try:
        state['state'] = 'running'
        goal = await runtime.service.create('One', 'Inspect current task files on resume', context=USER)
        task = await runtime.service.task({'id': 't', 'title': 'Task', 'expected_version': 0})
        await runtime.flush()
        before = await runtime.service.store.wake_state(goal.id)
        await runtime.service.change('pause', context=USER)
        await runtime.flush()
        runtime.service.files(goal).update('t', task.version, {'status': 'completed', 'summary': 'Worker finished'})
        assert (await runtime.service.store.wake_state(goal.id))['sequence'] == before['sequence']
        assert runtime._observer is None
        # Resume uses a new observation baseline AND reconciles actual files.
        state['state'] = 'idle'
        await parent.extensions.command('goal', 'resume')
        await runtime.flush()
        after = await runtime.service.store.wake_state(goal.id)
        assert after['manifest'] != before['manifest']
        assert len(sent) == 1
        assert runtime.service.files(goal).list()[0].status == 'completed'
    finally:
        await close(runtime, parent)


async def test_waiting_tasks_requires_new_task_change_not_settlement_or_availability(tmp_path, monkeypatch):
    runtime, parent, host, state, sent, authorized, listeners = await setup(tmp_path, monkeypatch)
    await receipt_facts(monkeypatch)
    try:
        goal = await runtime.service.create('One', 'Wait, do not spin', context=USER)
        await runtime.service.change('wait', until='tasks', reason='Waiting for assigned work')
        listeners[0]({'type': 'execution.settled', 'execution_id': 'natural-stop', 'status': 'succeeded'})
        listeners[0]({'type': 'owner.available'})
        await runtime.flush()
        assert not sent
        await runtime._task_changed(goal.id, SimpleNamespace(id='worker-finished'))
        await runtime.flush()
        assert len(sent) == 1
        listeners[0]({'type': 'execution.settled', 'execution_id': 'still-waiting', 'status': 'succeeded'})
        listeners[0]({'type': 'owner.available'})
        await runtime.flush()
        assert len(sent) == 1
        assert (await runtime.service.current()).wait_for == 'tasks'
    finally:
        await close(runtime, parent)


async def test_explicit_resume_can_wake_an_already_active_idle_goal(tmp_path, monkeypatch):
    runtime, parent, host, state, sent, authorized, listeners = await setup(tmp_path, monkeypatch)
    await receipt_facts(monkeypatch)
    try:
        await runtime.service.create('One', 'Explicitly wake idle work', context=USER)
        await runtime._wake('execution:first')
        assert len(sent) == 1
        await parent.extensions.command('goal', 'resume')
        await runtime.flush()
        assert len(sent) == 2 and sent[0].id != sent[1].id
        assert 'goal-state:2' in sent[1].provenance['triggers']
        assert (await runtime.service.current()).status == 'active'
    finally:
        await close(runtime, parent)
