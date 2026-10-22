"""Resuming the actual owner restores capabilities without an extra model turn."""
from __future__ import annotations

import asyncio

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.agent.skills import SkillManager
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.sessions.runtime import SessionRuntime
from xdog.skills.goal.host import OperationContext

from packages.coding.tests.test_child_agents import create


@pytest.mark.parametrize('state', ['active', 'tasks', 'user', 'paused', 'completed'])
async def test_resumed_owner_opens_existing_goal_before_first_new_input(tmp_path, monkeypatch, state):
    from xdog.skills.goal.automation import TaskObserver
    from xdog.skills.goal.runtime import GoalRuntime

    observations = []

    async def start(self, target, task_dir, config_path, goal_id, on_change=None):
        observations.append((goal_id, target.copy()))
        return {'status': 'watching', 'registration_id': 'test-owned-registration'}

    async def stop(self, *, suspend=True):
        pass

    async def wake(self, cause):
        # This test concerns owner startup, not delivery. Never start a model or
        # touch a real receiver while validating restoration of subscriptions.
        pass

    monkeypatch.setattr(TaskObserver, 'start', start)
    monkeypatch.setattr(TaskObserver, 'aclose', stop)
    monkeypatch.setattr(GoalRuntime, '_wake', wake)
    first, _ = create(tmp_path, 'allow-all', skills=SkillManager(tmp_path / 'skills'))
    await first.send_message('Initialize original owner')
    original = first.capabilities.get('goal')

    async def seed():
        goal = await original.service.create('Persisted', 'Restore original owner',
                                             context=OperationContext(True))
        if state in {'tasks', 'user'}:
            fields = ({'question': 'Which output?', 'impact': 'Select output', 'resume_when': 'User answers'}
                      if state == 'user' else {})
            await original.service.change('wait', until=state, reason='Waiting',
                                           context=OperationContext(True), **fields)
        elif state == 'paused':
            await original.service.change('pause', reason='User pause', context=OperationContext(True))
        elif state == 'completed':
            await original.service.change('complete', summary='Verified', context=OperationContext(True))
        return goal.id, (await original.service.current()).version

    identifier, version = await asyncio.wrap_future(first._sdk_controller.control(seed, name='seed-goal'))
    await first.aclose()
    observations.clear()
    data = await asyncio.to_thread(first.session_manager.load_session, first.session_id)

    def no_model(*args, **kwargs):
        pytest.fail('Resuming a capability must not itself run a model')

    restored = AgentSession(agent=Agent(no_model, config=AgentConfig(model='test/model'), skills=first.agent._skills),
        session_data=data, session_manager=first.session_manager, settings=first.settings,
        tool_registry=None, bash=None, working_dir=tmp_path, permissions=first.permissions)
    restored.agent.replace_messages(data.messages)
    runtime = SessionRuntime(restored, bound=True)
    runtime.start(setup_pending=True)
    try:
        assert restored.capabilities.get('goal') is None
        await runtime.activate_inputs()
        recovered = restored.capabilities.get('goal')
        assert recovered is not None and recovered is not original
        assert recovered.owner == original.owner
        current = await recovered.service.current()
        assert current.id == identifier and current.version == version
        assert current.status == ('waiting' if state in {'tasks', 'user'} else state)
        assert current.wait_for == (state if state in {'tasks', 'user'} else '')
        assert len(observations) == (1 if state in {'active', 'tasks'} else 0)
        if observations:
            assert observations[0][1]['session_id'] == first.session_id
        assert restored.messages == data.messages
        assert any(tool.name == 'goal' for tool in restored.agent.active_tools)
    finally:
        await restored.aclose()


@pytest.mark.parametrize('operation', ['navigate', 'close'])
async def test_owner_retirement_during_capability_open_never_publishes_stale_parent(tmp_path, monkeypatch, operation):
    from xdog.coding.core.capability_ports import SessionCapabilityHost

    session, calls = create(tmp_path)
    runtime = SessionRuntime(session, bound=True)
    runtime.start(setup_pending=True)
    entered, release = asyncio.Event(), asyncio.Event()
    original = SessionCapabilityHost.open
    openings = 0

    async def delayed(cls, value):
        nonlocal openings
        openings += 1
        if openings == 1:
            entered.set()
            await release.wait()
        return await original(value)

    monkeypatch.setattr(SessionCapabilityHost, 'open', classmethod(delayed))
    activating = asyncio.create_task(runtime.activate_inputs())
    await asyncio.wait_for(entered.wait(), 3)
    old = session.capabilities
    try:
        if operation == 'navigate':
            async def navigate():
                return await asyncio.to_thread(session.new_session)
            changing = asyncio.wrap_future(runtime.controller.control(navigate, name='startup-navigation'))
            # Startup uses the existing serialized control route: navigation
            # waits for its original startup rather than racing publication.
            await asyncio.sleep(.03)
            assert session.capabilities is old and not changing.done()
            release.set()
            await asyncio.wait_for(changing, 5)
            current = session.capabilities.get('goal')
            assert current is not None and current.host.identity == session.session_id
            assert await current.service.current() is None
        else:
            closing = asyncio.wrap_future(runtime.close())
            await asyncio.sleep(.02)
            release.set()
            await asyncio.wait_for(closing, 5)
        assert not calls
        assert old.get('goal') is None
    finally:
        release.set()
        # An activation fenced by navigation/close may fail its old intake check;
        # it must not poison the successor or make normal shutdown fail.
        await asyncio.gather(activating, return_exceptions=True)
        await session.aclose()
