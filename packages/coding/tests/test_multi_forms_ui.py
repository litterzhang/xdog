"""Actual multi-selection form paths in both clients; owned offline adapters only."""
from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.ai.types import AssistantMessage, TextContent, ToolCall
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
@pytest.mark.parametrize('child', [False, True])
@pytest.mark.parametrize('cancel', [False, True])
async def test_actual_owned_multiselect_form_typed_values_review_cancel_and_parent_recovery(
    tmp_path, kind, child, cancel,
):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions', root)
    calls = []
    def stream(model, context, options):
        calls.append(context)
        if context.messages[-1].role == 'user':
            tool = next(tool for tool in context.tools if tool.name.startswith('ext_dialogue_many_'))
            message = AssistantMessage(content=(ToolCall(id='many', name=tool.name, arguments={}),))
        else:
            message = AssistantMessage(content=(TextContent(text='many done'),))
        return EventStream.empty(message)
    server = None
    task = writer = None
    if kind == 'coding':
        settings = tmp_path / 'settings.json'
        settings.write_text(json.dumps({'extensions': ['dialogue'], 'extension_dir': str(root)}))
        store = SessionManager(tmp_path / 'sessions')
        session = AgentSession(agent=Agent(stream, config=AgentConfig(model='test/model')),
            session_data=store.create_session(working_dir=tmp_path), session_manager=store,
            settings=SettingsManager(global_path=settings), working_dir=tmp_path, tool_registry=None, bash=None,
            permissions=PermissionManager('allow-all' if child else 'ask'))
        ui = InteractiveMode(session)
    else:
        config = ClawConfig(model='test/model', data_dir=str(tmp_path / 'data'),
            socket_path=str(tmp_path / 'many.sock'), pid_file=str(tmp_path / 'many.pid'),
            groups=(GroupDef(id='main', is_main=True),), extensions=('dialogue',), extension_dir=str(root),
            extension_allow_tools=('dialogue',))
        server = GatewayServer(config)
        with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=('test/model', stream)):
            await server.start()
        ui = ChatApp(config.socket_path)
        reader, writer = await asyncio.open_unix_connection(config.socket_path)
        task = asyncio.create_task(ui._run_connection(reader, writer))
    async def wait(predicate):
        async with asyncio.timeout(10):
            while not predicate():
                ui._poll()
                await asyncio.sleep(0.003)
    try:
        if kind == 'claw':
            await wait(lambda: ui._connection_ready)
            assert ui._extension_multi_forms_supported
        ui._editor.set_text('kept parent draft')
        if child:
            if kind == 'coding':
                await session.children.supervisor.start('many child', access='inherit')
            else:
                ui._agents_command('start --inherit many child')
        elif kind == 'coding':
            ui._handle_slash_command('ext-dialogue-many', '')
        else:
            ui._extension_command('ext-dialogue-many', '')
        await wait(lambda: ui._extension_dialog_panel is not None)
        if kind == 'claw':
            session = server._orchestrator._runtimes['main']._active_session
        wrapper = ui._extension_dialog_panel
        assert wrapper.dialog['fields'][0]['kind'] == 'multi_choice'
        if cancel:
            wrapper.handle_input(KeyEvent('escape'))
        else:
            wrapper.handle_input(KeyEvent('space'))
            wrapper.handle_input(KeyEvent('down'))
            wrapper.handle_input(KeyEvent('space'))
            wrapper.handle_input(KeyEvent('down'))
            wrapper.handle_input(KeyEvent('space'))
            assert wrapper._form.values['values'] == [False, 0, None]
            wrapper.handle_input(KeyEvent('enter'))
            wrapper.handle_input(KeyEvent('enter'))
            assert ui._extension_dialog_panel is wrapper  # Unpainted review cannot submit.
            wrapper.focused = False
            wrapper.render(80)
            assert not wrapper._form._visible
            wrapper.focused = True
            for _ in range(100):
                wrapper.render(80)
                if wrapper._form._visible:
                    break
                wrapper.handle_input(KeyEvent('pagedown'))
            assert wrapper._form._visible
            wrapper.handle_input(KeyEvent('enter'))
        await wait(lambda: ui._extension_dialog_panel is None)
        if child:
            await wait(lambda: not session.children.supervisor.active)
            rows = session.children.store.load()
            assert len(rows) == 1 and rows[0]['status'] == 'completed'
            result = await session.children.supervisor.control('wait', rows[0]['id'])
            body = str(result['messages'])
            assert ('MANY-RESULT-null' if cancel else 'false, 0, null') in body
        else:
            await wait(lambda: ui._command is None if kind == 'coding' else ui._extension_pending is None)
            body = '\n'.join(ui._chat_log.render(200))
            assert ('MANY-RESULT-null' if cancel else 'false, 0, null') in body
        assert len(calls) == (2 if child else 0)
        assert ui._editor.get_text() == 'kept parent draft' and ui._tui._focused is ui._editor
    finally:
        ui._request_exit()
        if kind == 'claw':
            session = server._orchestrator._runtimes['main']._active_session
        if session is not None:
            await session.children.supervisor.aclose()
            await session.extensions.aclose()
        if kind == 'coding':
            session.dispose()
        else:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            writer.close()
            await writer.wait_closed()
            await server.stop()
