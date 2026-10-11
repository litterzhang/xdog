"""Both actual clients execute exact typed commands, not their similarly named neighbors."""
from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.ai.types import AssistantMessage
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
@pytest.mark.parametrize('uppercase', [False, True])
async def test_actual_live_registered_exact_command_enter_and_explicit_neighbor_selection(tmp_path, kind, uppercase):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions', root)
    calls = []
    def stream(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())
    server = task = writer = None
    if kind == 'coding':
        settings = tmp_path / 'settings.json'
        settings.write_text(json.dumps({'extensions': ['commandcollision'], 'extension_dir': str(root)}))
        manager = SessionManager(tmp_path / 'sessions')
        session = AgentSession(agent=Agent(stream, config=AgentConfig(model='offline/model')),
            session_data=manager.create_session(working_dir=tmp_path), session_manager=manager,
            settings=SettingsManager(global_path=settings), working_dir=tmp_path, tool_registry=None, bash=None,
            permissions=PermissionManager('deny'))
        ui = InteractiveMode(session)
    else:
        config = ClawConfig(model='offline/model', data_dir=str(tmp_path / 'data'),
            socket_path=str(tmp_path / 'exact.sock'), pid_file=str(tmp_path / 'exact.pid'),
            groups=(GroupDef(id='main', is_main=True),), extensions=('commandcollision',), extension_dir=str(root))
        server = GatewayServer(config)
        with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=('offline/model', stream)):
            await server.start()
        ui = ChatApp(config.socket_path)
        reader, writer = await asyncio.open_unix_connection(config.socket_path, limit=4 * 1024 * 1024)
        task = asyncio.create_task(ui._run_connection(reader, writer))
    async def wait(predicate):
        async with asyncio.timeout(8):
            while not predicate():
                ui._poll()
                await asyncio.sleep(0.003)
    try:
        if kind == 'coding':
            ui._handle_slash_command('extensions', '')
            await wait(lambda: ui._command is None)
        else:
            await wait(lambda: ui._connection_ready)
            ui._extension_command('extensions', '')
            await wait(lambda: ui._extension_pending is None)
            session = server._orchestrator._runtimes['main']._active_session
        typed = '/ext-commandcollision-show'
        ui._editor.set_text(typed.upper() if uppercase else typed)
        assert ui._editor._select_list.selected_value == '/ext-commandcollision-shows'
        ui._tui._dispatch_input(KeyEvent('enter'))
        await wait(lambda: 'EXACT-SHOW-ONLY' in str(ui._chat_log.render(140)))
        state = json.loads(await session.extensions.command('ext-commandcollision-counts', ''))
        assert state == {'show': 1, 'shows': 0}
        assert not calls and not session.agent.state.messages
        ui._editor.set_text(typed)
        ui._tui._dispatch_input(KeyEvent('down'))
        ui._tui._dispatch_input(KeyEvent('enter'))
        await wait(lambda: 'EXPLICIT-SHOWS-ONLY' in str(ui._chat_log.render(140)))
        state = json.loads(await session.extensions.command('ext-commandcollision-counts', ''))
        assert state == {'show': 1, 'shows': 1} and not calls
        if kind == 'coding':
            assert session.permissions.mode == 'deny'
            ui._editor.set_text('/session')
            ui._tui._dispatch_input(KeyEvent('enter'))
            await wait(lambda: 'Session ID:' in str(ui._chat_log.render(140)))
            assert ui._navigation_panel is None  # /sessions must not execute instead.
    finally:
        ui._request_exit()
        if 'session' in locals():
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
