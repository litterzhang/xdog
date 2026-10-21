"""Wheel-only default Coding Goal: actual native commands, model tools and owner fences."""
from __future__ import annotations

import asyncio
import hashlib
import importlib.metadata as metadata
import json
import sys
from pathlib import Path

from xdog.agent import Agent, AgentConfig
from xdog.agent.skills import SkillManager
from xdog.ai.types import AssistantMessage, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.core import agent_session as installed_coding
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.permissions import PermissionManager
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.settings_manager import SettingsManager
from xdog.coding.core.slash_commands import execute_command
from xdog.coding.core.tools import get_default_tools


class Model:
    def __init__(self):
        self.calls = []
        self.actions = ()

    def __call__(self, selected, context, options):
        self.calls.append(context)
        assert not {'task_report', 'goal_context'}.intersection(t.name for t in context.tools)
        start = max((i for i, m in enumerate(context.messages) if m.role == 'user'), default=-1)
        turn = sum(m.role == 'assistant' for m in context.messages[start + 1:])
        parts = ((ToolCall(id='actual-' + str(turn), name=self.actions[turn][0], arguments=self.actions[turn][1]),)
                 if turn < len(self.actions) else (TextContent(text='main Agent verified'),))
        return EventStream.empty(AssistantMessage(content=parts,
            stop_reason='toolUse' if isinstance(parts[0], ToolCall) else 'stop'))


async def owned(session, operation):
    return await asyncio.wrap_future(session._sdk_controller.control(operation, name='wheel-owner'))


async def main():
    assert Path(installed_coding.__file__).is_relative_to(Path(sys.prefix))
    names = {dist.metadata['Name'].lower().replace('_', '-') for dist in metadata.distributions()}
    assert 'xdog-skills' in names
    assert not names.intersection({'xdog-claw', 'xdog-eyes', 'xdog-flow', 'xdog-site'})
    root, base_only = Path.cwd(), '--base-only' in sys.argv
    root.joinpath('result.txt').write_text('real installed result')
    skills = SkillManager(root / 'not-created-skills')
    assert skills.load_skill('goal').scope == 'session'
    if not base_only:
        root.joinpath('settings.json').write_text(json.dumps({
            'extensions': ['goal_ui'], 'extension_dir': str(root / 'extensions')}))
    manager, model = SessionManager(root / 'sessions'), Model()
    session = AgentSession(agent=Agent(model, config=AgentConfig(model='test/installed'),
        tools=get_default_tools(root), tool_ctx={'fs_workspace': str(root)}, skills=skills),
        session_data=manager.create_session(model='test/installed', working_dir=root), session_manager=manager,
        settings=SettingsManager(global_path=root / 'settings.json'), tool_registry=None, bash=None,
        working_dir=root, permissions=PermissionManager('allow-all'))
    try:
        assert session.capabilities.get('goal') is None
        # The very first operation is a real slash route, not a model warm-up.
        result = await execute_command('goal', 'status', session)
        host = session.capabilities.get('goal')
        assert 'current' in result.output and host is not None and not model.calls
        assert session.extensions.is_native_command('goal') and session.extensions.command_owner('goal') == 'goal'
        assert not session._user_operation_authorized
        assert await owned(session, host.service.current) is None
        assert await owned(session, host.host.acp_target) is None  # Bare SDK never constructs a receiver.
        if base_only:
            await session.send_message('Ordinary default chat')
            assert len(model.calls) == 1 and not session.children.supervisor._children
            print(json.dumps({'installed_coding_default': 'first native /goal status; ordinary chat; no Goal created',
                              'no_eyes': True, 'implicit_receiver': False}))
            return
        from xdog.agent.extensions.discovery import extension_directory
        from xdog.tui.components.extension_view import ExtensionView
        resource = extension_directory('goal_ui', root / 'extensions')
        assert resource is not None and resource.is_relative_to(Path(sys.prefix))
        ui_hash = hashlib.sha256(resource.joinpath('extension.yaml').read_bytes() + b'\0'
                                 + resource.joinpath('main.py').read_bytes()).hexdigest()
        model.actions = (('goal', {'action': 'create', 'title': 'Output', 'outcome': 'Inspect actual result'}),
            ('goal', {'action': 'report', 'report': {'expected_version': 0, 'progress': 'baseline',
                'summary': 'Initial verified state before working', 'completed': [], 'evidence': ['result.txt'],
                'blockers': [], 'next_steps': ['Read actual result and update task']}}),
            ('goal', {'action': 'plan', 'content': 'Read result.txt; record main Agent evidence'}),
            ('filesystem', {'action': 'read', 'path': 'result.txt'}),
            ('goal', {'action': 'task_update', 'task': {'id': 'inspect', 'title': 'Read result',
                                                      'expected_version': 0}}))
        await session.send_message('Create this Goal and record the main plan')
        goal = await owned(session, host.service.current)
        assert goal is not None and goal.title == 'Output'
        status = await owned(session, host.service.status)
        assert status['tasks'][0]['worker'] == 'main' and status['plan'].startswith('Read result.txt')
        old_tool = host.tools[0]
        assert any(old_tool is value for value in session.agent.state.tools)
        before = len(model.calls)
        output = await owned(session, lambda: session.extensions.command('goals', ''))
        assert goal.id in str(output) and 'Output' in str(output) and len(model.calls) == before
        assert any('Goals: 1' in line for line in ExtensionView(lambda: session.extensions.snapshot()).render(80))
        await owned(session, lambda: session.extensions.manage('unload', 'goal_ui'))
        assert not session.extensions.snapshot()['ui'] and not session.children.supervisor._children
        model.actions = (('goal', {'action': 'report', 'report': {'expected_version': 1, 'progress': 'advanced',
            'summary': 'Read actual output since the baseline', 'completed': ['Read result.txt'],
            'evidence': ['result.txt'], 'blockers': [], 'next_steps': ['Commit task evidence and complete']}}),
            ('goal', {'action': 'task_update', 'task': {'id': 'inspect', 'expected_version': 1,
            'status': 'completed', 'summary': 'Read actual result', 'evidence': ['result.txt']}}),
            ('goal', {'action': 'complete', 'summary': 'Main Agent verified result.txt'}))
        await session.send_message('Finish the verified main task')
        completed = await owned(session, lambda: host.service.status(goal.id))
        assert completed['current']['status'] == 'completed' and completed['tasks'][0]['status'] == 'completed'
        summary = await owned(session, lambda: host.service.summary(goal.id))
        assert summary['latest_report']['version'] == 2 and summary['latest_report']['progress'] == 'advanced'
        assert 'plan' not in summary and 'tasks' not in summary
        assert Path(summary['report_path']).read_text().count('```json goal-report-v1') == 2
        assert await owned(session, host.service.current) is None and not session.children.supervisor._children
        # Both ask and read-only keep original pre-tool permission hooks.
        model.actions = (('filesystem', {'action': 'write', 'path': 'forbidden.txt', 'content': 'not authorized'}),)
        session.permissions = PermissionManager('ask')
        await session.send_message('Read-only inspection, no arbitrary write approval')
        assert not root.joinpath('forbidden.txt').exists()
        session.permissions = PermissionManager('allow-all')
        await asyncio.to_thread(session.access.set_read_only, True)
        await session.send_message('Inspect without mutation')
        assert not root.joinpath('forbidden.txt').exists()
        await asyncio.to_thread(session.access.set_read_only, False)
        model.actions = ()
        await owned(session, lambda: session.extensions.manage('reload', 'goal_ui'))
        output = await owned(session, lambda: session.extensions.command('goals', ''))
        assert goal.id in str(output) and 'completed' in str(output)
        previous = session.extensions
        await asyncio.to_thread(session.new_session)
        assert previous._closed and 'Output' not in str(session.extensions.snapshot())
        assert (await old_tool.execute('stale', {'action': 'status'})).is_error
        await execute_command('goal', 'status', session)
        fresh = session.capabilities.get('goal')
        assert fresh.owner != host.owner and await owned(session, fresh.service.current) is None
        assert await previous.views.read('goal_ui', 'goals', {}) is None
        assert not root.joinpath('not-created-skills').exists()
        assert not any(n.startswith(('xdog.claw', 'xdog.eyes', 'xdog.flow', 'xdog.site')) for n in sys.modules)
        print(json.dumps({'shared_goal_ui_sha256': ui_hash,
            'installed_coding_lifecycle': 'main model create/plan/read/task_update/complete through actual AgentTool',
            'goal_status': 'completed', 'children_started': 0, 'ui_loss_does_not_own_progress': True,
            'permission_modes': ['ask', 'readonly'], 'forbidden_write': False,
            'ui_owner_rotation': 'old exact owner unavailable; new owner genuinely empty',
            'main_model_calls': len(model.calls), 'implicit_receiver': False}))
    finally:
        await session.aclose()


if __name__ == '__main__':
    asyncio.run(main())
