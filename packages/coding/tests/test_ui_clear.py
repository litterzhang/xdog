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


@pytest.mark.parametrize('kind', ['coding', 'claw'])
async def test_actual_client_status_widget_clear_same_owner_focused_draft_and_no_model_turn(tmp_path, kind):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions', root)
    calls = []
    def stream(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())
    server = None
    task = writer = None
    if kind == 'coding':
        settings = tmp_path / 'settings.json'
        settings.write_text(json.dumps({'extensions': ['uiclear'], 'extension_dir': str(root)}))
        store = SessionManager(tmp_path / 'sessions')
        session = AgentSession(agent=Agent(stream, config=AgentConfig(model='test/model')),
            session_data=store.create_session(working_dir=tmp_path), session_manager=store,
            settings=SettingsManager(global_path=settings), working_dir=tmp_path, tool_registry=None, bash=None,
            permissions=PermissionManager('ask'))
        ui = InteractiveMode(session)
    else:
        config = ClawConfig(model='test/model', data_dir=str(tmp_path / 'data'),
            socket_path=str(tmp_path / 'ui.sock'), pid_file=str(tmp_path / 'ui.pid'),
            groups=(GroupDef(id='main', is_main=True),), extensions=('uiclear',), extension_dir=str(root))
        server = GatewayServer(config)
        with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=('test/model', stream)):
            await server.start()
        ui = ChatApp(config.socket_path)
        reader, writer = await asyncio.open_unix_connection(config.socket_path)
        task = asyncio.create_task(ui._run_connection(reader, writer))
    async def wait(predicate):
        async with asyncio.timeout(8):
            while not predicate():
                ui._poll()
                await asyncio.sleep(0.003)
    async def command(name):
        if kind == 'coding':
            ui._handle_slash_command('ext-uiclear-' + name, '')
            await wait(lambda: ui._command is None)
        else:
            ui._extension_command('ext-uiclear-' + name, '')
            await wait(lambda: ui._extension_pending is None)
    try:
        if kind == 'claw':
            await wait(lambda: ui._connection_ready)
        ui._editor.set_text('kept UI draft')
        await command('show')
        rows = '\n'.join(ui._extension_view.render(80))
        assert 'STATUS-LIVE' in rows and 'WIDGET-LIVE' in rows
        if kind == 'claw':
            session = server._orchestrator._runtimes['main']._active_session
        process = session.extensions._manager._clients['uiclear'].process
        await command('hide')
        assert not ui._extension_view.render(80) and not session.extensions.snapshot()['ui']
        await command('hide')
        await command('show')
        assert 'STATUS-LIVE' in '\n'.join(ui._extension_view.render(80))
        await command('collide')
        assert session.extensions.snapshot()['ui']['uiclear:same']['kind'] == 'widget'
        assert session.extensions._manager._clients['uiclear'].process is process
        assert ui._editor.get_text() == 'kept UI draft' and ui._tui._focused is ui._editor
        assert not calls
        if kind == 'coding':
            assert session.permissions.mode == 'ask'
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
