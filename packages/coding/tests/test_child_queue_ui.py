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
async def test_actual_native_child_queue_edit_or_return_keeps_parent_draft_and_never_submits_it(tmp_path, kind, action):
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
        if action == 'edit':
            panel.handle_input(KeyEvent('e'))
            queue.render(80)
            queue._input.set_text('EDITED-CHILD-DRAFT')
            panel.handle_input(KeyEvent('enter'))
        else:
            panel.handle_input(KeyEvent('u'))
        for _ in range(100):
            queue.render(80)
            if queue._visible:
                break
            queue.handle_input(KeyEvent('pagedown'))
        assert queue._visible
        panel.handle_input(KeyEvent('enter'))
        await wait(lambda: panel.queue is None if action in {'return', 'recover'} else not queue._busy)
        actual = (await session.children.supervisor.control('queue', child, json.dumps({'action': 'list'})))['queue']
        if action == 'edit':
            assert actual['inputs'][0]['content'] == 'EDITED-CHILD-DRAFT'
        elif action == 'return':
            assert not actual['inputs'] and panel._mode == 'form' and panel._form_action == 'steer'
            assert panel._input.get_text() == 'QUEUED-DRAFT /quit literal'
        else:
            assert actual['inputs'] and actual['stopped']
            assert panel._mode == 'form' and panel._form_action == 'start' and panel._access == 'read-only'
            assert panel._input.get_text() == 'QUEUED-DRAFT /quit literal'
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
