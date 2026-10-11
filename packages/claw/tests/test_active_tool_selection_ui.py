"""Actual Claw worker/socket/UI reviewed active selection, no parent permission grant."""
from __future__ import annotations

import asyncio
import json
import shutil
import threading
from pathlib import Path
from unittest.mock import patch

import pytest
from xdog.agent import AgentTool
from xdog.ai.types import AssistantMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer
from xdog.tui.keys import KeyEvent, KeyEventType


@pytest.mark.parametrize('ending', ['apply', 'deny', 'fail', 'cancel', 'changed', 'withdraw',
                                     'slow-save', 'failed-save', 'cancel-save', 'withdraw-save', 'disconnect-save'])
async def test_claw_full_review_callback_success_and_original_tool_selection_owner(tmp_path, ending, monkeypatch):
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
    session = runtime.get_or_create_session(allow_reset=False)
    await session.initialize_extensions()
    session.agent.set_tools((*session.agent.state.tools, AgentTool(name='selected', parameters={}),
                             AgentTool(name='excluded', parameters={})))
    entered, release = threading.Event(), threading.Event()
    gateway_thread = threading.get_ident()
    store = runtime.transcript_store
    original_save = store.save_active_tools
    selection_owner = []
    if ending.endswith('-save'):
        original_select = session.select_active_tools
        async def capture_selection(*args, **kwargs):
            selection_owner.append(asyncio.current_task())
            return await original_select(*args, **kwargs)
        monkeypatch.setattr(session, 'select_active_tools', capture_selection)
        def blocked_save(identifier, selected, *, before_replace=None):
            assert threading.get_ident() != gateway_thread, 'Storage blocked the socket reader'
            def barrier():
                entered.set()
                assert release.wait(8), 'Test disk gate was not released'
                if ending == 'failed-save':
                    raise OSError('Injected storage failure')
                before_replace()
            return original_save(identifier, selected, before_replace=barrier)
        monkeypatch.setattr(store, 'save_active_tools', blocked_save)
    before = session.agent.messages
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
        await wait(lambda: ui._connection_ready and ui._extension_active_tools_supported)
        ui._editor.set_text('UNSENT TOOL SELECTION')
        ui._extension_command('ext-toolselector-select', json.dumps({'names': ['selected'], 'fail': ending == 'fail'}))
        await wait(lambda: ui._extension_dialog_panel is not None)
        panel = ui._extension_dialog_panel
        assert panel.dialog['operation'] == 'select_tools'
        assert panel.dialog['current_tools']['session_id'] == session.meta.session_id
        assert 'excluded' in panel.dialog['current_tools']['active']
        if ending == 'withdraw':
            ui._queue_request({'type': 'ping', 'group_id': 'main', 'extension_active_tools': 0})
            await wait(lambda: not ui._extension_active_tools_supported)
        elif ending == 'cancel':
            panel.handle_input(KeyEvent('escape'))
        else:
            if ending != 'deny':
                panel.handle_input(KeyEvent('right'))
            panel.handle_input(KeyEvent('enter'))
            assert 'excluded' in {tool.name for tool in session.agent.active_tools}
            for _ in range(150):
                panel.render(80)
                if panel.form._visible:
                    break
                panel.handle_input(KeyEvent('pagedown'))
            assert panel.form._visible
            panel.handle_input(KeyEvent('enter', event_type=KeyEventType.REPEAT))
            assert 'excluded' in {tool.name for tool in session.agent.active_tools}
            if ending == 'changed':
                session.agent.set_tools((*session.agent.state.tools, AgentTool(name='new', parameters={})))
            panel.handle_input(KeyEvent('enter'))
        if ending.endswith('-save'):
            await wait(entered.is_set)
            assert session._tool_selection_changing and server._orchestrator.is_group_running('main')
            assert session.agent.active_tool_selection is None
            if ending == 'cancel-save':
                assert ui._extension_cancel_supported
                ui._cancel_extension_command()
                await wait(lambda: selection_owner[0].cancelling())
            elif ending == 'withdraw-save':
                ui._queue_request({'type': 'ping', 'group_id': 'main', 'extension_active_tools': 0})
                await wait(lambda: not ui._extension_active_tools_supported)
            elif ending == 'disconnect-save':
                writer.close()
                await writer.wait_closed()
                await wait(lambda: not ui._connection_ready)
            else:
                # Capability deltas are reader-side controls, independent of the
                # held disk owner and unrelated to the tool-selection grant.
                ui._queue_request({'type': 'ping', 'group_id': 'main', 'extension_command_catalog': 1})
            await asyncio.sleep(.02)
            assert session._tool_selection_changing and server._orchestrator.is_group_running('main')
            release.set()
        if ending == 'disconnect-save':
            await wait(lambda: not server._orchestrator.is_group_running('main'))
        else:
            await wait(lambda: ui._extension_pending is None)
        active = [tool.name for tool in session.agent.active_tools]
        applied = ending in {'apply', 'slow-save'}
        assert active == ['selected'] if applied else 'excluded' in active
        assert store.get_active_tools(session.meta.session_id) == (('selected',) if applied else None)
        assert ui._editor.get_text() == 'UNSENT TOOL SELECTION'
        assert session.agent.messages is before and not calls
        assert 'excluded' in {tool.name for tool in session.agent.state.tools}
        assert not server._orchestrator.is_group_running('main')
        if applied:
            assert 'Active tool selection applied' in str(ui._chat_log.render(160))
    finally:
        release.set()
        await wait(lambda: not session._tool_selection_changing)
        ui._request_exit()
        connection.cancel()
        await asyncio.gather(connection, return_exceptions=True)
        writer.close()
        await writer.wait_closed()
        await server.stop()


async def test_accepted_claw_selection_is_discarded_if_capability_withdraws_before_callback_returns(
    tmp_path, monkeypatch,
):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/toolselector', root / 'toolselector')
    config = ClawConfig(model='offline', data_dir=str(tmp_path / 'data'), socket_path=str(tmp_path / 'socket'),
        pid_file=str(tmp_path / 'pid'), groups=(GroupDef(id='main', is_main=True, workspace=str(tmp_path)),),
        extensions=('toolselector',), extension_dir=str(root))
    calls = []
    def stream(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())
    server = GatewayServer(config)
    with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=('offline', stream)):
        await server.start()
    runtime = server._orchestrator._runtimes['main']
    session = runtime.get_or_create_session(allow_reset=False)
    await session.initialize_extensions()
    entered, release = asyncio.Event(), asyncio.Event()
    original = session.extensions.command
    async def held(*args, **kwargs):
        output = await original(*args, **kwargs)
        entered.set()
        await release.wait()
        return output
    monkeypatch.setattr(session.extensions, 'command', held)
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
    before = tuple(tool.name for tool in session.agent.active_tools)
    try:
        await wait(lambda: ui._connection_ready and ui._extension_active_tools_supported)
        ui._extension_command('ext-toolselector-select', '{"names":[]}')
        await wait(lambda: ui._extension_dialog_panel is not None)
        panel = ui._extension_dialog_panel
        panel.handle_input(KeyEvent('right'))
        panel.handle_input(KeyEvent('enter'))
        for _ in range(150):
            panel.render(80)
            if panel.form._visible:
                break
            panel.handle_input(KeyEvent('pagedown'))
        assert panel.form._visible
        panel.handle_input(KeyEvent('enter'))
        await wait(entered.is_set)
        assert tuple(tool.name for tool in session.agent.active_tools) == before
        ui._queue_request({'type': 'ping', 'group_id': 'main', 'extension_active_tools': 0})
        await wait(lambda: not ui._extension_active_tools_supported)
        release.set()
        await wait(lambda: ui._extension_pending is None)
        assert tuple(tool.name for tool in session.agent.active_tools) == before
        assert 'Active tool selection not applied' in str(ui._chat_log.render(160))
        assert not calls
    finally:
        release.set()
        ui._request_exit()
        connection.cancel()
        await asyncio.gather(connection, return_exceptions=True)
        writer.close()
        await writer.wait_closed()
        await server.stop()
