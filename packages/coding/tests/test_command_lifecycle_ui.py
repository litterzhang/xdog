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
async def test_actual_live_command_catalog_replacement_binding_disposal_and_parent_ownership(tmp_path, kind):
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
        settings.write_text(json.dumps({'extensions': ['commandmock'], 'extension_dir': str(root)}))
        store = SessionManager(tmp_path / 'sessions')
        session = AgentSession(agent=Agent(stream, config=AgentConfig(model='test/model')),
            session_data=store.create_session(working_dir=tmp_path), session_manager=store,
            settings=SettingsManager(global_path=settings), working_dir=tmp_path, tool_registry=None, bash=None,
            permissions=PermissionManager('ask'))
        ui = InteractiveMode(session)
    else:
        config = ClawConfig(model='test/model', data_dir=str(tmp_path / 'data'),
            socket_path=str(tmp_path / 'commands.sock'), pid_file=str(tmp_path / 'commands.pid'),
            groups=(GroupDef(id='main', is_main=True),), extensions=('commandmock',), extension_dir=str(root))
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
            ui._handle_slash_command('ext-commandmock-' + name, '')
            await wait(lambda: ui._command is None)
        else:
            ui._extension_command('ext-commandmock-' + name, '')
            await wait(lambda: ui._extension_pending is None)
    async def shortcut():
        assert ui._handle_global_input(KeyEvent('f6')) == {'consume': True}
        await wait(lambda: ui._command is None if kind == 'coding' else ui._extension_pending is None)
    try:
        if kind == 'claw':
            await wait(lambda: ui._connection_ready)
        ui._editor.set_text('kept catalog draft')
        await command('enable')
        assert ui._extension_snapshot['commands']['ext-commandmock-target'] == 'Dynamic target'
        assert 'ext-commandmock-target' in ui._editor._command_provider()
        assert ui._effective_extension_shortcuts().command(KeyEvent('f6')) == 'ext-commandmock-target'
        if kind == 'claw':
            session = server._orchestrator._runtimes['main']._active_session
        process = session.extensions._manager._clients['commandmock'].process
        await shortcut()
        assert 'LIVE-COMMAND-ORIGINAL-1' in '\n'.join(ui._chat_log.render(200))
        await command('replace')
        await shortcut()
        assert 'LIVE-COMMAND-REPLACED-2' in '\n'.join(ui._chat_log.render(200))
        await command('disable')
        assert 'ext-commandmock-target' not in ui._extension_snapshot['commands']
        assert 'ext-commandmock-target' not in ui._editor._command_provider()
        assert ui._effective_extension_shortcuts().command(KeyEvent('f6')) is None
        await command('enable')
        await command('old')
        await shortcut()
        assert 'LIVE-COMMAND-ORIGINAL-3' in '\n'.join(ui._chat_log.render(200))
        assert session.extensions._manager._clients['commandmock'].process is process
        assert ui._editor.get_text() == 'kept catalog draft' and ui._tui._focused is ui._editor
        assert not calls and ui._extension_dialog_panel is None
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
