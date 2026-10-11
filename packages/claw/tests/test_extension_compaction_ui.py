"""Claw reviewed extension compaction retains connection/generation and native ownership."""
from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
from xdog.agent.messages import messages_to_dicts
from xdog.ai.types import AssistantMessage, TextContent
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer
from xdog.tui.keys import KeyEvent, KeyEventType

from packages.coding.tests.test_rpc_compaction import history, summary_stream


@pytest.mark.parametrize('ending', ['apply', 'deny', 'fail', 'cancel', 'old-client', 'withdraw',
                                     'literal-cancel', 'withdraw-summary', 'stale-dispatch', 'after-browser'])
async def test_claw_original_compaction_review_callback_and_native_generation(tmp_path, monkeypatch, ending):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/compactionrequest',
                    root / 'compactionrequest')
    calls = []
    entered, release, stopped = asyncio.Event(), asyncio.Event(), asyncio.Event()
    def stream(model, context, options):
        calls.append(context)
        return EventStream.empty(AssistantMessage(content=(TextContent(text='Claw reviewed summary'),)))
    selected_stream = summary_stream(entered, release, stopped, calls) if ending == 'withdraw-summary' else stream
    config = ClawConfig(model='offline/model', data_dir=str(tmp_path / 'data'), socket_path=str(tmp_path / 'socket'),
        pid_file=str(tmp_path / 'pid'), groups=(GroupDef(id='main', is_main=True, workspace=str(tmp_path)),),
        extensions=('compactionrequest',), extension_dir=str(root))
    server = GatewayServer(config)
    with patch('xdog.claw.core.runtime.gateway._build_model_and_options',
               return_value=('offline/model', selected_stream)):
        await server.start()
    runtime = server._orchestrator._runtimes['main']
    runtime.flush_runner = None  # This test owns a single real tool-free summarizer invocation.
    owner = runtime.get_or_create_session(allow_reset=False)
    history(owner)
    owner._restore_messages()
    before = messages_to_dicts(owner.agent.messages)
    callback_entered, callback_release = asyncio.Event(), asyncio.Event()
    original_command = owner.extensions.command
    if ending == 'withdraw':
        async def held(*args, **kwargs):
            result = await original_command(*args, **kwargs)
            callback_entered.set()
            await callback_release.wait()
            return result
        monkeypatch.setattr(owner.extensions, 'command', held)
    ui = ChatApp(config.socket_path)
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    connection = asyncio.create_task(ui._run_connection(reader, writer))
    async def wait(predicate):
        async with asyncio.timeout(10):
            while True:
                ui._poll()
                if predicate():
                    return
                await asyncio.sleep(.003)
    try:
        await wait(lambda: ui._connection_ready and ui._extension_compaction_supported)
        original_generation = ui._extension_compaction_generation
        if ending == 'stale-dispatch':
            original_compact = ui._compact_command
            def deferred(instructions, *, extension_generation=None):
                ui._queue_request({'type': 'ping', 'extension_compaction': 0})
                ui._queue_request({'type': 'ping', 'extension_compaction': 1})
                original_compact(instructions, extension_generation=extension_generation)
            monkeypatch.setattr(ui, '_compact_command', deferred)
        if ending == 'old-client':
            ui._queue_request({'type': 'ping', 'extension_compaction': 0})
            await wait(lambda: not ui._extension_compaction_supported)
        ui._editor.set_text('UNSENT CLAW COMPACTION')
        instructions = 'cancel' if ending == 'literal-cancel' else 'Keep COMPACTION-FOCUS'
        ui._extension_command('ext-compactionrequest-request', json.dumps({'instructions': instructions,
            'fail': ending == 'fail', 'after_browser': ending == 'after-browser'}))
        if ending != 'old-client':
            await wait(lambda: ui._extension_dialog_panel is not None)
            panel = ui._extension_dialog_panel
            assert panel.dialog['operation'] == 'compact' and not calls
            assert panel.dialog['compaction_generation'] == original_generation
            if ending == 'cancel':
                panel.handle_input(KeyEvent('escape'))
            else:
                if ending != 'deny':
                    panel.handle_input(KeyEvent('right'))
                panel.handle_input(KeyEvent('enter'))
                for _ in range(160):
                    panel.render(80)
                    if panel.form._visible:
                        break
                    panel.handle_input(KeyEvent('pagedown'))
                assert panel.form._visible and not calls
                panel.handle_input(KeyEvent('enter', event_type=KeyEventType.REPEAT))
                assert not calls
                panel.handle_input(KeyEvent('enter'))
        if ending == 'withdraw':
            await wait(callback_entered.is_set)
            assert not calls
            ui._queue_request({'type': 'ping', 'extension_compaction': 0})
            await wait(lambda: not ui._extension_compaction_supported)
            ui._queue_request({'type': 'ping', 'extension_compaction': 1})
            await wait(lambda: ui._extension_compaction_supported
                       and ui._extension_compaction_generation != original_generation)
            callback_release.set()
        if ending == 'withdraw-summary':
            await wait(entered.is_set)
            assert ui._compact_pending is not None
            ui._queue_request({'type': 'ping', 'extension_compaction': 0})
            await wait(lambda: stopped.is_set() and not ui._extension_compaction_supported)
        await wait(lambda: ui._extension_pending is None and ui._compact_pending is None)
        if ending in {'apply', 'literal-cancel', 'after-browser'}:
            assert len(calls) == 1 and not calls[0].tools and instructions in str(calls[0].messages)
            assert messages_to_dicts(owner.agent.messages) != before
            assert owner._compaction_metadata['recovery_branch_id']
        else:
            assert len(calls) == (1 if ending == 'withdraw-summary' else 0)
            assert messages_to_dicts(owner.agent.messages) == before and owner._compaction_metadata is None
        if ending == 'after-browser':
            assert '"browser": false' in str(ui._chat_log.render(160))
        assert ui._editor.get_text() == 'UNSENT CLAW COMPACTION'
        assert not server._orchestrator.is_group_running('main') and not config.extension_allow_tools
    finally:
        callback_release.set()
        release.set()
        ui._request_exit()
        connection.cancel()
        await asyncio.gather(connection, return_exceptions=True)
        writer.close()
        await writer.wait_closed()
        await server.stop()
