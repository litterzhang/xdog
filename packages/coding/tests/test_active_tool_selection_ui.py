"""Coding's actual extension command/broker/full-painted active-tool review."""
from __future__ import annotations

import asyncio
import json
import threading

import pytest
from xdog.agent import AgentTool
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
from xdog.tui.keys import KeyEvent, KeyEventType

from packages.coding.tests.test_rpc_extensions import close, session


@pytest.mark.parametrize('ending', ['apply', 'deny', 'fail', 'cancel', 'changed', 'expired',
                                     'slow-save', 'cancel-save', 'failed-save'])
async def test_native_coding_tool_selection_review_preserves_draft_permissions_and_callback_owner(tmp_path, ending,
                                                                                                monkeypatch):
    owner, calls = session(tmp_path, extra=('toolselector',))
    owner.agent.set_tools((*owner.agent.state.tools, AgentTool(name='selected', parameters={}),
                          AgentTool(name='excluded', parameters={})))
    ui = InteractiveMode(owner)
    entered, release = threading.Event(), threading.Event()
    terminal_thread = threading.get_ident()
    async def wait(predicate):
        async with asyncio.timeout(8):
            while True:
                ui._poll()
                if predicate():
                    return
                await asyncio.sleep(.003)
    try:
        await owner.initialize_extensions()
        if ending in {'slow-save', 'cancel-save', 'failed-save'}:
            original_save = owner.session_manager.save_session
            def blocked_save(data, *, before_replace=None):
                if before_replace is None:
                    return original_save(data)
                assert threading.get_ident() != terminal_thread, 'Disk persistence ran on terminal thread'
                def barrier():
                    entered.set()
                    assert release.wait(8), 'Test disk gate was not released'
                    if ending == 'failed-save':
                        raise OSError('Injected storage failure')
                    before_replace()
                return original_save(data, before_replace=barrier)
            monkeypatch.setattr(owner.session_manager, 'save_session', blocked_save)
        ui._editor.set_text('UNSENT SELECTION DRAFT')
        before = owner.agent.messages
        ui._handle_slash_command('ext-toolselector-select',
                                 json.dumps({'names': ['selected'], 'fail': ending == 'fail'}))
        await wait(lambda: ui._extension_dialog_panel is not None)
        panel = ui._extension_dialog_panel
        assert panel.dialog['operation'] == 'select_tools'
        assert panel.dialog['current_tools']['active'] == [tool.name for tool in owner.agent.active_tools]
        if ending == 'expired':
            broker = owner.extension_dialogs
            original = broker.resolve
            def refuse_before_delivery(key, value):
                if value is True:
                    _dialog, loop, future = broker._pending[key]
                    def lose():
                        future.set_result(None)
                        original(key, value)
                    loop.call_soon_threadsafe(lose)
                    return True
                return original(key, value)
            monkeypatch.setattr(broker, 'resolve', refuse_before_delivery)
        if ending == 'cancel':
            panel.handle_input(KeyEvent('escape'))
        else:
            if ending != 'deny':
                panel.handle_input(KeyEvent('right'))
            panel.handle_input(KeyEvent('enter'))
            assert 'excluded' in {tool.name for tool in owner.agent.active_tools}
            for _ in range(150):
                panel.render(80)
                if panel.form._visible:
                    break
                panel.handle_input(KeyEvent('pagedown'))
            assert panel.form._visible
            panel.handle_input(KeyEvent('enter', event_type=KeyEventType.REPEAT))
            assert 'excluded' in {tool.name for tool in owner.agent.active_tools}
            if ending == 'changed':
                owner.agent.set_tools((*owner.agent.state.tools, AgentTool(name='new', parameters={})))
            panel.handle_input(KeyEvent('enter'))
        if ending in {'slow-save', 'cancel-save', 'failed-save'}:
            await wait(entered.is_set)
            assert ui._command is not None and ui._command.tools_target is not None
            assert owner.agent.active_tool_selection is None
            ui._poll()  # Terminal owner keeps polling while fsync/replacement is outstanding.
            if ending == 'cancel-save':
                ui._cancel_command()
            await asyncio.sleep(.02)
            assert ui._command is not None
            release.set()
        await wait(lambda: ui._command is None)
        active = [tool.name for tool in owner.agent.active_tools]
        if ending in {'apply', 'slow-save'}:
            assert active == ['selected']
            owner._native_tool_calls = lambda: False
            owner._rebuild_system_prompt()
            assert 'excluded' not in owner.agent.state.system_prompt
            assert 'selected' in owner.agent.state.system_prompt
        else:
            assert 'excluded' in active
        assert 'excluded' in {tool.name for tool in owner.agent.state.tools}
        assert ui._editor.get_text() == 'UNSENT SELECTION DRAFT'
        assert not calls and owner.permissions.mode == 'deny' and owner.agent.messages is before
        assert ui._extension_navigation is None and ui._extension_navigation_review is None
    finally:
        release.set()
        if ui._command is not None:
            ui._cancel_command()
            await wait(lambda: ui._command is None)
        ui._request_exit()
        await close(owner)


async def test_claw_active_tools_without_negotiation_returns_unavailable_without_browser_or_model(tmp_path):
    import shutil
    from pathlib import Path
    from unittest.mock import patch

    from xdog.ai.types import AssistantMessage
    from xdog.ai.utils.event_stream import EventStream
    from xdog.claw.channels.tui.tui_app import ChatApp
    from xdog.claw.config import ClawConfig, GroupDef
    from xdog.claw.core.runtime.gateway import GatewayServer
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/toolselector', root / 'toolselector')
    calls = []
    def stream(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())
    config = ClawConfig(model='offline', data_dir=str(tmp_path / 'data'), socket_path=str(tmp_path / 'socket'),
        pid_file=str(tmp_path / 'pid'), groups=(GroupDef(id='main', is_main=True, workspace=str(tmp_path)),),
        extensions=('toolselector',), extension_dir=str(root))
    server = GatewayServer(config)
    with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=('offline', stream)):
        await server.start()
    runtime = server._orchestrator._runtimes['main']
    runtime.get_or_create_session(allow_reset=False)
    ui = ChatApp(config.socket_path)
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    connection = asyncio.create_task(ui._run_connection(reader, writer))
    async def wait(predicate):
        async with asyncio.timeout(8):
            while True:
                ui._poll()
                if predicate():
                    return
                await asyncio.sleep(.003)
    try:
        await wait(lambda: ui._connection_ready)
        ui._queue_request({'type': 'ping', 'group_id': ui.group_id, 'extension_active_tools': 0})
        await wait(lambda: not ui._extension_active_tools_supported)
        ui._editor.set_text('UNSENT CLAW')
        ui._extension_command('ext-toolselector-select', '{"names":[]}')
        await wait(lambda: ui._extension_pending is None)
        assert 'ACTIVE-TOOLS-UNAVAILABLE' in str(ui._chat_log.render(160))
        assert ui._navigation_panel is None and ui._extension_browser is None
        assert not calls and ui._editor.get_text() == 'UNSENT CLAW'
    finally:
        ui._request_exit()
        connection.cancel()
        await asyncio.gather(connection, return_exceptions=True)
        writer.close()
        await writer.wait_closed()
        await server.stop()
