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
async def test_actual_extension_reads_complete_paged_menu_and_filtered_ui_actions_without_execution(tmp_path, kind):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/commandreader',
                    root / 'commandreader')
    calls = []
    def stream(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())
    server = task = writer = None
    if kind == 'coding':
        settings = tmp_path / 'settings.json'
        settings.write_text(json.dumps({'extensions': ['commandreader'], 'extension_dir': str(root)}))
        manager = SessionManager(tmp_path / 'sessions')
        session = AgentSession(agent=Agent(stream, config=AgentConfig(model='offline/model')),
            session_data=manager.create_session(working_dir=tmp_path), session_manager=manager,
            settings=SettingsManager(global_path=settings), working_dir=tmp_path, tool_registry=None, bash=None,
            permissions=PermissionManager('deny'))
        ui = InteractiveMode(session)
    else:
        config = ClawConfig(model='offline/model', data_dir=str(tmp_path / 'data'),
            socket_path=str(tmp_path / 'help.sock'), pid_file=str(tmp_path / 'help.pid'),
            groups=(GroupDef(id='main', is_main=True),), extensions=('commandreader',), extension_dir=str(root))
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
        ui._editor.set_text('CATALOG-RETAINED-DRAFT')
        if kind == 'coding':
            ui._handle_slash_command('ext-commandreader-inspect', '')
            await wait(lambda: ui._command is None)
        else:
            assert ui._extension_command_catalog_supported
            ui._extension_command('ext-commandreader-inspect', '')
            await wait(lambda: ui._extension_pending is None)
        records = [row['data'] for row in session.extensions.entries.for_extension('commandreader')
                   if row['kind'] == 'menu']
        assert len(records) == 1 and records[0]['count'] > 32 and records[0]['executed'] == 0
        expected = session.session_id if kind == 'coding' else session.meta.session_id
        assert records[0]['source'] == 'terminal-menu' and records[0]['session_id'] == expected
        names = records[0]['names']
        assert '/help' in names and '/copy' in names and '/keys' in names and '/editor' in names
        assert '/ext-commandreader-item24' in names
        if kind == 'coding':
            ui._handle_slash_command('ext-commandreader-inspect', 'COPY')
            await wait(lambda: ui._command is None)
        else:
            ui._extension_command('ext-commandreader-inspect', 'COPY')
            await wait(lambda: ui._extension_pending is None)
        records = [row['data'] for row in session.extensions.entries.for_extension('commandreader')
                   if row['kind'] == 'menu']
        assert len(records) == 2 and '/copy' in records[-1]['names']
        assert '/ext-commandreader-item24' not in records[-1]['names']
        tool = session.extensions.tools()[0]
        # Existing dialog/editor grants alone cannot lend the new independent
        # read-only host menu. Positive noncommand scopes have separate tests.
        menu_handler = session.extensions.command_menu_handler
        session.extensions.command_menu_handler = None
        try:
            result = await tool.execute('no-menu-authority', {})
            assert result.content[0].text == 'TOOL-MENU-UNAVAILABLE'
        finally:
            session.extensions.command_menu_handler = menu_handler
        assert session.agent is actor and session.agent.state.messages is messages
        assert session.agent.state.model == model
        assert ui._editor.get_text() == 'CATALOG-RETAINED-DRAFT' and not calls
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
