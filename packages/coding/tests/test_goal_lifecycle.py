"""Goal lifetime follows the exact Coding owner, not a UI, role loop or ephemeral caller."""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import threading

import pytest
from xdog.agent.skills import SkillRepo
from xdog.coding.core.slash_commands import execute_command
from xdog.skills.goal.host import OperationContext

from packages.coding.tests.test_child_agents import create
from packages.coding.tests.test_goal_adapter import owned, scripted


def enabled(tmp_path, *, mode='ask'):
    session, _ = create(tmp_path, mode)
    contexts = []
    session.agent.set_stream_fn(scripted(contexts))
    return session, contexts


async def dispose(session):
    await session.aclose()


async def test_module_import_is_inert_and_no_discovered_provider_opens_no_goal_storage(tmp_path, monkeypatch):
    result = subprocess.run([sys.executable, '-c',
        'import sys; import xdog.coding.core.agent_session; assert not any('
        'n=="xdog.skills.goal" or n.startswith("xdog.skills.goal.") for n in sys.modules)'],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    monkeypatch.setattr('xdog.agent.capabilities.runtime.discover', lambda scope: ())
    session, _ = create(tmp_path)
    try:
        await session.send_message('ordinary')
        assert session.capabilities.get('goal') is None and not (tmp_path / 'goals').exists()
    finally:
        await dispose(session)


async def test_default_sdk_goal_has_existing_owner_loop_but_no_implicit_receiver_or_worker(tmp_path):
    session, calls = enabled(tmp_path)
    try:
        await session.send_message('Ready')
        host, controller = session.capabilities.get('goal'), session._sdk_controller
        assert host is not None and host.loop is controller._loop and host.loop is not asyncio.get_running_loop()
        goal = await owned(controller, lambda: host.service.create('Output', 'Main verifies',
                                                                    context=OperationContext(True)))
        await owned(controller, lambda: host.service.task({'id': 'work', 'title': 'Main work', 'expected_version': 0}))
        await owned(controller, host.flush)
        assert await owned(controller, host.host.acp_target) is None
        assert host.automation['status'] == 'unavailable'
        count = len(calls)
        await asyncio.sleep(.01)
        assert len(calls) == count and not session.children.runtime._children
        assert (await owned(controller, host.service.current)).id == goal.id
    finally:
        await dispose(session)


async def test_idle_navigation_retires_original_capability_without_transferring_registry(tmp_path):
    from xdog.skills.goal.store import GoalStore
    session, _ = enabled(tmp_path)
    try:
        await session.send_message('Ready')
        old, controller = session.capabilities.get('goal'), session._sdk_controller
        goal = await owned(controller, lambda: old.service.create('Private', 'Original exact owner',
                                                                 context=OperationContext(True)))
        tool = old.tools[0]
        await asyncio.to_thread(session.new_session)
        # Owner activation now eagerly restores installed capabilities. It must
        # create a fresh empty binding, never transfer the previous Goal.
        restored = session.capabilities.get('goal')
        if restored is not None:
            assert restored.owner != old.owner
            assert await owned(session._sdk_controller, restored.service.current) is None
        await session.send_message('New owner')
        current = session.capabilities.get('goal')
        assert current.owner != old.service.store.owner
        assert await owned(session._sdk_controller, current.service.current) is None
        assert (await tool.execute('late', {'action': 'status'})).is_error
        with pytest.raises(ValueError, match='owner|closed|retired'):
            await old.service.current()
        saved = await GoalStore.open(old.service.store.path, old.service.store.owner)
        try:
            assert (await saved.get(goal.id)).title == 'Private'
        finally:
            await saved.close()
    finally:
        await dispose(session)


async def test_native_goal_command_uses_user_authority_and_current_goal_pause_clear_lifecycle(tmp_path):
    session, calls = enabled(tmp_path)
    try:
        result = await execute_command('goal', 'create Inspect actual main output', session)
        assert 'Inspect actual main output' in result.output
        controller, host = session._sdk_controller, session.capabilities.get('goal')
        assert not calls and not session._user_operation_authorized
        assert session.extensions.command_owner('goal') == 'goal'
        await execute_command('goal', 'pause User pause', session)
        assert (await owned(controller, host.service.current)).status == 'paused'
        paused = json.loads((await execute_command('goal', 'status --json', session)).output)
        assert paused['last_transition']['origin'] == 'native_command'
        assert paused['last_transition']['command'] == '/goal pause'
        assert paused['last_transition']['actor'] == 'user'
        await execute_command('goal', 'resume', session)
        resumed = json.loads((await execute_command('goal', 'status --json', session)).output)
        assert resumed['last_transition']['command'] == '/goal resume'
        assert resumed['last_transition']['from'] == {'status': 'paused', 'wait_for': ''}
        assert resumed['last_transition']['to'] == {'status': 'active', 'wait_for': ''}
        assert resumed['last_transition']['user_authorized'] is True
        assert not session._user_operation_authorized
        await execute_command('goal', 'pause User pause', session)
        with pytest.raises(ValueError, match='current Goal'):
            await execute_command('goal', 'create Cannot replace paused target', session)
        await execute_command('goal', 'clear User archive', session)
        assert await owned(controller, host.service.current) is None
        assert len(await owned(controller, host.service.list)) == 1
        assert not session.children.runtime._children
    finally:
        await dispose(session)


async def test_shutdown_releases_registry_and_reopen_retains_main_tasks_without_replay(tmp_path):
    from xdog.agent import Agent, AgentConfig
    from xdog.coding.core.agent_session import AgentSession
    from xdog.coding.core.permissions import PermissionManager
    from xdog.coding.core.session_manager import SessionManager
    from xdog.coding.core.settings_manager import SettingsManager
    from xdog.coding.core.tools import get_default_tools
    session, _ = enabled(tmp_path)
    successor = None
    try:
        await session.send_message('Ready')
        host, controller = session.capabilities.get('goal'), session._sdk_controller
        goal = await owned(controller, lambda: host.service.create('Pending', 'Keep main task',
                                                                    context=OperationContext(True)))
        await owned(controller, lambda: host.service.task({'id': 'work', 'title': 'No worker', 'expected_version': 0}))
        identifier = session.session_id
        await dispose(session)
        assert host.loop.is_closed() and session._lease is None
        manager, calls = SessionManager(tmp_path / 'sessions'), []
        successor = AgentSession(agent=Agent(scripted(calls),
                                             skills=SkillRepo().register(directory=tmp_path / 'skills', name="shared"),
                                             config=AgentConfig(model='test/model'),
            tools=get_default_tools(tmp_path), tool_ctx={'fs_workspace': str(tmp_path)}),
            session_data=manager.load_session(identifier), session_manager=manager,
            settings=SettingsManager(global_path=tmp_path / 'settings.json'), tool_repo=None, bash=None,
            working_dir=tmp_path, permissions=PermissionManager('ask'))
        await successor.send_message('Read current state')
        next_host = successor.capabilities.get('goal')
        saved = await owned(successor._sdk_controller, lambda: next_host.service.status(goal.id))
        assert saved['current']['status'] == 'active' and saved['tasks'][0]['worker'] == 'main'
        assert saved['tasks'][0]['status'] == 'pending' and len(calls) == 1
        assert not successor.children.runtime._children
    finally:
        if successor is not None:
            await dispose(successor)
        await dispose(session)


async def test_native_goal_status_pause_clear_with_real_child_preserves_worker_extension_guard(tmp_path):
    from xdog.ai.utils.event_stream import EventStream
    session, calls = enabled(tmp_path, mode='allow-all')
    release, entered = threading.Event(), threading.Event()
    child = None
    try:
        await execute_command('goal', 'create Main target', session)
        controller, host = session._sdk_controller, session.capabilities.get('goal')
        original = scripted(calls)
        def stream(*args):
            source = original(*args)
            async def events():
                entered.set()
                while not release.is_set():
                    await asyncio.sleep(.001)
                async for event in source:
                    yield event
            return EventStream.from_async_generator(events(), result_future=source._result_future)
        session.agent.set_stream_fn(stream)
        child = await owned(controller, lambda: session.children.runtime.start('ordinary optional child'))
        assert await asyncio.to_thread(entered.wait, 3)
        assert session.children.runtime.active
        output = (await execute_command('goal', 'status', session)).output
        assert 'Goal: Main target' in output and 'Status: active' in output
        raw = (await execute_command('goal', 'status --json', session)).output
        assert json.loads(raw)['current']['title'] == 'Main target'
        await execute_command('goal', 'pause User pause', session)
        assert (await owned(controller, host.service.current)).status == 'paused'
        await execute_command('goal', 'clear Archive main target', session)
        assert await owned(controller, host.service.current) is None and session.children.runtime.active
        with pytest.raises(RuntimeError, match='Child work'):
            await owned(controller, lambda: session.extension_command('extensions', 'list'))
        assert not session._user_operation_authorized
    finally:
        release.set()
        if child is not None:
            await session.children.runtime.control('wait', child)
        await dispose(session)


async def test_goal_tool_and_commands_are_available_without_default_skill_activation(tmp_path):
    from xdog.ai.types import system_prompt_text

    session, contexts = enabled(tmp_path)
    # Discovery and tool registration must not implicitly activate skill bodies.
    assert isinstance(session.agent._skills, SkillRepo)
    skill = session.agent._skills.load_skill('goal')
    assert skill is not None and skill.content.strip()
    try:
        assert not session.capabilities.default_skills
        await session.send_message('Can you create a goal and test its full loop?')
        assert contexts
        prompt = system_prompt_text(contexts[0].system_prompt) or ''
        assert '# Goal: main-Agent-owned work' not in prompt
        assert 'goal' not in session.agent.active_skills and 'goal' not in session.active_skills
        assert not session.capabilities.default_skills
        assert 'goal' in session.capabilities.declared_skills
        assert any(tool.name == 'goal' for tool in session.agent.active_tools)
        assert 'goal' not in session._active_skills  # never pretend this was user activation/authority
        assert 'The user activated these' not in prompt
        assert not session.children.runtime._children
        binding = session.capabilities.get('goal')
        assert await owned(session._sdk_controller, binding.service.current) is None
        assert len(contexts) == 1  # loading instructions does not create a Goal or start another Agent
        await asyncio.to_thread(session.activate_skill, 'goal')
        await session.send_message('Continue ordinary chat')
        next_prompt = system_prompt_text(contexts[-1].system_prompt) or ''
        assert next_prompt.count('# Goal: main-Agent-owned work') == 1
    finally:
        await dispose(session)
