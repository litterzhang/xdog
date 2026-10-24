"""Wheel-only default Claw Goal: actual main Agent tools, native commands and stable group."""
from __future__ import annotations

import asyncio
import hashlib
import importlib.metadata as metadata
import json
import sys
import tempfile
from pathlib import Path

from xdog.ai.types import AssistantMessage, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.config import ClawConfig
from xdog.claw.core.runtime import orchestrator as installed_claw
from xdog.claw.core.runtime.orchestrator import Orchestrator
from xdog.claw.core.types import Group, UserInput


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


def application(root, model, *, display=False):
    app = Orchestrator(ClawConfig(data_dir=str(root / 'data'), extensions=('goal_ui',) if display else (),
        extension_dir=str(root / 'extensions')), stream_fn=model, data_dir=root / 'data')
    app.register_group(Group(id='main', workspace=str(root / 'workspace')))
    return app, app._runtimes['main']


async def activate(app):
    result = await app.route_message(UserInput(group_id='main', content='Inspect actual output', channel='tui'))
    assert result is not None and not result.error


async def base(root):
    model = Model()
    app, runtime = application(root, model)
    old = root / 'data/groups/main/goals.json'
    raw = b'old independently reviewed workers; never read, migrate or replay\n'
    old.write_bytes(raw)
    try:
        await app.start()
        host = runtime.capabilities.get('goal')
        assert host is not None and await host.service.current() is None and not model.calls
        assert runtime._active_session is None and old.read_bytes() == raw
        await runtime.prepare_session(allow_reset=False)
        session = runtime._active_session
        result = await app.manage_extensions('main', 'command', 'goal', 'status', session.meta.session_id)
        assert 'current' in result['output'] and not model.calls and session.extensions.is_native_command('goal')
        model.actions = (('filesystem', {'action': 'write', 'path': 'ordinary.txt', 'content': 'ordinary tool result'}),
            ('memory', {'action': 'write', 'target': 'memory', 'text': 'installed-memory-sentinel'}))
        await activate(app)
        assert runtime.workspace_dir.joinpath('ordinary.txt').read_text() == 'ordinary tool result'
        assert 'installed-memory-sentinel' in runtime.memory.long_term.read()
        assert runtime.transcript_store.load_transcript(runtime.get_new_session_id())
        assert await host.service.current() is None and old.read_bytes() == raw
    finally:
        await app.stop()
    # Default optional ACP uses an actual owned private receiver when the SDK exists.
    from xdog.claw.config import GroupDef
    from xdog.claw.core.runtime import gateway as gateway_module
    from xdog.claw.core.runtime.gateway import GatewayServer
    previous = gateway_module._build_model_and_options
    gateway_module._build_model_and_options = lambda _: ('test/installed', model)
    model.actions = ()
    try:
        with tempfile.TemporaryDirectory(prefix='default-acp-') as short:
            private = Path(short)
            gateway = GatewayServer(ClawConfig(data_dir=str(private / 'data'),
                socket_path=str(private / 'native.sock'), pid_file=str(private / 'pid'),
                groups=(GroupDef(id='main', workspace=str(root / 'gateway-workspace')),)))
            try:
                await gateway.start()
                assert gateway._application_capabilities.tools == () and gateway._acp_server is not None
                host = gateway._orchestrator._runtimes['main']._capability_host
                target = await host.acp_target()
                assert target['session_id'] == 'group:main' and target['endpoint'] == str(gateway._acp_server.path)
            finally:
                await gateway.stop()
    finally:
        gateway_module._build_model_and_options = previous
    assert not any(n.startswith('xdog.eyes') for n in sys.modules)
    print(json.dumps({'installed_claw_default': 'first native /goal status, no create; ordinary tools/memory/history',
                      'eyes_absent': True, 'default_private_acp': True, 'legacy_bytes_unchanged': True}))


async def lifecycle(root):
    from xdog.agent.extensions.discovery import extension_directory
    from xdog.skills.goal.store import GoalStore
    from xdog.tui.components.extension_view import ExtensionView
    model = Model()
    app, runtime = application(root, model, display=True)
    resource = extension_directory('goal_ui', root / 'extensions')
    assert resource is not None and resource.is_relative_to(Path(sys.prefix))
    ui_hash = hashlib.sha256(resource.joinpath('extension.yaml').read_bytes() + b'\0'
                             + resource.joinpath('main.py').read_bytes()).hexdigest()
    runtime.workspace_dir.joinpath('result.txt').write_text('actual installed result')
    try:
        assert runtime.skill_repo.load_skill('goal').packaged
        await app.start()
        host = runtime.capabilities.get('goal')
        assert runtime._active_session is None and not model.calls
        await runtime.prepare_session(allow_reset=False)
        session = runtime._active_session
        result = await app.manage_extensions('main', 'command', 'goal', 'status', session.meta.session_id)
        assert 'current' in result['output'] and not model.calls
        model.actions = (('goal', {'action': 'create', 'title': 'Output', 'outcome': 'Inspect installed result'}),
            ('goal', {'action': 'report', 'report': {'expected_version': 0, 'progress': 'baseline',
                'summary': 'Initial verified state before working', 'completed': [], 'evidence': ['result.txt'],
                'blockers': [], 'next_steps': ['Read actual result and update task']}}),
            ('goal', {'action': 'plan', 'content': 'Read result.txt and record main Agent evidence'}),
            ('filesystem', {'action': 'read', 'path': 'result.txt'}),
            ('goal', {'action': 'task_update', 'task': {'id': 'inspect', 'title': 'Read result',
                                                      'expected_version': 0}}))
        await activate(app)
        goal = await host.service.current()
        assert goal is not None and goal.title == 'Output'
        status = await host.service.status()
        assert status['plan'].startswith('Read result.txt') and status['tasks'][0]['worker'] == 'main'
        before = len(model.calls)
        output = await session.extensions.command('goals', '')
        assert goal.id in str(output) and len(model.calls) == before
        assert any('Goals: 1' in line for line in ExtensionView(lambda: session.extensions.snapshot()).render(80))
        await session.extensions.manage('unload', 'goal_ui')
        assert not session.extensions.snapshot()['ui'] and not session.children.runtime._children
        old_tool, owner = host.tools[0], host.owner
        runtime.reset_session()
        model.actions = ()
        await activate(app)
        assert runtime._active_session is not session and runtime.capabilities.get('goal') is host
        assert host.owner == owner and (await host.service.current()).id == goal.id
        assert (await old_tool.execute('stale', {'action': 'status'})).is_error
        assert await session.extensions.views.read('goal_ui', 'goals', {}) is None
        # A second actual group cannot borrow the first group's records or tool binding.
        app.register_group(Group(id='other', workspace=str(root / 'other')))
        await app.route_message(UserInput(group_id='other', content='Read-only inspection', channel='tui'))
        other = app._runtimes['other'].capabilities.get('goal')
        assert other.owner != owner and await other.service.current() is None
        try:
            await other.service.status(goal.id)
        except ValueError:
            pass
        else:
            raise AssertionError('Cross-owner read was permitted')
        model.actions = (('goal', {'action': 'report', 'report': {'expected_version': 1, 'progress': 'advanced',
            'summary': 'Read actual output since the baseline', 'completed': ['Read result.txt'],
            'evidence': ['result.txt'], 'blockers': [], 'next_steps': ['Commit task evidence and complete']}}),
            ('goal', {'action': 'task_update', 'task': {'id': 'inspect', 'expected_version': 1,
            'status': 'completed', 'summary': 'Read actual output', 'evidence': ['result.txt']}}),
            ('goal', {'action': 'complete', 'summary': 'Main Agent verified result.txt'}))
        await activate(app)
        completed = await host.service.status(goal.id)
        assert completed['current']['status'] == 'completed' and completed['tasks'][0]['status'] == 'completed'
        summary = await host.service.summary(goal.id)
        assert summary['latest_report']['version'] == 2 and summary['latest_report']['progress'] == 'advanced'
        assert 'plan' not in summary and 'tasks' not in summary
        assert Path(summary['report_path']).read_text().count('```json goal-report-v1') == 2
        session = runtime._active_session
        current = await host.service.current()
        assert current.id == goal.id and current.status == 'completed'
        assert not session.children.runtime._children
        session.access.set_read_only(True)
        model.actions = (('filesystem', {'action': 'write', 'path': 'forbidden.txt', 'content': 'not authorized'}),)
        await activate(app)
        assert not runtime.workspace_dir.joinpath('forbidden.txt').exists()
        session.access.set_read_only(False)
        output = await session.extensions.command('goals', '')
        assert goal.id in str(output) and 'completed' in str(output)
        path = host.service.store.path
    finally:
        await app.stop()
    store = await GoalStore.open(path, owner)
    await store.close()
    count = len(model.calls)
    app, runtime = application(root, model)
    try:
        await app.start()
        host = runtime.capabilities.get('goal')
        assert host.owner == owner and (await host.service.status(goal.id))['current']['status'] == 'completed'
        current = await host.service.current()
        assert current.id == goal.id and current.status == 'completed'
        assert runtime._active_session is None and len(model.calls) == count
    finally:
        await app.stop()
    print(json.dumps({'shared_goal_ui_sha256': ui_hash,
        'installed_claw_goal': 'main model create/plan/read/task_update/complete; actual rotation/reopen',
        'goal_status': 'completed', 'children_started': 0, 'ui_loss_does_not_own_progress': True,
        'ui_owner_rotation': 'same original group Goal in fresh parent; stale tool rejected',
        'cross_owner_rejected': True, 'readonly_write_rejected': True, 'main_model_calls': len(model.calls)}))


async def main():
    assert Path(installed_claw.__file__).is_relative_to(Path(sys.prefix))
    names = {dist.metadata['Name'].lower().replace('_', '-') for dist in metadata.distributions()}
    assert 'xdog-skills' in names and 'agent-client-protocol' in names
    assert not names.intersection({'xdog-coding', 'xdog-eyes', 'xdog-flow', 'xdog-site'})
    await (base(Path.cwd()) if '--base-only' in sys.argv else lifecycle(Path.cwd()))


if __name__ == '__main__':
    asyncio.run(main())
