"""Actual Claw native callback review and explicit-user nextTurn admission."""
from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
from xdog.agent.core import CustomAgentMessage
from xdog.ai.types import AssistantMessage, TextContent
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer
from xdog.claw.core.types import SystemInput, UserInput
from xdog.tui.keys import KeyEvent


async def start(tmp_path, calls):
    root = tmp_path / 'extensions'
    if not root.exists():
        shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/contextrequest',
                        root / 'contextrequest')
    def stream(model, context, options):
        calls.append(context)
        return EventStream.empty(AssistantMessage(content=(TextContent(text='CONTEXT-ANSWER'),)))
    config = ClawConfig(model='test/model', data_dir=str(tmp_path / 'data'),
        socket_path=str(tmp_path / 'context.sock'), pid_file=str(tmp_path / 'context.pid'),
        groups=(GroupDef(id='main', is_main=True, workspace=str(tmp_path / 'workspace')),),
        extensions=('contextrequest',), extension_dir=str(root))
    server = GatewayServer(config)
    with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=('test/model', stream)):
        await server.start()
    runtime = server._orchestrator._runtimes['main']
    session = runtime.get_or_create_session(allow_reset=False)
    ui = ChatApp(config.socket_path)
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    task = asyncio.create_task(ui._run_connection(reader, writer))
    return server, runtime, session, ui, task, writer


async def close(server, ui, task, writer):
    ui._request_exit()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    writer.close()
    await writer.wait_closed()
    await server.stop()


async def wait(ui, predicate):
    async with asyncio.timeout(12):
        while True:
            ui._poll()
            if predicate():
                return
            await asyncio.sleep(.003)


def answer(ui, approve):
    panel = ui._extension_dialog_panel
    if approve:
        panel.handle_input(KeyEvent('right'))
    panel.handle_input(KeyEvent('enter'))
    for _ in range(160):
        panel.render(80)
        if panel.form._visible:
            break
        panel.handle_input(KeyEvent('pagedown'))
    assert panel.form._visible
    panel.handle_input(KeyEvent('enter'))


@pytest.mark.parametrize('mode', ['apply', 'deny', 'hidden', 'extension-first', 'system-first', 'restart'])
async def test_claw_real_context_review_waits_for_original_explicit_user_and_survives_restart(tmp_path, mode):
    calls = []
    server, runtime, session, ui, task, writer = await start(tmp_path, calls)
    try:
        await wait(ui, lambda: ui._connection_ready)
        ui._editor.set_text('UNCHANGED PRIVATE DRAFT')
        ui._extension_command('ext-contextrequest-request', json.dumps({'display': mode != 'hidden'}))
        await wait(ui, lambda: ui._extension_dialog_panel is not None or ui._extension_pending is None)
        assert ui._extension_dialog_panel is not None, (
            ui._extension_context_messages_supported, ui._extension_context_messages_generation,
            '\n'.join(ui._chat_log.render(160)), session.extensions.snapshot())
        assert ui._extension_dialog_panel.dialog['operation'] == 'context_message'
        answer(ui, mode != 'deny')
        await wait(ui, lambda: ui._extension_pending is None)
        assert not calls and not session.agent.messages
        assert ui._editor.get_text() == 'UNCHANGED PRIVATE DRAFT'
        if mode == 'restart':
            original = session.meta.session_id
            await close(server, ui, task, writer)
            server, runtime, session, ui, task, writer = await start(tmp_path, calls)
            await wait(ui, lambda: ui._connection_ready)
            assert session.meta.session_id == original and not calls
        elif mode in {'system-first', 'extension-first'}:
            value = (SystemInput(group_id='main', content='SCHEDULER') if mode == 'system-first'
                     else UserInput(group_id='main', content='REQUESTED MESSAGE',
                                    metadata={'input_origin': 'extension'}))
            await server._orchestrator.route_message(value)
            assert len(calls) == 1 and 'RETAINED-CUSTOM-CONTENT' not in str(calls[0].messages)
            assert not any(isinstance(item, CustomAgentMessage) for item in session.agent.messages)
        ui._handle_submit('NEXT EXPLICIT USER')
        await wait(ui, lambda: calls and 'NEXT EXPLICIT USER' in str(calls[-1].messages)
                   and not ui._waiting and ui._active_run_id is None)
        custom = [item for item in session.agent.messages if isinstance(item, CustomAgentMessage)]
        if mode == 'deny':
            assert not custom and 'RETAINED-CUSTOM-CONTENT' not in str(calls[-1].messages)
        else:
            assert len(custom) == 1 and custom[0].data['details'] == {'opaque': 'PRIVATE-NOT-MODEL'}
            assert 'RETAINED-CUSTOM-CONTENT' in str(calls[-1].messages)
            assert 'PRIVATE-NOT-MODEL' not in str(calls[-1].messages)
            persisted = runtime.transcript_store.load_transcript(session.meta.session_id)
            assert sum(row.get('role') == 'custom' for row in persisted) == 1
        from xdog.ai.types import UserMessage
        user_index = next(index for index, item in enumerate(session.agent.messages)
                          if isinstance(item, UserMessage) and 'NEXT EXPLICIT USER' in str(item.content))
        assert session._source_channels[user_index] == 'tui'
        assert not (await session.context_messages.capture())[1]
    finally:
        await close(server, ui, task, writer)


@pytest.mark.parametrize('hook', ['turn_start', 'input'])
async def test_claw_context_proposed_inside_current_user_hooks_waits_for_following_user(tmp_path, hook):
    root = tmp_path / 'extensions/contextrequest'
    root.mkdir(parents=True)
    (root / 'extension.yaml').write_text('name: contextrequest\nentry_point: main\n')
    (root / 'main.py').write_text('''
def setup(api):
    requested = False
    async def propose():
        nonlocal requested
        if not requested:
            requested = True
            assert await api.request_context_message('hook-note', 'HOOK-NEXT-TURN-ONLY',
                                                     details={'private': 'NOT MODEL'})
    async def event(context):
        await propose()
    async def input_hook(text, metadata, context):
        await propose()
        return {'action': 'continue'}
    ''' + ("api.on('turn_start', event)" if hook == 'turn_start' else 'api.on_input(input_hook)') + '\n')
    calls = []
    server, runtime, session, ui, task, writer = await start(tmp_path, calls)
    try:
        await wait(ui, lambda: ui._connection_ready)
        ui._handle_submit('FIRST EXPLICIT USER')
        await wait(ui, lambda: ui._extension_dialog_panel is not None)
        answer(ui, True)
        await wait(ui, lambda: len(calls) == 1 and ui._active_run_id is None and not ui._waiting)
        assert 'HOOK-NEXT-TURN-ONLY' not in str(calls[0].messages)
        assert not any(isinstance(item, CustomAgentMessage) for item in session.agent.messages)
        ui._handle_submit('SECOND EXPLICIT USER')
        await wait(ui, lambda: len(calls) == 2 and ui._active_run_id is None and not ui._waiting)
        assert 'HOOK-NEXT-TURN-ONLY' in str(calls[1].messages)
        assert 'NOT MODEL' not in str(calls[1].messages)
        assert sum(isinstance(item, CustomAgentMessage) for item in session.agent.messages) == 1
    finally:
        await close(server, ui, task, writer)
