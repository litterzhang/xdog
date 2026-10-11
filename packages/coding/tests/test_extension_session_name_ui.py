"""Actual worker commands review and defer metadata CAS in both owning applications."""
from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.ai.types import AssistantMessage, UserMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.persistence.transcript_store import TranscriptStore
from xdog.claw.core.runtime.gateway import GatewayServer
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.permissions import PermissionManager
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.settings_manager import SettingsManager
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
from xdog.tui.keys import KeyEvent


@pytest.mark.parametrize('kind', ['coding', 'claw'])
@pytest.mark.parametrize('scenario', ['save', 'clear', 'dash', 'deny', 'fail', 'stale', 'cancel', 'expired'])
async def test_actual_extension_name_inspection_review_cas_and_callback_failure_keep_history_owner(
    tmp_path, kind, scenario, monkeypatch,
):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/namer', root / 'namer')
    marker, release = tmp_path / 'queued', tmp_path / 'release'
    flags = {'marker': str(marker), 'release': str(release)}
    calls = []
    def stream(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())
    server = task = writer = None
    if kind == 'coding':
        settings = tmp_path / 'settings.json'
        settings.write_text(json.dumps({'extensions': ['namer'], 'extension_dir': str(root),
                                        'extension_settings': {'namer': flags}}))
        manager = SessionManager(tmp_path / 'sessions')
        session = AgentSession(agent=Agent(stream, config=AgentConfig(model='offline/model')),
            session_data=manager.create_session(working_dir=tmp_path), session_manager=manager,
            settings=SettingsManager(global_path=settings), working_dir=tmp_path, tool_registry=None, bash=None,
            permissions=PermissionManager('deny'))
        session.agent.replace_messages([UserMessage(content='NAME-HISTORY-UNCHANGED')])
        session._persist()
        session.rename_session('Original')
        ui = InteractiveMode(session)
    else:
        config = ClawConfig(model='offline/model', data_dir=str(tmp_path / 'data'),
            socket_path=str(tmp_path / 'name.sock'), pid_file=str(tmp_path / 'name.pid'),
            groups=(GroupDef(id='main', is_main=True),), extensions=('namer',), extension_dir=str(root),
            extension_settings={'namer': flags})
        manager = TranscriptStore(tmp_path / 'data/groups/main/sessions', owner_group='main')
        meta = manager.create_session('main')
        manager.replace_transcript(meta.session_id, [{'role': 'user', 'content': 'NAME-HISTORY-UNCHANGED'}])
        manager.rename_session('main', meta.session_id, 'Original')
        server = GatewayServer(config)
        with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=(config.model, stream)):
            await server.start()
        runtime = server._orchestrator._runtimes['main']
        manager = runtime.transcript_store
        session = runtime.get_or_create_session(allow_reset=False)
        await session.initialize_extensions()
        session._restore_messages()
        ui = ChatApp(config.socket_path)
        reader, writer = await asyncio.open_unix_connection(config.socket_path, limit=4 * 1024 * 1024)
        task = asyncio.create_task(ui._run_connection(reader, writer))
    async def wait(predicate):
        async with asyncio.timeout(10):
            while not predicate():
                ui._poll()
                await asyncio.sleep(0.003)
    def named():
        return (manager.load_session(session.session_id).name if kind == 'coding'
                else manager.get_active_session('main').label)
    def command(name, args=''):
        if kind == 'coding':
            ui._handle_slash_command('ext-namer-' + name, args)
        else:
            ui._extension_command('ext-namer-' + name, args)
    def settled():
        return (ui._command is None if kind == 'coding' else
                ui._extension_pending is None and ui._pending_command is None)
    try:
        if kind == 'claw':
            await wait(lambda: ui._connection_ready)
            assert ui._extension_name_supported
            ui._state['session_name'] = 'FORGED-STALE-VIEWER-CACHE'
        else:
            await session.initialize_extensions()
        owner_agent, owner = session.agent, session.agent.state
        plan, extensions, children = session.plan, session.extensions, session.children
        ui._editor.set_text('NAME-COMMAND-DRAFT')
        command('inspect')
        await wait(settled)
        assert 'NAME-INSPECT {"name": "Original"}' in str(ui._chat_log.render(160))
        target = '' if scenario == 'clear' else '-' if scenario == 'dash' else 'Reviewed-开发'
        command('rename', json.dumps({'name': target, 'fail': scenario == 'fail', 'hold': scenario == 'stale'}))
        await wait(lambda: ui._extension_dialog_panel is not None)
        panel = ui._extension_dialog_panel
        assert panel.dialog['name'] == target and panel.dialog['expected_name'] == 'Original'
        assert named() == 'Original'
        late_acks = []
        if scenario == 'expired':
            broker = session.extension_dialogs
            original_resolve = broker.resolve
            def late_answer(key, value):
                if value is True:
                    _dialog, loop, future = broker._pending[key]
                    def lose_to_cancellation():
                        future.set_result(None)
                        late_acks.append(original_resolve(key, value))
                    loop.call_soon_threadsafe(lose_to_cancellation)
                    return True
                return original_resolve(key, value)
            monkeypatch.setattr(broker, 'resolve', late_answer)
        if scenario == 'cancel':
            panel.handle_input(KeyEvent('escape'))
        else:
            if scenario != 'deny':
                panel.handle_input(KeyEvent('right'))
            panel.handle_input(KeyEvent('enter'))
            for _ in range(100):
                panel.render(80)
                if panel.form._visible:
                    break
                panel.handle_input(KeyEvent('pagedown'))
            assert panel.form._visible
            panel.handle_input(KeyEvent('enter'))
        if scenario == 'stale':
            await wait(marker.exists)
            assert named() == 'Original'  # Reviewed is queued, not already persisted.
            if kind == 'coding':
                session.rename_session('Concurrent')
            else:
                runtime.rename_session(session.meta.session_id, 'Concurrent')
            release.touch()
        await wait(settled)
        if scenario == 'expired':
            assert late_acks == [True]
            assert ui._extension_navigation is None and ui._extension_navigation_review is None
            assert '"accepted": false' in str(ui._chat_log.render(160))
        expected = (target if scenario in {'save', 'clear', 'dash'} else
                    'Concurrent' if scenario == 'stale' else 'Original')
        assert named() == expected and session.agent is owner_agent
        assert session.agent.state.messages is owner.messages and session.agent.state.model == owner.model
        assert session.agent.state.system_prompt == owner.system_prompt
        assert [(tool.name, tool.parameters) for tool in session.agent.state.tools] == [
            (tool.name, tool.parameters) for tool in owner.tools
            if scenario != 'fail' or tool.name not in session.extensions.aliases]
        if scenario == 'fail':
            assert not session.extensions.tools()  # Failed registrations withdraw; no policy/state fallback.
        assert session.plan is plan and session.extensions is extensions and session.children is children
        assert 'NAME-HISTORY-UNCHANGED' in str(session.agent.state.messages)
        assert ui._editor.get_text() == 'NAME-COMMAND-DRAFT' and not calls
        if scenario == 'fail':
            assert session.extensions.snapshot()['extensions'][0]['state'] == 'failed'
        else:
            tool = next(tool for tool in session.extensions.tools() if tool.name.startswith('ext_namer_inspect'))
            result = await tool.execute('headless-inspect', {})
            assert json.loads(result.content[0].text.removeprefix('NAME-TOOL '))['name'] == expected
            assert named() == expected  # Tools can inspect but cannot borrow rename review authority.
        if kind == 'coding':
            assert session.permissions.mode == 'deny'
    finally:
        release.touch()
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
