"""Host failures are facts and receipt fences, not Goal business-state changes."""
import asyncio
import time
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from jsonschema import Draft202012Validator
from xdog.skills.goal.host import OperationContext
from xdog.skills.goal.runtime import GoalRuntime

from packages.skills.tests.goal.legacy import wait_goal
from packages.skills.tests.goal.test_reminder_review import receipt_states
from packages.skills.tests.goal.test_runtime import close, setup

USER = OperationContext(True)


async def test_new_host_wait_rejected_by_tool_schema_service_and_store(tmp_path, monkeypatch):
    runtime, parent, _, state, _, _, _ = await setup(tmp_path, monkeypatch)
    state['state'] = 'running'
    try:
        goal = await runtime.service.create('One', 'No host business state', context=USER)
        args = {'action': 'wait', 'until': 'host', 'reason': 'A tool needs permission'}
        tool = runtime.tools[0]
        assert not Draft202012Validator(tool.parameters).is_valid(args)
        assert (await tool.execute('denied', args)).is_error
        with pytest.raises(ValueError, match='only user or tasks'):
            await runtime.service.change('wait', until='host', context=USER)
        with pytest.raises(ValueError, match='historical read-only'):
            await runtime.service.store.put(replace(goal, status='waiting', wait_for='host', version=2),
                                            expected_version=1)
        assert await runtime.service.current() == goal
        assert await runtime.service.store.history(goal.id) == (goal,)
    finally:
        await close(runtime, parent)


@pytest.mark.parametrize('status', ['failed', 'cancelled', 'canceled', 'interrupted', 'preparation_failed'])
@pytest.mark.parametrize('wait_tasks', [False, True])
async def test_failure_fact_survives_reopen_without_changing_business_state(tmp_path, monkeypatch, status, wait_tasks):
    runtime, parent, host, state, sent, _, listeners = await setup(tmp_path, monkeypatch)
    try:
        goal = await runtime.service.create('One', 'Keep actual business state', context=USER)
        if wait_tasks:
            goal = await runtime.service.change('wait', until='tasks', reason='Only waiting for assigned task evidence')
        await runtime.flush()
        transition = await runtime.service.store.last_transition(goal)
        history = await runtime.service.store.history(goal.id)
        await runtime._settled('original', status)
        await runtime._settled('original', status)
        assert await runtime.service.current() == goal
        assert await runtime.service.store.history(goal.id) == history
        assert await runtime.service.store.last_transition(goal) == transition
        facts = await runtime.service.summary()
        fault_history = (await runtime.service.store.wake_state(goal.id))['fault_history']
        assert len(fault_history) == 1 and fault_history[0]['status'] == status
        if status == 'preparation_failed':
            assert not facts['runtime_faults']  # A diagnostic is not an implicit host wait.
        else:
            assert len(facts['runtime_faults']) == 1 and facts['runtime_faults'][0]['status'] == status
            assert facts['next']['action'] == 'inspect_execution'
            assert facts['next']['requires_user'] is (status == 'interrupted')
            assert 'Runtime fault: execution/' in await parent.extensions.command('goal', 'status')
        await runtime.aclose()
        runtime = await GoalRuntime.open(host)
        runtime.attach(parent)
        await runtime._task_changed(goal.id, SimpleNamespace(id='offline-change'))
        listeners[0]({'type': 'owner.available'})
        await runtime.flush()
        assert len(sent) == (1 if status == 'preparation_failed' else 0)
        assert await runtime.service.current() == goal
        assert (await runtime.service.summary())['runtime_faults'] == facts['runtime_faults']
    finally:
        await close(runtime, parent)


@pytest.mark.parametrize('receipt', ['pending', 'claimed', 'running', None, 'interrupted'])
async def test_historical_host_wait_restore_and_explicit_conversion_keep_original_receipt_fence(
        tmp_path, monkeypatch, receipt):
    runtime, parent, host, _, sent, _, listeners = await setup(tmp_path, monkeypatch)
    states = await receipt_states(monkeypatch)
    import xdog.agent.acp.client as client
    try:
        goal = await runtime.service.create('One', 'Retain original accepted work', context=USER)
        await runtime._wake('original-cause')
        original = sent[0]
        states[original.id] = receipt
        if receipt is None:
            async def missing(target, identifier=None):
                return {'messages': []}
            monkeypatch.setattr(client, 'inbox_status', missing)
        legacy = await wait_goal(runtime.service, until='host', reason='Historical host error')
        saved = await runtime.service.store.history(goal.id)
        await runtime.aclose()
        runtime = await GoalRuntime.open(host)
        runtime.attach(parent)
        listeners[0]({'type': 'owner.available'})
        await runtime.flush()
        assert await runtime.service.current() == legacy
        assert await runtime.service.store.history(goal.id) == saved
        assert len(sent) == 1
        with pytest.raises(ValueError, match='user operation'):
            await runtime.service.change('activate', goal.id, version=legacy.version, reason='automatic event')
        with pytest.raises(ValueError, match='Resume only'):
            await runtime.service.change('resume', context=USER)
        active = await runtime.service.change('activate', goal.id, version=legacy.version,
                                              reason='Actual user inspected old work', context=USER)
        await runtime._task_changed(goal.id, SimpleNamespace(id='new-work'))
        await runtime._available()
        await runtime.flush()
        assert await runtime.service.current() == active
        assert len(sent) == 1  # Explicit conversion does not prove interrupted effects were absent.
        row = next(row for row in await runtime.service.store.reminders() if row['id'] == original.id)
        assert row['status'] == 'accepted'
    finally:
        await close(runtime, parent)


@pytest.mark.parametrize('retiring', [False, True])
async def test_owner_exit_detaches_without_suspending_own_registration(tmp_path, monkeypatch, retiring):
    runtime, parent, host, _, _, _, listeners = await setup(tmp_path, monkeypatch)
    try:
        await runtime.service.create('One', 'Offline collection', context=USER)
        await runtime.flush()
        observer = runtime._observer
        if retiring:
            host.accepting = lambda: False
            listeners[0]({'type': 'owner.retiring'})
            await runtime.flush()
        else:
            await runtime.aclose()
        assert observer.closed and observer.suspended is False
    finally:
        await close(runtime, parent)


@pytest.mark.parametrize('action', ['pause', 'user', 'complete', 'clear'])
async def test_goal_business_stop_suspends_own_registration(tmp_path, monkeypatch, action):
    runtime, parent, _, _, _, _, _ = await setup(tmp_path, monkeypatch)
    try:
        await runtime.service.create('One', 'Stop own registration only', context=USER)
        await runtime.flush()
        observer = runtime._observer
        if action == 'user':
            await runtime.service.change('wait', until='user', question='Choose target?', impact='Unknown target',
                                         resume_when='Actual user selects target')
        else:
            fields = {'summary': 'Verified'} if action == 'complete' else {}
            await runtime.service.change(action, context=USER, **fields)
        await runtime.flush()
        assert observer.closed and observer.suspended is True
    finally:
        await close(runtime, parent)


async def test_same_parent_attach_reconciles_resume_without_a_new_turn(tmp_path, monkeypatch):
    runtime, parent, _, state, sent, _, _ = await setup(tmp_path, monkeypatch)
    await receipt_states(monkeypatch)
    try:
        state['state'] = 'running'
        goal = await runtime.service.create('One', 'Resume original owner', context=USER)
        await runtime._task_changed(goal.id, SimpleNamespace(id='original-event'))
        await runtime.flush()
        assert not sent
        state['state'] = 'idle'
        runtime.attach(parent)
        await runtime.flush()
        assert len(sent) == 1 and 'tasks:original-event' in sent[0].provenance['triggers']
        for _ in range(3):
            runtime.attach(parent)
            await runtime.flush()
        assert len(sent) == 1
    finally:
        await close(runtime, parent)


async def test_old_receipt_failure_lookup_cannot_fence_new_resume_epoch(tmp_path, monkeypatch):
    import xdog.agent.acp.client as client
    runtime, parent, _, _, sent, _, _ = await setup(tmp_path, monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()
    pending = None
    try:
        await runtime.service.create('One', 'Original receipt only', context=USER)
        await runtime._wake('initial')
        async def lookup(target, identifier=None):
            entered.set()
            await release.wait()
            return {'messages': [{'message_id': identifier, 'status': 'failed'}]}
        monkeypatch.setattr(client, 'inbox_status', lookup)
        pending = asyncio.create_task(runtime._wake('later'))
        await asyncio.wait_for(entered.wait(), 2)
        await runtime.service.change('pause', context=USER)
        resumed = await runtime.service.change('resume', context=USER)
        release.set()
        await pending
        await runtime.flush()
        assert await runtime.service.current() == resumed
        assert not (await runtime.service.summary())['runtime_faults']
        assert len(sent) == 2 and sent[0].id != sent[1].id
    finally:
        release.set()
        if pending is not None:
            await asyncio.gather(pending, return_exceptions=True)
        await close(runtime, parent)


async def test_content_edit_at_fault_admission_cannot_drop_replay_fence(tmp_path, monkeypatch):
    runtime, parent, _, _, sent, _, _ = await setup(tmp_path, monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()
    pending = None
    original = runtime.service.store.record_fault
    async def record(*args, **kwargs):
        entered.set()
        await release.wait()
        return await original(*args, **kwargs)
    monkeypatch.setattr(runtime.service.store, 'record_fault', record)
    try:
        goal = await runtime.service.create('One', 'Content is not recovery authority', context=USER)
        await runtime.flush()
        pending = asyncio.create_task(runtime._settled('original-failure', 'interrupted', expected=goal))
        await asyncio.wait_for(entered.wait(), 2)
        revised = await runtime.service.plan('A content-only revision while the fact is being saved')
        release.set()
        await pending
        await runtime._task_changed(goal.id, SimpleNamespace(id='later'))
        await runtime._available()
        await runtime.flush()
        assert await runtime.service.current() == revised
        assert len((await runtime.service.summary())['runtime_faults']) == 1 and not sent
    finally:
        release.set()
        if pending is not None:
            await asyncio.gather(pending, return_exceptions=True)
        await close(runtime, parent)


async def test_late_unknown_send_cannot_fault_a_replacement_goal(tmp_path, monkeypatch):
    import xdog.agent.acp.client as client
    runtime, parent, _, _, _, _, _ = await setup(tmp_path, monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()
    pending = None
    async def send(target, message):
        entered.set()
        await release.wait()
        return {'status': 'outcome-unknown'}
    monkeypatch.setattr(client, 'enqueue', send)
    try:
        original = await runtime.service.create('Old', 'Original effects remain uncertain', context=USER)
        await runtime.flush()
        pending = asyncio.create_task(runtime._wake('original'))
        await asyncio.wait_for(entered.wait(), 2)
        # Isolate the late original result from a separately authorized new
        # Goal's callback-driven wake (as in a lost post-commit callback).
        runtime.service.on_change = lambda: None
        await runtime.service.change('clear', context=USER)
        replacement = await runtime.service.create('New', 'Do not inherit another Goal fault', context=USER)
        release.set()
        await pending
        await runtime.flush()
        assert await runtime.service.current() == replacement
        facts = await runtime.service.summary()
        assert facts['runtime_faults'] == [] and facts['next']['action'] == 'main_agent_work'
        assert facts['delivery']['status'] == 'idle' and facts['last_delivery']['status'] == 'unknown'
        rows = await runtime.service.store.reminders()
        assert len(rows) == 1 and rows[0]['goal_id'] == original.id and rows[0]['status'] == 'unknown'
    finally:
        release.set()
        if pending is not None:
            await asyncio.gather(pending, return_exceptions=True)
        await close(runtime, parent)


async def test_preparation_failed_needs_fresh_cause_not_goal_pause_resume(tmp_path, monkeypatch):
    runtime, parent, _, _, sent, _, _ = await setup(tmp_path, monkeypatch)
    await receipt_states(monkeypatch)
    try:
        goal = await runtime.service.create('One', 'Preparation never executed the model', context=USER)
        await runtime.flush()
        await runtime._settled('not-started', 'preparation_failed')
        for _ in range(3):
            await runtime._available()
        assert not sent  # Availability never retries the old input or manufactures a cause.
        assert await runtime._task_changed(goal.id, SimpleNamespace(id='actual-new-task-change')) is True
        await runtime.flush()
        assert len(sent) == 1 and await runtime.service.current() == goal
        assert 'tasks:actual-new-task-change' in sent[0].provenance['triggers']
        for _ in range(3):
            await runtime._available()
        assert len(sent) == 1
        wake = await runtime.service.store.wake_state(goal.id)
        assert not wake['faults'] and wake['fault_history'][0]['status'] == 'preparation_failed'
    finally:
        await close(runtime, parent)


@pytest.mark.parametrize('status', ['failed', 'cancelled', 'canceled'])
async def test_actual_later_owner_success_recovers_known_failure_without_goal_resume(tmp_path, monkeypatch, status):
    runtime, parent, host, _, sent, _, _ = await setup(tmp_path, monkeypatch)
    await receipt_states(monkeypatch)
    try:
        goal = await runtime.service.create('One', 'Owner recovery is not Goal authorization', context=USER)
        await runtime.flush()
        await runtime._settled('old-terminal', status)
        host.latest_execution = AsyncMock(return_value={
            'execution_id': 'actual-later-success', 'status': 'succeeded', 'settled_at': time.time(),
            'message_ids': ['a-new-normal-input']})
        await runtime._settled('delayed-older-success', 'succeeded')
        assert not sent and (await runtime.service.store.wake_state(goal.id))['faults']
        await runtime._settled('actual-later-success', 'succeeded')
        assert len(sent) == 1 and await runtime.service.current() == goal
        assert 'execution:actual-later-success' in sent[0].provenance['triggers']
        wake = await runtime.service.store.wake_state(goal.id)
        assert not wake['faults'] and wake['fault_history'][0]['recovery_id'] == 'actual-later-success'
        assert wake['fault_history'][0]['status'] == status
        for _ in range(3):
            await runtime._available()
        assert len(sent) == 1
        await runtime.aclose()
        runtime = await GoalRuntime.open(host)
        runtime.attach(parent)
        await runtime.flush()
        assert len(sent) == 1 and not (await runtime.service.store.wake_state(goal.id))['faults']
    finally:
        await close(runtime, parent)


@pytest.mark.parametrize('receipt', ['failed', 'cancelled', 'preparation_failed'])
async def test_real_success_supersedes_terminal_original_receipt_without_resubmitting_it(
        tmp_path, monkeypatch, receipt):
    runtime, parent, host, _, sent, _, _ = await setup(tmp_path, monkeypatch)
    states = await receipt_states(monkeypatch)
    try:
        goal = await runtime.service.create('One', 'Preserve original receipt identities', context=USER)
        await runtime._wake('initial')
        original = sent[0]
        states[original.id] = receipt
        saved = (await runtime.service.store.reminders())[0]
        host.latest_execution = AsyncMock(return_value={
            'execution_id': 'real-normal-success', 'status': 'succeeded', 'settled_at': time.time(),
            'message_ids': ['a-separate-input']})
        await runtime._available()
        await runtime.flush()
        assert len(sent) == 2 and sent[1].id != original.id
        assert 'execution:real-normal-success' in sent[1].provenance['triggers']
        assert (await runtime.service.store.reminders())[0] == saved
        assert await runtime.service.current() == goal
        for _ in range(3):
            await runtime._available()
        assert len(sent) == 2
    finally:
        await close(runtime, parent)


@pytest.mark.parametrize('source,status', [
    ('execution', 'interrupted'), ('execution', 'unknown'), ('delivery', 'unknown'),
])
async def test_success_and_goal_epoch_never_release_uncertain_effect_fences(tmp_path, monkeypatch, source, status):
    runtime, parent, host, _, sent, _, _ = await setup(tmp_path, monkeypatch)
    try:
        goal = await runtime.service.create('One', 'Success is not proof of absence of old effects', context=USER)
        await runtime.flush()
        await runtime._record_fault(source, 'original-uncertain', status, 'Original effects need inspection')
        host.latest_execution = AsyncMock(return_value={
            'execution_id': 'new-success', 'status': 'succeeded', 'settled_at': time.time(), 'message_ids': ['new']})
        await runtime._available()
        await runtime.service.change('pause', context=USER)
        resumed = await runtime.service.change('resume', context=USER)
        await runtime._task_changed(goal.id, SimpleNamespace(id='new-change'))
        await runtime._available()
        await runtime.flush()
        wake = await runtime.service.store.wake_state(goal.id)
        assert not sent and await runtime.service.current() == resumed
        assert wake['faults'][0]['identity'] == 'original-uncertain' and not wake['faults'][0]['recovery_id']
    finally:
        await close(runtime, parent)


async def test_success_snapshot_cannot_release_a_new_concurrent_failure(tmp_path, monkeypatch):
    runtime, parent, host, _, sent, _, _ = await setup(tmp_path, monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()
    pending = None
    try:
        goal = await runtime.service.create('One', 'Recovery belongs only to observed faults', context=USER)
        await runtime._settled('old-failure', 'failed')
        async def latest():
            entered.set()
            await release.wait()
            return {'execution_id': 'success-before-new-failure', 'status': 'succeeded', 'settled_at': time.time()}
        host.latest_execution = latest
        pending = asyncio.create_task(runtime._settled('success-before-new-failure', 'succeeded'))
        await asyncio.wait_for(entered.wait(), 2)
        await runtime.service.store.record_fault(goal, 'execution', 'new-failure', 'failed', 'Later actual failure')
        release.set()
        await pending
        wake = await runtime.service.store.wake_state(goal.id)
        assert [row['identity'] for row in wake['faults']] == ['new-failure'] and not sent
    finally:
        release.set()
        if pending is not None:
            await asyncio.gather(pending, return_exceptions=True)
        await close(runtime, parent)


@pytest.mark.parametrize('fact_kind', ['missing', 'unavailable', 'different', 'running', 'old'])
async def test_success_without_matching_current_durable_host_fact_does_not_recover(
        tmp_path, monkeypatch, fact_kind):
    runtime, parent, host, _, sent, _, _ = await setup(tmp_path, monkeypatch)
    try:
        goal = await runtime.service.create('One', 'Recovery requires the original actual host', context=USER)
        await runtime._settled('original-failure', 'failed')
        fact = {'execution_id': 'candidate-success', 'status': 'succeeded', 'settled_at': time.time()}
        if fact_kind == 'missing':
            fact = None
        elif fact_kind == 'unavailable':
            fact = {'availability': 'unavailable', 'reason': 'No durable execution metadata'}
        elif fact_kind == 'different':
            fact['execution_id'] = 'a-different-execution'
        elif fact_kind == 'running':
            fact['status'] = 'running'
        else:
            fact['settled_at'] = goal.created_at - 1
        host.latest_execution = AsyncMock(return_value=fact)
        await runtime._settled('candidate-success', 'succeeded')
        assert not sent and (await runtime.service.store.wake_state(goal.id))['faults']
        assert await runtime.service.current() == goal
    finally:
        await close(runtime, parent)


async def test_known_failure_recovery_does_not_bypass_an_original_pending_receipt(tmp_path, monkeypatch):
    runtime, parent, host, _, sent, _, _ = await setup(tmp_path, monkeypatch)
    states = await receipt_states(monkeypatch)
    try:
        goal = await runtime.service.create('One', 'A separate success cannot finish pending work', context=USER)
        await runtime._wake('initial')
        original = sent[0]
        states[original.id] = 'pending'
        saved = (await runtime.service.store.reminders())[0]
        await runtime._settled('a-known-failure', 'failed')
        host.latest_execution = AsyncMock(return_value={
            'execution_id': 'later-normal-success', 'status': 'succeeded', 'settled_at': time.time()})
        await runtime._available()
        assert not (await runtime.service.store.wake_state(goal.id))['faults']
        assert len(sent) == 1 and (await runtime.service.store.reminders())[0] == saved
        states[original.id] = 'succeeded'
        await runtime._available()
        assert len(sent) == 2 and sent[1].id != original.id
        assert await runtime.service.current() == goal
    finally:
        await close(runtime, parent)
