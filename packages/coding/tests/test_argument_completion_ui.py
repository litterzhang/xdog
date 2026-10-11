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
@pytest.mark.parametrize('operation', ['select', 'escape', 'blur', 'exit'])
async def test_actual_both_client_argument_editor_selects_without_submission_or_side_effects(
    tmp_path, kind, operation,
):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/argcomplete',
                    root / 'argcomplete')
    marker = tmp_path / 'completion-entered'
    settings_flags = {'marker': str(marker)}
    calls = []
    def stream(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())
    server = task = writer = None
    if kind == 'coding':
        settings = tmp_path / 'settings.json'
        settings.write_text(json.dumps({'extensions': ['argcomplete'], 'extension_dir': str(root),
                                         'extension_settings': {'argcomplete': settings_flags}}))
        manager = SessionManager(tmp_path / 'sessions')
        session = AgentSession(agent=Agent(stream, config=AgentConfig(model='offline/model')),
            session_data=manager.create_session(working_dir=tmp_path), session_manager=manager,
            settings=SettingsManager(global_path=settings), working_dir=tmp_path, tool_registry=None, bash=None,
            permissions=PermissionManager('deny'))
        ui = InteractiveMode(session)
    else:
        config = ClawConfig(model='offline/model', data_dir=str(tmp_path / 'data'),
            socket_path=str(tmp_path / 'help.sock'), pid_file=str(tmp_path / 'help.pid'),
            groups=(GroupDef(id='main', is_main=True),), extensions=('argcomplete',), extension_dir=str(root),
            extension_settings={'argcomplete': settings_flags})
        server = GatewayServer(config)
        with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=(config.model, stream)):
            await server.start()
        ui = ChatApp(config.socket_path)
        reader, writer = await asyncio.open_unix_connection(config.socket_path, limit=4 * 1024 * 1024)
        task = asyncio.create_task(ui._run_connection(reader, writer))
    async def wait(predicate):
        try:
            async with asyncio.timeout(8):
                while not predicate():
                    ui._poll()
                    await asyncio.sleep(0.003)
        except TimeoutError as exc:
            raise AssertionError({'status': ui._argument_feed._status, 'rows': ui._argument_feed._rows,
                'focus': ui._editor.focused, 'feed_request': ui._argument_feed._request,
                'inflight': ui._argument_feed._inflight, 'extension': session.extensions.snapshot()}) from exc
    try:
        if kind == 'coding':
            await session.initialize_extensions()
        else:
            await wait(lambda: ui._connection_ready)
            ui._extension_command('extensions', '')
            await wait(lambda: ui._extension_pending is None)
            session = server._orchestrator._runtimes['main']._active_session
        from xdog.tui.keys import KeyEvent
        actor, messages, model = session.agent, session.agent.state.messages, session.agent.state.model
        if kind == 'claw':
            assert ui._argument_supported
        if operation != 'select':
            process = session.extensions._manager._clients['argcomplete'].process
            query = session.extensions.complete_command
            admitted, canceled = [], []
            async def long_deadline(*args, **kwargs):
                # Distinguish explicit cancellation from the ordinary one-second timeout.
                # Actual registered worker/bridge still owns all cancellation cleanup.
                admitted.append(asyncio.current_task())
                try:
                    return await query(*args, **{**kwargs, 'timeout': 5})
                except asyncio.CancelledError:
                    canceled.append(True)
                    raise
            session.extensions.complete_command = long_deadline
            ui._editor.set_text('/ext-argcomplete-deploy hold')
            await wait(marker.exists)
            assert ui._argument_feed._inflight is not None
            if operation == 'escape':
                ui._tui._dispatch_input(KeyEvent('escape'))
                ui._tui._dispatch_input(KeyEvent('escape'))  # Do not repeatedly interrupt worker cleanup.
            elif operation == 'blur':
                ui._editor.focused = False
            else:
                ui._request_exit()
                if kind == 'claw':
                    # Direct socket fixture owns the transport teardown that the
                    # production _io_loop_async performs on application exit.
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    writer.close()
                    await writer.wait_closed()
            await wait(lambda: process.returncode is not None and canceled)
            assert admitted and admitted[0].cancelling() >= 1
            assert ui._editor.get_text() == '/ext-argcomplete-deploy hold'
            assert not ui._argument_feed._rows and not calls
            assert session.agent is actor and session.agent.state.messages is messages
            assert not session.extensions.command_completions()
            if kind == 'coding':
                await wait(lambda: not ui._argument_jobs)
                assert session.permissions.mode == 'deny'
            else:
                await wait(lambda: not server._orchestrator.is_group_running('main'))
                if operation != 'exit':
                    await wait(lambda: not ui._argument_requests)
                    assert not ui._extension_snapshot.get('command_completions')
            return
        ui._editor.set_text('/ext-argcomplete-deploy a')
        await wait(lambda: ui._editor._select_list is not None)
        assert 'ALPHA [alpha]' in str(ui._editor.render(100))
        ui._tui._dispatch_input(KeyEvent('enter'))
        assert ui._editor.get_text() == '/ext-argcomplete-deploy alpha'
        counts = json.loads(await session.extensions.command('ext-argcomplete-counts', ''))
        assert counts['executed'] == 0 and counts['completed'] >= 1
        assert session.agent is actor and session.agent.state.messages is messages
        assert session.agent.state.model == model and not calls
        assert ui._extension_browser is None and ui._extension_navigation is None
        ui._editor.set_text('/ext-argcomplete-deploy b')
        await wait(lambda: ui._editor._select_list is not None)
        ui._tui._dispatch_input(KeyEvent('tab'))
        assert ui._editor.get_text() == '/ext-argcomplete-deploy beta'
        counts = json.loads(await session.extensions.command('ext-argcomplete-counts', ''))
        assert counts['executed'] == 0
        ui._editor.set_text('/ext-argcomplete-deploy label')
        await wait(lambda: ui._editor._select_list is not None)
        painted = str(ui._editor.render(120))
        assert '🚀 发布候选 [release-1]' in painted and '🚀 发布候选 [release-2]' in painted
        ui._tui._dispatch_input(KeyEvent('down'))
        ui._tui._dispatch_input(KeyEvent('enter'))
        assert ui._editor.get_text() == '/ext-argcomplete-deploy release-2'
        counts = json.loads(await session.extensions.command('ext-argcomplete-counts', ''))
        assert counts['executed'] == 0 and not calls
        if kind == 'claw':
            assert ui._argument_context_supported
        text = '/ext-argcomplete-deploy production enTAIL later'
        ui._editor.set_text(text)
        ui._editor._cursor = text.index('enTAIL') + 2
        ui._editor.poll_autocomplete()
        await wait(lambda: ui._editor._select_list is not None)
        assert 'Endpoint for production' in str(ui._editor.render(120))
        ui._tui._dispatch_input(KeyEvent('tab'))
        assert ui._editor.get_text() == '/ext-argcomplete-deploy production endpoint-production later'
        text = '/ext-argcomplete-deploy staging enTAIL later'
        ui._editor.set_text(text)
        ui._editor._cursor = text.index('enTAIL') + 2
        ui._editor.poll_autocomplete()
        await wait(lambda: ui._editor._select_list is not None)
        ui._tui._dispatch_input(KeyEvent('enter'))
        assert ui._editor.get_text() == '/ext-argcomplete-deploy staging endpoint-staging later'
        for token, replacement in (
            (r'release\ canTAIL', r'release\ candidate'),
            ('"release canTAIL"', '"release candidate"'),
            ("'release\\ canTAIL'", "'release\\ candidate'"),
        ):
            text = '/ext-argcomplete-deploy literal ' + token + ' later'
            ui._editor.set_text(text)
            ui._editor._cursor = text.index('canTAIL') + 3
            ui._editor.poll_autocomplete()
            await wait(lambda: ui._editor._select_list is not None)
            assert 'Literal complete token' in str(ui._editor.render(120))
            ui._tui._dispatch_input(KeyEvent('tab'))
            assert ui._editor.get_text() == '/ext-argcomplete-deploy literal ' + replacement + ' later'
            ui._tui._dispatch_input(KeyEvent('z', alt=True))
            assert ui._editor.get_text() == text
        counts = json.loads(await session.extensions.command('ext-argcomplete-counts', ''))
        assert counts['executed'] == 0 and not calls
        if kind == 'coding':
            assert session.permissions.mode == 'deny'
    finally:
        ui._request_exit()
        if kind == 'coding':
            # Match native InteractiveMode.run teardown: original completion threads
            # own deferred dispose; join them before direct fixture async cleanup.
            with ui._argument_jobs_lock:
                threads = tuple(ui._argument_threads)
            await asyncio.gather(*(asyncio.to_thread(thread.join, 5) for thread in threads))
            assert not any(thread.is_alive() for thread in threads)
            await session.children.supervisor.aclose()
            await session.extensions.aclose()
            session.dispose()
        else:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            writer.close()
            await writer.wait_closed()
            await server.stop()
