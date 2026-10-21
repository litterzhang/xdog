"""Native extension commands, actual object binding and signal-driven ACP policy."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from xdog.agent import Agent
from xdog.agent.capabilities.runtime import ParentPort
from xdog.agent.extensions.runtime import ExtensionRuntime
from xdog.skills.goal.host import OperationContext
from xdog.skills.goal.runtime import GoalRuntime


class Observer:
    def __init__(self):
        self.closed = False

    async def start(self, *args):
        return {'status': 'watching'}

    async def aclose(self):
        self.closed = True


async def setup(tmp_path, monkeypatch):
    import xdog.agent.acp.client as client
    import xdog.skills.goal.automation as automation
    monkeypatch.setattr(automation, 'TaskObserver', Observer)
    sent = []
    async def enqueue(target, message):
        sent.append(message)
        return {'status': 'queued'}
    monkeypatch.setattr(client, 'enqueue', enqueue)
    listeners = []
    state = {'state': 'idle', 'user_pending': False, 'children_active': False, 'generation': 'one'}
    host = SimpleNamespace(scope='group', owner_prefix='host', identity='main', namespace='original', branch='',
        workspace=tmp_path, storage_root=tmp_path / 'data', valid=lambda: True, accepting=lambda: True,
        execution_snapshot=lambda: dict(state),
        acp_target=AsyncMock(return_value={'endpoint': str(tmp_path / 'private.sock'), 'session_id': 'group:main',
                                         'cwd': str(tmp_path), 'inbox_id': 'original', 'branch_id': ''}))
    def subscribe(callback):
        listeners.append(callback)
        return lambda: listeners.remove(callback)
    host.subscribe_events = subscribe
    runtime = await GoalRuntime.open(host)
    def forbidden_model(*args, **kwargs):
        raise AssertionError('Goal must not call a model')
    extensions = ExtensionRuntime((), tmp_path / 'extensions', tmp_path)
    extensions.reserve_commands({'help'}, skill_commands={'goal'})
    authorized = [True]
    parent = ParentPort(Agent(forbidden_model), extensions, SimpleNamespace(), lambda: True, lambda: authorized[0])
    host.owns_parent = lambda selected: selected.agent is parent.agent
    runtime.attach(parent)
    return runtime, parent, host, state, sent, authorized, listeners


async def close(runtime, parent):
    await runtime.aclose()
    await parent.extensions.aclose()
    await parent.agent.aclose()


async def test_default_native_commands_do_not_require_a_worker_or_start_a_model(tmp_path, monkeypatch):
    runtime, parent, host, state, sent, authorized, listeners = await setup(tmp_path, monkeypatch)
    try:
        assert parent.extensions.command_owner('goal') == 'goal'
        assert parent.extensions.enabled == ()
        await parent.extensions.command('goal', 'create Work from the original context')
        await runtime.flush()
        assert len(sent) == 1
        assert await runtime.service.current() is not None
        await parent.extensions.command('goal', 'pause user requested')
        assert runtime._observer is None
        listeners[0]({'type': 'execution.settled', 'execution_id': 'paused', 'status': 'succeeded'})
        await runtime.flush()
        assert len(sent) == 1
        authorized[0] = False
        with pytest.raises(ValueError, match='user operation'):
            await parent.extensions.command('goal', 'resume')
        authorized[0] = True
        await parent.extensions.command('goal', 'clear New task instead')
        assert await runtime.service.current() is None
        assert 'cleared' in await parent.extensions.command('goal', 'history')
    finally:
        await close(runtime, parent)
    assert not listeners


@pytest.mark.parametrize('busy', ['state', 'user_pending', 'children_active'])
async def test_busy_wakeup_is_deferred_until_owner_available_and_events_are_idempotent(tmp_path, monkeypatch, busy):
    runtime, parent, host, state, sent, authorized, listeners = await setup(tmp_path, monkeypatch)
    try:
        await runtime.service.create('One', 'Only reminders', context=OperationContext(True))
        state[busy] = 'running' if busy == 'state' else True
        event = {'type': 'execution.settled', 'execution_id': 'one', 'status': 'succeeded'}
        listeners[0](event)
        await runtime.flush()
        assert not sent
        state[busy] = 'idle' if busy == 'state' else False
        listeners[0]({'type': 'owner.available'})
        await runtime.flush()
        assert len(sent) == 1
        listeners[0](event)
        listeners[0]({'type': 'owner.available'})
        await runtime.flush()
        assert len(sent) == 1
        await runtime.service.change('complete', summary='Main says all criteria checked')
        listeners[0]({'type': 'execution.settled', 'execution_id': 'two', 'status': 'succeeded'})
        await runtime.flush()
        assert len(sent) == 1
    finally:
        await close(runtime, parent)


async def test_initial_command_wakeup_survives_control_lock_and_old_parent_tool_is_revoked(tmp_path, monkeypatch):
    runtime, parent, host, state, sent, authorized, listeners = await setup(tmp_path, monkeypatch)
    old_tool = runtime.tools[0]
    try:
        state['state'] = 'running'
        await parent.extensions.command('goal', 'create Initial planning')
        await runtime.flush()
        assert not sent
        state['state'] = 'idle'
        listeners[0]({'type': 'owner.available'})
        await runtime.flush()
        assert len(sent) == 1
        successor = ParentPort(Agent(lambda *args: None), ExtensionRuntime((), tmp_path / 'new-ext', tmp_path),
                               SimpleNamespace(), lambda: True, lambda: True)
        host.owns_parent = lambda selected: selected.agent is successor.agent
        runtime.attach(successor)
        assert (await old_tool.execute('old', {'action': 'status'})).is_error
        assert 'goal' not in parent.extensions.commands()
        await successor.extensions.aclose()
        await successor.agent.aclose()
    finally:
        await close(runtime, parent)


@pytest.mark.parametrize('status', ['failed', 'cancelled', 'interrupted'])
async def test_execution_failure_waits_for_user_instead_of_replaying(tmp_path, monkeypatch, status):
    runtime, parent, host, state, sent, authorized, listeners = await setup(tmp_path, monkeypatch)
    try:
        await runtime.service.create('One', 'Inspect failure', context=OperationContext(True))
        listeners[0]({'type': 'execution.settled', 'execution_id': 'failure', 'status': status})
        await runtime.flush()
        goal = await runtime.service.current()
        assert goal.status == 'waiting' and goal.wait_for == 'host' and not sent
        listeners[0]({'type': 'owner.available'})
        await runtime.flush()
        assert not sent
    finally:
        await close(runtime, parent)


async def test_unknown_acp_outcome_stops_automatic_reminders_until_user_inspection(tmp_path, monkeypatch):
    import xdog.agent.acp.client as client
    runtime, parent, host, state, sent, authorized, listeners = await setup(tmp_path, monkeypatch)
    unknown = AsyncMock(return_value={'status': 'outcome-unknown', 'error': 'ACK unavailable'})
    monkeypatch.setattr(client, 'enqueue', unknown)
    try:
        await runtime.service.create('One', 'Inspect uncertainty', context=OperationContext(True))
        listeners[0]({'type': 'execution.settled', 'execution_id': 'first', 'status': 'succeeded'})
        await runtime.flush()
        goal = await runtime.service.current()
        assert goal.status == 'waiting' and goal.wait_for == 'host'
        assert runtime.delivery['status'] == 'unknown'
        assert runtime.automation['status'] == 'idle'  # Transport facts are not observer health.
        assert (await runtime.service.store.reminders())[0]['status'] == 'unknown'
        listeners[0]({'type': 'owner.available'})
        listeners[0]({'type': 'execution.settled', 'execution_id': 'second', 'status': 'succeeded'})
        await runtime.flush()
        assert unknown.await_count == 1
    finally:
        await close(runtime, parent)
