"""Native shared queue widgets use actual Coding SDK / Claw socket-owned child queues."""
from __future__ import annotations

import asyncio
import json
import threading
from unittest.mock import patch

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.ai.types import AssistantMessage, StartEvent, TextContent
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.permissions import PermissionManager
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.settings_manager import SettingsManager
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
from xdog.tui.keys import KeyEvent


@pytest.mark.parametrize('kind', ['coding', 'claw'])
@pytest.mark.parametrize('action', ['edit', 'return', 'recover'])
async def test_actual_native_child_queue_is_list_only_and_keeps_parent_draft_and_backend_inputs(tmp_path, kind, action):
    entered = threading.Event()
    calls = []
    def stream(*args):
        calls.append(args)
        async def generate():
            entered.set()
            yield StartEvent(partial=AssistantMessage(content=(TextContent(text='held partial'),)))
            await asyncio.sleep(60)
        return EventStream.from_async_generator(generate())
    server = task = writer = None
    if kind == 'coding':
        manager = SessionManager(tmp_path / 'sessions')
        session = AgentSession(agent=Agent(stream, config=AgentConfig(model='offline/model')),
            session_data=manager.create_session(model='offline/model', working_dir=tmp_path),
            session_manager=manager, settings=SettingsManager(global_path=tmp_path / 'settings.json'),
            working_dir=tmp_path, tool_registry=None, bash=None, permissions=PermissionManager('deny'))
        ui = InteractiveMode(session)
    else:
        config = ClawConfig(model='offline/model', data_dir=str(tmp_path / 'data'),
            socket_path=str(tmp_path / 'queue.sock'), pid_file=str(tmp_path / 'queue.pid'),
            groups=(GroupDef(id='main', is_main=True),))
        server = GatewayServer(config)
        with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=(config.model, stream)):
            await server.start()
        session = server._orchestrator._runtimes['main'].get_or_create_session(allow_reset=False)
        ui = ChatApp(config.socket_path)
        reader, writer = await asyncio.open_unix_connection(config.socket_path)
        task = asyncio.create_task(ui._run_connection(reader, writer))
    async def wait(predicate):
        async with asyncio.timeout(6):
            while not predicate():
                ui._poll()
                await asyncio.sleep(0.005)
    try:
        if kind == 'claw':
            await wait(lambda: ui._connection_ready)
            assert ui._child_queues_supported
        child = await session.children.supervisor.start('hold')
        assert await asyncio.to_thread(entered.wait, 3)
        await session.children.supervisor.control('steer', child, 'QUEUED-DRAFT /quit literal')
        if action == 'recover':
            await session.children.supervisor.control('cancel', child)
        ui._editor.set_text('PARENT-DRAFT-UNTOUCHED')
        ui._open_children()
        await wait(lambda: ui._child_panel._entries)
        panel = ui._child_panel
        panel.render(80)
        panel.handle_input(KeyEvent('q'))
        await wait(lambda: panel.queue is not None and not panel.queue._busy)
        queue = panel.queue
        assert queue._rows[0]['content'] == 'QUEUED-DRAFT /quit literal'
        queue.render(80)
        before = await session.children.supervisor.control('queue', child, json.dumps({'action': 'list'}))
        for key in ('e', 'u', 'x', 'delete', 'enter', 'up', 'down'):
            panel.handle_input(KeyEvent(key))
        panel.handle_paste('UNTRUSTED QUEUE EDIT /quit')
        assert not hasattr(queue, '_input') and not hasattr(queue, '_confirm')
        for retired in ('edit', 'return', 'recover', 'remove', 'move', 'recover_images', 'recover_task',
                        'return_prepare', 'return_commit', 'return_cancel'):
            ui._child_send('queue', child, json.dumps({'action': retired}), 'read-only')
        await asyncio.sleep(0.04)
        ui._poll()
        actual = await session.children.supervisor.control('queue', child, json.dumps({'action': 'list'}))
        assert actual == before and panel.queue is queue
        assert actual['queue']['inputs'][0]['content'] == 'QUEUED-DRAFT /quit literal'
        assert actual['queue'].get('stopped', False) is (action == 'recover')
        panel.handle_input(KeyEvent('escape'))  # Leave metadata details first.
        panel.handle_input(KeyEvent('escape'))
        assert panel.queue is None  # Return to inspection, not to a recovered/editable draft.
        assert ui._editor.get_text() == 'PARENT-DRAFT-UNTOUCHED'
        assert len(calls) == 1 and len(session.agent.messages) == 0
        await session.children.supervisor.control('cancel', child)
    finally:
        ui._request_exit()
        if kind == 'coding':
            await session.children.supervisor.aclose()
            await session.extensions.aclose()
            session.dispose()
        else:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            writer.close()
            await writer.wait_closed()
            await server.stop()


async def test_direct_native_agents_queue_command_rejects_mutations_before_backend_dispatch(coding_session):
    from unittest.mock import AsyncMock

    from xdog.coding.core.slash_commands import execute_command
    control = AsyncMock(return_value={'queue': {'inspection': 'native list'}})
    with patch.object(coding_session.children.supervisor, 'control', control):
        for action in ('edit', 'remove', 'move', 'return', 'recover', 'recover_images', 'recover_task',
                       'remove_task', 'return_prepare', 'return_commit', 'return_cancel'):
            with pytest.raises(ValueError, match='read-only'):
                await execute_command('agents', 'queue child ' + json.dumps({'action': action}), coding_session)
        control.assert_not_awaited()
        result = await execute_command('agents', 'queue child {"action":"list"}', coding_session)
        assert json.loads(result.output) == {'queue': {'inspection': 'native list'}}
        control.assert_awaited_once_with('queue', 'child', '{"action":"list"}', images=())
        assert not coding_session.agent.messages


@pytest.mark.parametrize('fence', ['view', 'panel', 'group', 'session', 'child', 'context'])
def test_claw_queue_reply_is_locally_fenced_without_old_wire_metadata(monkeypatch, fence):
    from xdog.tui.child_browser import child_entries

    ui = ChatApp('/unused-child-queue-fences.sock')
    sent = []
    monkeypatch.setattr(ui, '_queue_request', lambda value, *images: sent.append(value))
    ui._connection_ready = ui._children_supported = ui._child_queues_supported = True
    ui._gateway_session_id = 'session'
    try:
        ui._open_children()
        panel = ui._child_panel
        panel.publish(child_entries({'children': [{'id': '0123456789ab', 'task': 'inspect', 'status': 'running',
            'access': 'read-only', 'calls': 0, 'tokens': 0, 'revision': 1, 'settling': False}]}))
        panel.open_queue()
        queue = panel.queue
        request = sent[-1]
        assert request['action'] == 'queue' and json.loads(request['text']) == {'action': 'list'}
        assert not {'queue_view', 'child_return_owner'} & request.keys()
        identifier = request['request_id']
        assert ui._child_queue_owners[identifier] == (queue.owner_token, panel.owner_token, 'main',
                                                    'session', '0123456789ab')
        queue._error = 'successor state'
        if fence == 'view':
            queue.owner_token = 'newer-view'
        elif fence == 'panel':
            panel.owner_token = 'newer-panel'
        elif fence == 'group':
            ui.group_id = 'newer-group'
        elif fence == 'session':
            ui._gateway_session_id = 'newer-session'
        elif fence == 'child':
            queue.child = 'newer-child'
        else:
            ui._child_context = 'newer-context'
        ui._handle_response({'type': 'child_result', 'request_id': identifier, 'group_id': 'main',
                             'session_id': 'session', 'success': True,
                             'data': {'queue': {'child_id': '0123456789ab', 'revision': 'a' * 64, 'inputs': []}}})
        assert queue._busy and queue._error == 'successor state' and not queue._rows
        assert ui._child_panel is panel and len(sent) == 2
        assert ui._editor.get_text() == '' and ui._active_run_id is None
    finally:
        ui._request_exit()
