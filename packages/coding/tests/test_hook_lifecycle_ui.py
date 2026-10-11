from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.ai.types import AssistantMessage, TextContent
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
async def test_actual_ui_live_hook_enable_dispose_reenable_same_worker_and_no_implicit_turn(tmp_path, kind):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions', root)
    calls = []
    def stream(model, context, options):
        calls.append(context)
        return EventStream.empty(AssistantMessage(content=(TextContent(text='HOOK-TURN-DONE'),)))
    server = None
    task = writer = None
    if kind == 'coding':
        settings = tmp_path / 'settings.json'
        settings.write_text(json.dumps({'extensions': ['hookmock'], 'extension_dir': str(root)}))
        store = SessionManager(tmp_path / 'sessions')
        session = AgentSession(agent=Agent(stream, config=AgentConfig(model='test/model')),
            session_data=store.create_session(working_dir=tmp_path), session_manager=store,
            settings=SettingsManager(global_path=settings), working_dir=tmp_path, tool_registry=None, bash=None,
            permissions=PermissionManager('ask'))
        ui = InteractiveMode(session)
    else:
        config = ClawConfig(model='test/model', data_dir=str(tmp_path / 'data'),
            socket_path=str(tmp_path / 'hooks.sock'), pid_file=str(tmp_path / 'hooks.pid'),
            groups=(GroupDef(id='main', is_main=True),), extensions=('hookmock',), extension_dir=str(root))
        server = GatewayServer(config)
        with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=('test/model', stream)):
            await server.start()
        ui = ChatApp(config.socket_path)
        reader, writer = await asyncio.open_unix_connection(config.socket_path)
        task = asyncio.create_task(ui._run_connection(reader, writer))
    async def wait(predicate):
        async with asyncio.timeout(10):
            while not predicate():
                ui._poll()
                await asyncio.sleep(0.003)
    async def command(name):
        if kind == 'coding':
            ui._handle_slash_command('ext-hookmock-' + name, '')
            await wait(lambda: ui._command is None)
        else:
            ui._extension_command('ext-hookmock-' + name, '')
            await wait(lambda: ui._extension_pending is None)
    async def prompt(text, count):
        ui._handle_submit(text)
        await wait(lambda: len(calls) == count and (not ui._worker_active and not ui._is_busy if kind == 'coding'
                                                  else ui._active_run_id is None and not ui._waiting))
    def input_text(context):
        last = context.messages[-1]
        return last.content if isinstance(last.content, str) else ''.join(part.text for part in last.content)
    try:
        if kind == 'claw':
            await wait(lambda: ui._connection_ready)
        ui._editor.set_text('preserved draft')
        await command('enable')
        if kind == 'claw':
            session = server._orchestrator._runtimes['main']._active_session
        process = session.extensions._manager._clients['hookmock'].process
        assert session.extensions._infos['hookmock']['events'] == ['turn_start']
        assert not calls and ui._editor.get_text() == 'preserved draft'
        await prompt('first', 1)
        assert input_text(calls[-1]) == 'LISTENER-first'
        before = json.loads(await session.extensions.command('ext-hookmock-status', ''))
        assert before['event'] == 1 and before['input'] == 1 and before['context'] > 0
        await command('disable')
        assert 'hookmock' not in session.extensions._input_required
        await prompt('second', 2)
        assert input_text(calls[-1]) == 'second'
        assert json.loads(await session.extensions.command('ext-hookmock-status', '')) == before
        await command('enable')
        await command('enable')
        await command('old')
        await prompt('third', 3)
        assert input_text(calls[-1]) == 'LISTENER-third'
        assert session.extensions._manager._clients['hookmock'].process is process and process.returncode is None
        assert len(calls) == 3  # Explicit user turns only; hook registration/metadata never calls a model.
        assert ui._extension_dialog_panel is None and ui._tui._focused is ui._editor
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
