from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.ai.types import AssistantMessage, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.permissions import PermissionManager
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.settings_manager import SettingsManager
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
from xdog.tui.components.extension_custom import ExtensionCustom
from xdog.tui.components.extension_dialog import ExtensionDialog
from xdog.tui.keys import KeyEvent


@pytest.mark.parametrize('child', [False, True])
@pytest.mark.parametrize('mode', ['', 'custom', 'full'])
async def test_coding_nested_owned_stack_restores_original_parent_and_draft(tmp_path, child, mode):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions', root)
    settings = tmp_path / 'settings.json'
    settings.write_text(json.dumps({'extensions': ['nested'], 'extension_dir': str(root)}))
    calls = []
    def stream(model, context, options):
        calls.append(context)
        if context.messages[-1].role == 'user':
            tool = next(tool for tool in context.tools if tool.name.startswith('ext_nested_'))
            result = AssistantMessage(content=(ToolCall(id='nested', name=tool.name, arguments={'mode': mode}),))
        else:
            result = AssistantMessage(content=(TextContent(text='done'),))
        return EventStream.empty(result)
    store = SessionManager(tmp_path / 'sessions')
    session = AgentSession(agent=Agent(stream, config=AgentConfig(model='test/model')),
        session_data=store.create_session(working_dir=tmp_path), session_manager=store,
        settings=SettingsManager(global_path=settings), working_dir=tmp_path, tool_registry=None, bash=None,
        permissions=PermissionManager('allow-all' if child else 'ask'))
    ui = InteractiveMode(session)
    if mode == 'full':
        ui._tui._running = True
    async def wait(predicate):
        async with asyncio.timeout(8):
            while not predicate():
                ui._poll()
                if ui._extension_dialog_panel is not None:
                    ui._layout.set_height(30)
                    if mode == 'full':
                        ui._layout.render(80)
                    ui._extension_dialog_panel.render(80)
                await asyncio.sleep(0.003)
    try:
        ui._editor.set_text('original draft')
        ui._editor._cursor = 3
        if child:
            identifier = await session.children.supervisor.start('nested question', access='inherit')
        else:
            ui._handle_slash_command('ext-nested-view', mode)
        await wait(lambda: isinstance(ui._extension_dialog_panel, ExtensionCustom)
                   and ui._extension_dialog_panel._painted > 0)
        parent = ui._extension_dialog_panel
        parent.handle_input(KeyEvent('n'))
        await wait(lambda: ui._extension_dialog_panel is not parent)
        nested = ui._extension_dialog_panel
        assert nested.dialog['parent_id'] == parent.dialog['id'] and not parent.focused and parent._painted == -1
        if mode == 'custom':
            assert isinstance(nested, ExtensionCustom)
            await wait(lambda: nested._painted > 0)
            nested.handle_input(KeyEvent('n'))
            await wait(lambda: isinstance(ui._extension_dialog_panel, ExtensionDialog))
            grandchild = ui._extension_dialog_panel
            assert grandchild.dialog['parent_id'] == nested.dialog['id']
            grandchild._input.set_text('grand')
            grandchild.render(80)
            grandchild.handle_input(KeyEvent('enter'))
            await wait(lambda: ui._extension_dialog_panel is nested and nested._painted > 0)
            nested.handle_input(KeyEvent('enter'))
        else:
            assert isinstance(nested, ExtensionDialog) and nested.dialog['kind'] == 'input'
            nested._input.set_text('answer')
            nested.render(80)
            nested.handle_input(KeyEvent('enter'))
            await wait(lambda: isinstance(ui._extension_dialog_panel, ExtensionDialog)
                       and ui._extension_dialog_panel.dialog['kind'] == 'confirm')
            confirm = ui._extension_dialog_panel
            confirm.handle_input(KeyEvent('down'))
            confirm.render(80)
            confirm.handle_input(KeyEvent('enter'))
        await wait(lambda: ui._extension_dialog_panel is parent and parent._painted > 0)
        assert parent.focused and ui._extension_stack.panels[parent.dialog['id']] is parent
        assert parent.fullscreen == (mode == 'full')
        parent.handle_input(KeyEvent('enter'))
        if child:
            await wait(lambda: not session.children.supervisor.active)
            record = await session.children.supervisor.control('wait', identifier)
            assert record['status'] == 'completed' and len(calls) == 2
        else:
            await wait(lambda: ui._command is None)
            assert not calls
        assert ui._tui._focused is ui._editor and ui._editor.get_text() == 'original draft'
        assert ui._editor._cursor == 3 and not ui._extension_stack.panels and not session.agent.state.messages
    finally:
        ui._request_exit()
        await session.children.supervisor.aclose()
        await session.extensions.aclose()
        session.dispose()
