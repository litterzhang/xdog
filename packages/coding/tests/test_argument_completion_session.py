"""Actual host command-menu API uses the same registered native terminal menus, never executes entries."""
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
async def test_actual_both_client_sessions_complete_registered_arguments_without_executing_commands(tmp_path, kind):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/argcomplete',
                    root / 'argcomplete')
    calls = []
    def stream(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())
    server = task = writer = None
    if kind == 'coding':
        settings = tmp_path / 'settings.json'
        settings.write_text(json.dumps({'extensions': ['argcomplete'], 'extension_dir': str(root)}))
        manager = SessionManager(tmp_path / 'sessions')
        session = AgentSession(agent=Agent(stream, config=AgentConfig(model='offline/model')),
            session_data=manager.create_session(working_dir=tmp_path), session_manager=manager,
            settings=SettingsManager(global_path=settings), working_dir=tmp_path, tool_registry=None, bash=None,
            permissions=PermissionManager('deny'))
        ui = InteractiveMode(session)
    else:
        config = ClawConfig(model='offline/model', data_dir=str(tmp_path / 'data'),
            socket_path=str(tmp_path / 'help.sock'), pid_file=str(tmp_path / 'help.pid'),
            groups=(GroupDef(id='main', is_main=True),), extensions=('argcomplete',), extension_dir=str(root))
        server = GatewayServer(config)
        with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=(config.model, stream)):
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
            await session.initialize_extensions()
        else:
            await wait(lambda: ui._connection_ready)
            ui._extension_command('extensions', '')
            await wait(lambda: ui._extension_pending is None)
            session = server._orchestrator._runtimes['main']._active_session
        actor, messages, model = session.agent, session.agent.state.messages, session.agent.state.model
        ui._editor.set_text('ARGUMENTS-RETAINED-DRAFT')
        key = 'ext-argcomplete-deploy'
        identity = session.extensions.command_completions()[key]
        result = await session.complete_command(key, 'a', identity)
        assert result == {'status': 'ok', 'items': [
            {'value': 'alpha', 'label': 'ALPHA', 'description': 'Completion data only'}]}
        counts = json.loads(await session.extensions.command('ext-argcomplete-counts', ''))
        assert counts == {'executed': 0, 'completed': 1}
        assert session.agent is actor and session.agent.state.messages is messages
        assert session.agent.state.model == model and not calls
        assert ui._editor.get_text() == 'ARGUMENTS-RETAINED-DRAFT'
        assert ui._extension_browser is None and ui._extension_navigation is None
        if kind == 'coding':
            assert session.permissions.mode == 'deny'
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
