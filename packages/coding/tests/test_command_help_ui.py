"""Read-only help is derived from both actual clients' currently registered completion menus."""
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
async def test_help_lists_and_filters_live_extension_and_ui_commands_without_activation_or_execution(tmp_path, kind):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/commandcollision',
                    root / 'commandcollision')
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
            socket_path=str(tmp_path / 'help.sock'), pid_file=str(tmp_path / 'help.pid'),
            groups=(GroupDef(id='main', is_main=True),), extensions=('commandcollision',), extension_dir=str(root))
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
    def help(query=''):
        if kind == 'coding':
            ui._handle_slash_command('help', query)
        else:
            ui._handle_submit('/help' + (' ' + query if query else ''))
    try:
        if kind == 'coding':
            await session.initialize_extensions()
        else:
            await wait(lambda: ui._connection_ready)
            ui._extension_command('extensions', '')
            await wait(lambda: ui._extension_pending is None)
            session = server._orchestrator._runtimes['main']._active_session
        actor, messages, model = session.agent, session.agent.state.messages, session.agent.state.model
        ui._editor.set_text('HELP-RETAINED-DRAFT')
        help()
        text = str(ui._chat_log.render(180))
        assert '/copy' in text and '/keys' in text and '/editor' in text and '/help' in text
        assert '/ext-commandcollision-show' in text and '/ext-commandcollision-shows' in text
        previous = len(ui._chat_log.children)
        if kind == 'coding':
            ui._worker_active = True
            ui._set_busy(True)
            timer = ui._busy_started
        else:
            ui._active_run_id = 'help-does-not-own-model'
            ui._set_waiting(True)
            timer = ui._status_started
        help('COMMANDCOLLISION')
        filtered = '\n'.join(line for child in ui._chat_log.children[previous:] for line in child.render(180))
        assert '3/' in filtered and '/ext-commandcollision-counts' in filtered
        assert '/copy —' not in filtered
        if kind == 'coding':
            assert ui._worker_active and ui._busy_started == timer
            ui._worker_active = False
            ui._set_busy(False)
        else:
            assert ui._active_run_id == 'help-does-not-own-model' and ui._status_started == timer
            ui._active_run_id = None
            ui._set_waiting(False)
        counts = json.loads(await session.extensions.command('ext-commandcollision-counts', ''))
        assert counts == {'show': 0, 'shows': 0}
        assert not calls and ui._editor.get_text() == 'HELP-RETAINED-DRAFT'
        assert session.agent is actor and session.agent.state.messages is messages
        assert session.agent.state.model == model
        assert ui._command is None if kind == 'coding' else ui._extension_pending is None
        # Explicit unload withdraws the current menu; help cannot resurrect its old entries.
        if kind == 'coding':
            await session.extensions.manage('unload', 'commandcollision')
        else:
            ui._extension_command('extensions', 'unload commandcollision')
            await wait(lambda: ui._extension_pending is None)
        before = len(ui._chat_log.children)
        help('commandcollision')
        new = '\n'.join(line for child in ui._chat_log.children[before:] for line in child.render(180))
        assert '0/' in new and 'No matching commands' in new
        assert '/ext-commandcollision-show' not in new and not calls
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
