"""Goal-only wake/epoch interleavings; fake host, observer and ACP, no model IO."""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from xdog.skills.goal.host import OperationContext, OwnerRetired

from packages.skills.tests.goal.legacy import wait_goal
from packages.skills.tests.goal.test_reminder_review import receipt_states
from packages.skills.tests.goal.test_runtime import close, setup

USER = OperationContext(True)


async def waiting_with_deferred_task(runtime, state):
    goal = await runtime.service.create('Race', 'Keep the original durable task cause', context=USER)
    await runtime.flush()
    await runtime.service.change('wait', until='tasks')
    await runtime.flush()
    state['state'] = 'running'
    await runtime._task_changed(goal.id, SimpleNamespace(id='original-task'))
    await runtime.flush()
    assert not await runtime.service.store.reminders()
    state['state'] = 'idle'
    return goal


@pytest.mark.parametrize('source', ['available', 'wake'])
async def test_version_change_during_wake_reschedules_unconsumed_task(tmp_path, monkeypatch, source):
    runtime, parent, host, state, sent, _, _ = await setup(tmp_path, monkeypatch)
    await receipt_states(monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()
    pending = None
    original = host.acp_target

    async def target():
        if asyncio.current_task() is pending:
            entered.set()
            await release.wait()
        return await original()

    try:
        goal = await waiting_with_deferred_task(runtime, state)
        monkeypatch.setattr(host, 'acp_target', target)
        operation = runtime._available() if source == 'available' else runtime._wake(None)
        pending = asyncio.create_task(operation)
        await asyncio.wait_for(entered.wait(), 2)
        updated = await runtime.service.plan('A concurrent valid plan revision')
        await runtime.flush()  # Same observer key: refresh cannot manufacture a task change.
        release.set()
        await asyncio.wait_for(pending, 2)
        await asyncio.wait_for(runtime.flush(), 2)
        assert len(sent) == 1, 'A revision invalidated the only availability wake without replacing it'
        assert sent[0].provenance['goal_version'] == updated.version
        assert 'tasks:original-task' in sent[0].provenance['triggers']
        assert (await runtime.service.store.wake_state(goal.id))['task_sequence'] > 0
        for _ in range(3):
            await runtime._available()
        await runtime.flush()
        assert len(sent) == 1 and not runtime._jobs and not runtime._job_errors
    finally:
        release.set()
        if pending is not None:
            await asyncio.gather(pending, return_exceptions=True)
        await close(runtime, parent)


@pytest.mark.parametrize('boundary', ['epoch_read', 'fault_admission'])
async def test_old_available_failure_cannot_fence_a_resumed_epoch(tmp_path, monkeypatch, boundary):
    runtime, parent, host, state, sent, _, _ = await setup(tmp_path, monkeypatch)
    await receipt_states(monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()
    pending = None
    original_wake_state = runtime.service.store.wake_state
    original_fault = runtime.service.store.record_fault

    async def wake_state(identifier):
        result = await original_wake_state(identifier)
        if boundary == 'epoch_read' and asyncio.current_task() is pending:
            entered.set()
            await release.wait()
        return result

    async def record_fault(*args, **kwargs):
        if boundary == 'fault_admission' and asyncio.current_task() is pending:
            entered.set()
            await release.wait()
        return await original_fault(*args, **kwargs)

    try:
        goal = await waiting_with_deferred_task(runtime, state)
        fact = {'execution_id': 'old-failure', 'status': 'failed', 'settled_at': time.time()}
        host.latest_execution = AsyncMock(return_value=fact)
        monkeypatch.setattr(runtime.service.store, 'wake_state', wake_state)
        monkeypatch.setattr(runtime.service.store, 'record_fault', record_fault)
        pending = asyncio.create_task(runtime._available())
        await asyncio.wait_for(entered.wait(), 2)
        await runtime.service.change('pause', context=USER)
        resumed = await runtime.service.change('resume', context=USER)
        epoch = await original_wake_state(goal.id)
        assert epoch['epoch'] == resumed.version and epoch['epoch_time'] > fact['settled_at']
        await runtime.flush()
        release.set()
        await asyncio.wait_for(pending, 2)
        await asyncio.wait_for(runtime.flush(), 2)
        current = await runtime.service.current()
        assert current == resumed, 'A pre-resume failure fact mutated the newly authorized Goal epoch'
        await runtime._available()
        await runtime.flush()
        assert len(sent) == 1 and sent[0].provenance['goal_version'] == resumed.version
        assert not runtime._job_errors
    finally:
        release.set()
        if pending is not None:
            await asyncio.gather(pending, return_exceptions=True)
        await close(runtime, parent)


@pytest.mark.parametrize('fence', ['user', 'host', 'paused', 'retired'])
async def test_invalidated_wake_does_not_cross_a_protected_fence(tmp_path, monkeypatch, fence):
    runtime, parent, host, state, sent, _, _ = await setup(tmp_path, monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()
    pending = None
    original = host.acp_target
    original_valid = host.valid

    async def target():
        if asyncio.current_task() is pending:
            entered.set()
            await release.wait()
        return await original()

    try:
        await waiting_with_deferred_task(runtime, state)
        monkeypatch.setattr(host, 'acp_target', target)
        pending = asyncio.create_task(runtime._wake(None))
        await asyncio.wait_for(entered.wait(), 2)
        if fence == 'retired':
            host.valid = lambda: False
        elif fence == 'paused':
            await runtime.service.change('pause', context=USER)
        else:
            await wait_goal(runtime.service, until=fence,
            **({'question': 'Which delivery target?', 'impact': 'Final target unknown',
                'resume_when': 'User selects target'} if fence == 'user' else {}))
        await runtime.flush()
        release.set()
        if fence == 'retired':
            with pytest.raises(OwnerRetired):
                await asyncio.wait_for(pending, 2)
        else:
            await asyncio.wait_for(pending, 2)
        await asyncio.wait_for(runtime.flush(), 2)
        assert not sent and not await runtime.service.store.reminders()
        assert not runtime._jobs and not runtime._job_errors
    finally:
        release.set()
        if pending is not None:
            await asyncio.gather(pending, return_exceptions=True)
        host.valid = original_valid
        await close(runtime, parent)


async def test_fast_task_revision_chain_retains_cause_and_uses_current_version(tmp_path, monkeypatch):
    runtime, parent, host, state, sent, _, _ = await setup(tmp_path, monkeypatch)
    await receipt_states(monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()
    pending = None
    original = runtime.service.store.signal

    async def signal(goal, trigger):
        if trigger == 'tasks:fast-task':
            entered.set()
            await release.wait()
        return await original(goal, trigger)

    try:
        goal = await waiting_with_deferred_task(runtime, state)
        state['state'] = 'running'
        monkeypatch.setattr(runtime.service.store, 'signal', signal)
        pending = asyncio.create_task(runtime._task_changed(goal.id, SimpleNamespace(id='fast-task')))
        await asyncio.wait_for(entered.wait(), 2)
        for index in range(3):
            await runtime.service.plan(f'Concurrent revision {index}')
        latest = await runtime.service.current()
        release.set()
        await asyncio.wait_for(pending, 2)
        await runtime.flush()
        assert not sent
        assert 'tasks:fast-task' in (await runtime.service.store.wake_state(goal.id))['causes']
        state['state'] = 'idle'
        await runtime._available()
        await asyncio.wait_for(runtime.flush(), 2)
        assert len(sent) == 1 and sent[0].provenance['goal_version'] == latest.version
        assert 'tasks:fast-task' in sent[0].provenance['triggers']
    finally:
        release.set()
        if pending is not None:
            await asyncio.gather(pending, return_exceptions=True)
        await close(runtime, parent)


@pytest.mark.parametrize('receipt', ['pending', 'succeeded'])
async def test_active_revision_recheck_preserves_receipt_backpressure_and_coalescing(tmp_path, monkeypatch, receipt):
    runtime, parent, host, state, sent, _, _ = await setup(tmp_path, monkeypatch)
    facts = await receipt_states(monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()
    pending = None
    original = host.acp_target

    async def target():
        if asyncio.current_task() is pending:
            entered.set()
            await release.wait()
        return await original()

    try:
        goal = await runtime.service.create('Active', 'One unconsumed task cause, one reminder', context=USER)
        await runtime.flush()
        await runtime._wake('execution:first')
        first = sent[0]
        facts[first.id] = receipt
        state['state'] = 'running'
        await runtime._task_changed(goal.id, SimpleNamespace(id='active-task'))
        await runtime.flush()
        state['state'] = 'idle'
        monkeypatch.setattr(host, 'acp_target', target)
        pending = asyncio.create_task(runtime._wake(None))
        await asyncio.wait_for(entered.wait(), 2)
        updated = await runtime.service.plan('Still active, same continuation epoch')
        await runtime.flush()
        release.set()
        await asyncio.wait_for(pending, 2)
        await asyncio.wait_for(runtime.flush(), 2)
        assert len(sent) == (1 if receipt == 'pending' else 2)
        assert not runtime._jobs and not runtime._job_errors
        facts[first.id] = 'succeeded'
        await runtime._available()
        await runtime.flush()
        assert len(sent) == 2
        assert sent[1].provenance['goal_version'] == updated.version
        assert 'tasks:active-task' in sent[1].provenance['triggers']
        # Repeated checks and further ordinary changes cannot manufacture causes.
        for index in range(3):
            await runtime.service.plan(f'No new task or execution cause {index}')
            await runtime._available()
            await asyncio.wait_for(runtime.flush(), 2)
        assert len(sent) == 2 and not runtime._jobs and not runtime._job_errors
    finally:
        release.set()
        if pending is not None:
            await asyncio.gather(pending, return_exceptions=True)
        await close(runtime, parent)
