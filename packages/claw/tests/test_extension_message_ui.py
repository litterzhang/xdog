"""Reviewed extension messages use canonical Claw turns, never the unsent composer."""
from __future__ import annotations

import asyncio
import base64
import json
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest
from xdog.agent.extensions.message_request import host_message
from xdog.ai.types import AssistantMessage, ImageContent, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer
from xdog.tui.attachments import attachment
from xdog.tui.keys import KeyEvent, KeyEventType

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5ioAAAAASUVORK5CYII=')
WORKER = '''import json

def setup(api):
    async def command(args, context):
        options = json.loads(args or '{}')
        queued = await api.request_message(options.get('text', '/reset literal requested text'),
                                           timeout=options.get('timeout', 300))
        after = await api.open_sessions() if options.get('after') else None
        twice = await api.request_message('second message') if options.get('twice') else None
        compact = await api.request_compaction() if options.get('compact') else None
        if options.get('fail'):
            raise RuntimeError('Callback failed after message approval')
        return 'MESSAGE-QUEUED ' + json.dumps({'queued': queued, 'after': after, 'twice': twice, 'compact': compact})

    async def forbidden(params, context):
        return 'ILLEGAL-MESSAGE-' + str(await api.request_message('Tool must not borrow parent owner'))

    api.command('request', 'Request reviewed host message', command)
    api.tool('forbidden', 'Cannot borrow message authority', {'type': 'object'}, forbidden)
'''


@asynccontextmanager
async def message_server(tmp_path, stream=None, *, allow_tools=False):
    root = tmp_path / 'extensions' / 'messagerequest'
    root.mkdir(parents=True)
    (root / 'extension.yaml').write_text('name: messagerequest\nentry_point: main\n')
    (root / 'main.py').write_text(WORKER)
    calls = []
    def default_stream(model, context, options):
        calls.append(context)
        return EventStream.empty(AssistantMessage(content=(TextContent(text='REQUEST-MODEL-DONE'),)))
    config = ClawConfig(model='offline/model', data_dir=str(tmp_path / 'data'),
        socket_path=str(tmp_path / 'socket'), pid_file=str(tmp_path / 'pid'),
        groups=(GroupDef(id='main', is_main=True, workspace=str(tmp_path)),),
        extensions=('messagerequest',), extension_dir=str(root.parent),
        extension_allow_tools=('messagerequest',) if allow_tools else ())
    server = GatewayServer(config)
    with patch('xdog.claw.core.runtime.gateway._build_model_and_options',
               return_value=('offline/model', stream or default_stream)):
        await server.start()
    runtime = server._orchestrator._runtimes['main']
    owner = runtime.get_or_create_session(allow_reset=False)
    try:
        yield server, runtime, owner, config, calls
    finally:
        await asyncio.wait_for(server.stop(), 10)


@asynccontextmanager
async def native_ui(config):
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
        await wait(lambda: ui._connection_ready and ui._extension_message_supported)
        yield ui, wait
    finally:
        ui._request_exit()
        connection.cancel()
        await asyncio.gather(connection, return_exceptions=True)
        writer.close()
        await writer.wait_closed()


def review(panel, approve=True):
    assert panel.dialog['operation'] == 'message'
    if approve:
        panel.handle_input(KeyEvent('right'))
    panel.handle_input(KeyEvent('enter'))
    for _ in range(160):
        panel.render(80)
        if panel.form._visible:
            break
        panel.handle_input(KeyEvent('pagedown'))
    assert panel.form._visible
    panel.handle_input(KeyEvent('enter', event_type=KeyEventType.REPEAT))
    panel.handle_input(KeyEvent('enter'))


@pytest.mark.parametrize('ending', ['apply', 'deny', 'escape', 'fail', 'withdraw', 'withdraw-review',
    'old-client', 'late-ack', 'stale-dispatch', 'foreign-owner', 'expired', 'source-changed', 'agent-changed',
    'twice', 'after', 'compact'])
async def test_native_message_review_lifecycle_and_untouched_draft(tmp_path, monkeypatch, ending):
    async with message_server(tmp_path) as (server, runtime, owner, config, calls):
        entered, release = asyncio.Event(), asyncio.Event()
        original = owner.extensions.command
        if ending in {'withdraw', 'source-changed', 'agent-changed'}:
            async def held(*args, **kwargs):
                value = await original(*args, **kwargs)
                entered.set()
                await release.wait()
                return value
            monkeypatch.setattr(owner.extensions, 'command', held)
        async with native_ui(config) as (ui, wait):
            generation = ui._extension_message_generation
            if ending == 'stale-dispatch':
                start = ui._start_message
                def delayed(value, images, *, requested_message=None):
                    ui._queue_request({'type': 'ping', 'extension_message': 0})
                    ui._queue_request({'type': 'ping', 'extension_message': 1})
                    start(value, images, requested_message=requested_message)
                monkeypatch.setattr(ui, '_start_message', delayed)
            if ending == 'old-client':
                ui._queue_request({'type': 'ping', 'extension_message': 0})
                await wait(lambda: not ui._extension_message_supported)
            ui._editor.set_text('UNSENT requested-message draft')
            ui._editor._cursor = 7
            image = attachment(PNG, 'unsent.png')
            ui._composer.items[image.id] = image
            before_session = owner.meta.session_id
            ui._extension_command('ext-messagerequest-request', json.dumps({
                'text': '/reset literal requested text', 'fail': ending == 'fail',
                'after': ending == 'after', 'twice': ending == 'twice', 'compact': ending == 'compact',
                'timeout': .1 if ending == 'expired' else 300}))
            try:
                if ending != 'old-client':
                    await wait(lambda: ui._extension_dialog_panel is not None)
                    panel = ui._extension_dialog_panel
                    assert panel.dialog['host_message'] == host_message(
                        'messagerequest', '/reset literal requested text')
                    assert panel.dialog['message_generation'] == generation and not calls
                    if ending == 'late-ack':
                        broker = owner.extension_dialogs
                        resolve = broker.resolve
                        def late(key, value):
                            if value is True:
                                _dialog, loop, future = broker._pending[key]
                                def lose():
                                    future.set_result(None)
                                    assert resolve(key, value)
                                loop.call_soon_threadsafe(lose)
                                return True
                            return resolve(key, value)
                        monkeypatch.setattr(broker, 'resolve', late)
                    if ending == 'foreign-owner':
                        ui._extension_editor_owner = (ui._extension_pending[0], 'someoneelse')
                    if ending == 'escape':
                        panel.handle_input(KeyEvent('escape'))
                    elif ending == 'withdraw-review':
                        ui._queue_request({'type': 'ping', 'extension_message': 0})
                    elif ending != 'expired':
                        review(panel, ending != 'deny')
                if ending in {'withdraw', 'source-changed', 'agent-changed'}:
                    await wait(entered.is_set)
                    assert not calls
                    if ending == 'withdraw':
                        ui._queue_request({'type': 'ping', 'extension_message': 0})
                        await wait(lambda: not ui._extension_message_supported)
                        ui._queue_request({'type': 'ping', 'extension_message': 1})
                        await wait(lambda: ui._extension_message_supported
                                   and ui._extension_message_generation != generation)
                    elif ending == 'source-changed':
                        runtime._source_revision += 1
                    else:
                        runtime._active_session = None
                    release.set()
                await wait(lambda: ui._extension_pending is None and ui._active_run_id is None)
                await wait(lambda: not server._orchestrator.is_group_running('main'))
                if ending in {'apply', 'twice', 'after', 'compact'}:
                    assert len(calls) == 1
                    content = str(calls[0].messages)
                    assert host_message('messagerequest', '/reset literal requested text') in ''.join(
                        part.text for message in calls[0].messages for part in message.content
                        if isinstance(part, TextContent))
                    assert 'UNSENT' not in content and 'second message' not in content
                    assert not any(isinstance(part, ImageContent) for message in calls[0].messages
                                   for part in message.content)
                    saved = runtime.transcript_store.load_transcript(before_session)
                    assert 'requested by extension messagerequest' in str(saved)
                    if ending in {'twice', 'after', 'compact'}:
                        assert f'"{ending}": false' in str(ui._chat_log.render(180))
                else:
                    assert not calls
                assert runtime.get_new_session_id() == before_session
                assert ui._editor.get_text() == 'UNSENT requested-message draft' and ui._editor._cursor == 7
                assert ui._composer.items == {image.id: image} and not ui._pending_messages
                assert not config.extension_allow_tools
            finally:
                release.set()
                runtime._active_session = owner


async def test_requested_turn_keeps_normal_extension_tool_permission_gate(tmp_path):
    calls = []
    def stream(model, context, options):
        calls.append(context)
        if len(calls) == 1:
            tool = next(tool for tool in context.tools if tool.name.startswith('ext_messagerequest_'))
            return EventStream.empty(AssistantMessage(content=(ToolCall(id='blocked', name=tool.name, arguments={}),),
                                                       stop_reason='toolUse'))
        return EventStream.empty(AssistantMessage(content=(TextContent(text='PERMISSION-DONE'),)))
    async with message_server(tmp_path, stream) as (_, _, owner, config, _calls):
        async with native_ui(config) as (ui, wait):
            ui._extension_command('ext-messagerequest-request', '{}')
            await wait(lambda: ui._extension_dialog_panel is not None)
            review(ui._extension_dialog_panel)
            await wait(lambda: ui._extension_pending is None and ui._active_run_id is None)
            assert len(calls) == 2
            assert 'Extension tool use disabled' in str(owner.agent.messages)
            assert 'ILLEGAL-MESSAGE-' not in str(owner.agent.messages)
            assert not config.extension_allow_tools
