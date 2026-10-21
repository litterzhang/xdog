"""Waiting is condition-based; paused is explicit; completion remains visible."""
import json
from types import SimpleNamespace

import pytest
from xdog.skills.goal.host import OperationContext
from xdog.skills.goal.service import GoalService
from xdog.skills.goal.store import GoalStore

from packages.skills.tests.goal.test_runtime import close, setup
from packages.skills.tests.goal.test_service import USER, opened


async def test_task_wait_activate_is_agent_chosen_versioned_and_idempotent(tmp_path):
    service = await opened(tmp_path)
    try:
        goal = await service.create('Wait', 'Continue only when useful', context=USER)
        waiting = await service.change('wait', until='tasks', reason='Worker running')
        with pytest.raises(ValueError, match='Resume only'):
            await service.change('resume', context=USER)
        active = await service.change('activate', goal.id, version=waiting.version,
                                      reason='Read the assigned result; dependency satisfied',
                                      context=OperationContext(origin='model_tool'))
        assert active.status == 'active' and not active.wait_for
        fact = (await service.summary())['last_transition']
        assert fact['action'] == 'activate' and fact['actor'] == 'agent'
        assert fact['from'] == {'status': 'waiting', 'wait_for': 'tasks'}
        wake = await service.store.wake_state(goal.id)
        history = await service.store.history(goal.id)
        assert await service.change('activate', goal.id, version=waiting.version,
                                    reason='Retry the original acknowledged activation') == active
        assert await service.store.history(goal.id) == history
        assert await service.store.wake_state(goal.id) == wake
    finally:
        await service.close()


@pytest.mark.parametrize('until', ['user', 'host'])
async def test_automatic_task_reminder_cannot_release_user_or_host_wait(tmp_path, until):
    service = await opened(tmp_path)
    try:
        goal = await service.create('Wait', 'Real decision/recovery', context=USER)
        waiting = await service.change('wait', until=until)
        with pytest.raises(ValueError, match='user operation'):
            await service.change('activate', goal.id, version=waiting.version, reason='Untrusted event claims recovery')
        assert await service.current() == waiting
        before = await service.store.wake_state(goal.id)
        active = await service.change('activate', goal.id, version=waiting.version,
                                      reason='Actual user answered; main Agent checked the condition', context=USER)
        assert active.status == 'active'
        after = await service.store.wake_state(goal.id)
        assert after['sequence'] == before['sequence']  # Never creates another wake request.
        assert after['epoch'] == active.version  # Actual user recovery still fences old failure facts.
    finally:
        await service.close()


@pytest.mark.parametrize('boundary', ['paused', 'completed'])
async def test_activate_never_restores_pause_or_completed_even_with_user_authority(tmp_path, boundary):
    service = await opened(tmp_path)
    try:
        goal = await service.create('One', 'Protected', context=USER)
        stopped = await service.change('pause' if boundary == 'paused' else 'complete', context=USER,
                                       **({} if boundary == 'paused' else {'summary': 'Verified'}))
        with pytest.raises(ValueError):
            await service.change('activate', goal.id, version=stopped.version, reason='Not a resume', context=USER)
        assert await service.current() == stopped
        if boundary == 'paused':
            assert (await service.change('resume', context=USER)).status == 'active'
    finally:
        await service.close()


async def test_stale_activate_cannot_overwrite_a_new_wait_or_another_goal(tmp_path):
    service = await opened(tmp_path)
    try:
        goal = await service.create('One', 'Original wait', context=USER)
        waiting = await service.change('wait', until='tasks')
        newer = await service.change('wait', until='user', reason='Need a decision')
        with pytest.raises(ValueError, match='version'):
            await service.change('activate', goal.id, version=waiting.version, reason='Stale result', context=USER)
        assert await service.current() == newer
        await service.change('clear', context=USER)
        other = await service.create('Two', 'Different goal', context=USER)
        await service.change('wait', until='tasks')
        with pytest.raises(ValueError, match='matching current'):
            await service.change('activate', goal.id, version=2, reason='Late old call', context=USER)
        assert (await service.current()).id == other.id
    finally:
        await service.close()


async def test_completion_retains_current_across_reopen_and_create_preserves_completed_history(tmp_path):
    service = await opened(tmp_path)
    first = await service.create('One', 'Visible completion', context=USER)
    completed = await service.change('complete', summary='Evidence verified')
    path, owner = service.store.path, service.store.owner
    assert (await service.summary())['current']['status'] == 'completed'
    assert 'completed' in (await service.summary())['next']['reason']
    await service.close()
    service = GoalService(await GoalStore.open(path, owner))
    try:
        assert await service.current() == completed
        before = await service.store.history(first.id)
        for write in [lambda: service.plan('Cannot change completed plan'),
                      lambda: service.task({'id': 'extra', 'title': 'Late', 'expected_version': 0}),
                      lambda: service.change('update', context=USER, title='Overwrite')]:
            with pytest.raises(ValueError, match='read-only'):
                await write()
        second = await service.create('Two', 'New explicit target', context=USER)
        assert second.id != first.id and await service.current() == second
        assert (await service.store.get(first.id)).status == 'completed'
        assert await service.store.history(first.id) == before
    finally:
        await service.close()


async def test_only_explicit_clear_releases_completed_binding(tmp_path):
    service = await opened(tmp_path)
    try:
        await service.create('One', 'Completed but visible', context=USER)
        completed = await service.change('complete', summary='Finished')
        with pytest.raises(ValueError, match='user operation'):
            await service.change('clear')
        assert await service.current() == completed
        cleared = await service.change('clear', context=USER)
        assert cleared.status == 'cleared' and await service.current() is None
    finally:
        await service.close()


async def test_waiting_tasks_idle_is_not_a_new_wake_cause_and_reminder_does_not_activate(tmp_path, monkeypatch):
    runtime, parent, host, state, sent, authorized, listeners = await setup(tmp_path, monkeypatch)
    state['state'] = 'running'
    try:
        goal = await runtime.service.create('One', 'No busy loop', context=USER)
        waiting = await runtime.service.change('wait', until='tasks')
        await runtime.flush()
        state['state'] = 'idle'
        before = await runtime.service.store.wake_state(goal.id)
        for _ in range(5):
            listeners[0]({'type': 'owner.available'})
            await runtime.flush()
        assert not sent and await runtime.service.current() == waiting
        assert await runtime.service.store.wake_state(goal.id) == before
        await runtime._task_changed(goal.id, SimpleNamespace(id='real-change'))
        await runtime.flush()
        assert len(sent) == 1 and await runtime.service.current() == waiting
        assert 'already-loaded Goal skill' in sent[0].instruction
        assert 'Read the goal skill' not in sent[0].instruction
        result = await runtime.tools[0].execute('activate', {'action': 'activate', 'goal_id': goal.id,
            'version': waiting.version, 'reason': 'Checked task; wait is satisfied'})
        assert not result.is_error and json.loads(result.content[0].text)['status'] == 'active'
    finally:
        await close(runtime, parent)


async def test_completed_goal_stops_owned_observer_and_never_reminds_on_late_events(tmp_path, monkeypatch):
    runtime, parent, host, state, sent, authorized, listeners = await setup(tmp_path, monkeypatch)
    state['state'] = 'running'
    try:
        goal = await runtime.service.create('One', 'Done stays visible', context=USER)
        await runtime.flush()
        observer = runtime._observer
        await runtime.service.change('complete', summary='Main checked criteria')
        await runtime.flush()
        assert observer.closed and runtime._observer is None
        assert (await runtime.service.current()).status == 'completed'
        state['state'] = 'idle'
        listeners[0]({'type': 'execution.settled', 'execution_id': 'last', 'status': 'succeeded'})
        listeners[0]({'type': 'owner.available'})
        await runtime._task_changed(goal.id, SimpleNamespace(id='late-child-change'))
        await runtime.flush()
        assert not sent and not await runtime.service.store.reminders()
    finally:
        await close(runtime, parent)
