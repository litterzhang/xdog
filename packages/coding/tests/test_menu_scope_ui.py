"""Both native hosts read their actual menu from original canonical callback scopes."""
from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
from xdog.ai.types import AssistantMessage, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode

from packages.claw.tests.test_extension_message_ui import native_ui
from packages.coding.tests.test_rpc_extensions import close, session


@pytest.mark.parametrize('kind', ['coding', 'claw'])
@pytest.mark.parametrize('scope', ['tool', 'event', 'pre', 'post', 'input', 'context'])
async def test_canonical_callback_reads_original_native_menu_without_composer_disclosure(tmp_path, kind, scope):
    calls = []
    def stream(model, context, options):
        calls.append(context)
        if context.messages[-1].role == 'user':
            tool = next(tool for tool in context.tools if 'menureader' in tool.name)
            result = AssistantMessage(content=(ToolCall(id='menu-tool', name=tool.name, arguments={}),),
                                      stop_reason='toolUse')
        else:
            result = AssistantMessage(content=(TextContent(text='READONLY MENU TURN FINISHED'),))
        return EventStream.empty(result)
    if kind == 'coding':
        owner, _ = session(tmp_path, extra=('menureader',))
        owner.extensions.settings['menureader'] = {'scope': scope}
        owner.permissions.set_mode('allow-all')
        owner.agent.set_stream_fn(stream)
        ui = InteractiveMode(owner)
        async def wait(predicate):
            async with asyncio.timeout(10):
                while True:
                    ui._poll()
                    if predicate():
                        return
                    await asyncio.sleep(.003)
        try:
            base = ui._editor._command_provider
            ui._editor._command_provider = lambda: {**base(), 'only-original-ui': 'ACTUAL CLIENT MENU'}
            ui._handle_submit('original explicitly admitted input')
            ui._editor.set_text('PRIVATE UNSENT NOT IN MENU')
            await wait(lambda: not ui._worker_active)
            verify(owner, ui, scope, calls)
        finally:
            ui._request_exit()
            await close(owner)
    else:
        root = tmp_path / 'extensions'
        shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/menureader', root / 'menureader')
        config = ClawConfig(model='offline/model', data_dir=str(tmp_path / 'data'),
            socket_path=str(tmp_path / 'socket'), pid_file=str(tmp_path / 'pid'),
            groups=(GroupDef(id='main', is_main=True, workspace=str(tmp_path)),),
            extensions=('menureader',), extension_dir=str(root), extension_allow_tools=('menureader',),
            extension_settings={'menureader': {'scope': scope}})
        server = GatewayServer(config)
        with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=('offline/model', stream)):
            await server.start()
        owner = server._orchestrator._runtimes['main'].get_or_create_session(allow_reset=False)
        try:
            async with native_ui(config) as (ui, wait):
                base = ui._editor._command_provider
                ui._editor._command_provider = lambda: {**base(), 'only-original-ui': 'ACTUAL CLIENT MENU'}
                ui._editor.set_text('PRIVATE UNSENT NOT IN MENU')
                ui._start_message('original explicitly admitted input', ())
                await wait(lambda: ui._active_run_id is None)
                verify(owner, ui, scope, calls)
                assert owner.extensions.command_menu_handler is None
        finally:
            await server.stop()


def verify(owner, ui, scope, calls):
    rows = [row['data'] for row in owner.extensions.entries.for_extension('menureader')]
    assert scope in {row['scope'] for row in rows} and 'tool' in {row['scope'] for row in rows}
    assert all(row['available'] and row['executed'] == 0 for row in rows)
    assert all('/only-original-ui' in row['names'] and '/help' in row['names'] for row in rows)
    assert all(not row['child_id'] for row in rows)
    assert len(calls) == 2 and ui._editor.get_text() == 'PRIVATE UNSENT NOT IN MENU'
    assert 'PRIVATE UNSENT' not in json.dumps(rows)
    # A tool may explicitly return the metadata it read; that is its ordinary
    # ToolResult, not a hidden user message or composer disclosure.
    assert 'PRIVATE UNSENT' not in str(owner.agent.messages)
    assert sum(message.role == 'user' for message in owner.agent.messages) == 1
    assert not owner.extensions._failed and ui._extension_navigation is None and ui._extension_browser is None


@pytest.mark.parametrize('kind', ['coding', 'claw'])
async def test_native_custom_input_menu_read_preserves_original_view_and_no_model(tmp_path, kind):
    from xdog.tui.components.extension_custom import ExtensionCustom
    from xdog.tui.keys import KeyEvent
    calls = []
    def stream(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())
    async def drive(ui, owner):
        seen = set()
        async with asyncio.timeout(10):
            while True:
                ui._poll()
                panel = ui._extension_dialog_panel
                if isinstance(panel, ExtensionCustom):
                    panel.set_render_budget(12)
                    panel.render(80)
                    stamp = panel.dialog['id'], panel._painted
                    if panel._painted > 0 and stamp not in seen:
                        seen.add(stamp)
                        panel.handle_input(KeyEvent('enter'))
                running = ui._command is not None if kind == 'coding' else ui._extension_pending is not None
                if not running:
                    break
                await asyncio.sleep(.003)
        row = owner.extensions.entries.for_extension('menureader')[-1]['data']
        assert row['scope'] == 'custom-input' and row['available'] and '/native-custom-menu' in row['names']
        assert row['executed'] == 0 and not calls and not owner.agent.messages
        assert ui._editor.get_text() == 'PRIVATE CUSTOM DRAFT' and ui._extension_dialog_panel is None
    if kind == 'coding':
        owner, _ = session(tmp_path, extra=('menureader',))
        ui = InteractiveMode(owner)
        try:
            base = ui._editor._command_provider
            ui._editor._command_provider = lambda: {**base(), 'native-custom-menu': 'Actual menu'}
            ui._editor.set_text('PRIVATE CUSTOM DRAFT')
            ui._handle_slash_command('ext-menureader-custom', '')
            await drive(ui, owner)
        finally:
            ui._request_exit()
            await close(owner)
    else:
        root = tmp_path / 'extensions'
        shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/menureader', root / 'menureader')
        config = ClawConfig(model='offline', data_dir=str(tmp_path / 'data'), socket_path=str(tmp_path / 'socket'),
            pid_file=str(tmp_path / 'pid'), groups=(GroupDef(id='main', is_main=True, workspace=str(tmp_path)),),
            extensions=('menureader',), extension_dir=str(root))
        server = GatewayServer(config)
        with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=('offline', stream)):
            await server.start()
        owner = server._orchestrator._runtimes['main'].get_or_create_session(allow_reset=False)
        try:
            async with native_ui(config) as (ui, _wait):
                base = ui._editor._command_provider
                ui._editor._command_provider = lambda: {**base(), 'native-custom-menu': 'Actual menu'}
                ui._editor.set_text('PRIVATE CUSTOM DRAFT')
                ui._extension_command('ext-menureader-custom', '')
                await drive(ui, owner)
        finally:
            await server.stop()
