"""Actual Claw tool callback proposals enter only their reviewed canonical run queue."""
from __future__ import annotations

import asyncio
import json
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
from xdog.ai.types import AssistantMessage, ImageContent, Model, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer
from xdog.tui.attachments import attachment
from xdog.tui.keys import KeyEvent, KeyEventType

from packages.claw.tests.test_extension_message_ui import PNG, native_ui


@asynccontextmanager
async def queued_server(tmp_path, delivery='follow_up', ending='apply'):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/queuedhost', root / 'queuedhost')
    calls = []
    marker, release = tmp_path / 'reviewed', tmp_path / 'release'
    if ending in {'withdraw', 'source', 'cancel', 'receipt-fail', 'disconnect'}:
        source = root / 'queuedhost/main.py'
        source.write_text(source.read_text().replace("        if args.get('ending') == 'fail':", f'''
        from pathlib import Path
        import asyncio
        Path({str(marker)!r}).write_text(str(queued))
        async with asyncio.timeout(10):
            while not Path({str(release)!r}).exists():
                await asyncio.sleep(.003)
        if args.get('ending') == 'fail':'''))
    def stream(model, context, options):
        calls.append(context)
        result = (AssistantMessage(content=(ToolCall(id='request', name=next(tool.name for tool in context.tools
            if tool.name.startswith('ext_queuedhost_request')),
            arguments={'delivery': delivery, 'ending': ending, 'image': ending in {'image', 'image-denied'}}),),
            stop_reason='toolUse') if len(calls) == 1 else
            AssistantMessage(content=(TextContent(text='CLAW-QUEUE-DONE'),)))
        return EventStream.empty(result)
    config = ClawConfig(model='offline/model', data_dir=str(tmp_path / 'data'),
        socket_path=str(tmp_path / 'socket'), pid_file=str(tmp_path / 'pid'),
        groups=(GroupDef(id='main', is_main=True, workspace=str(tmp_path)),),
        extensions=('queuedhost',), extension_dir=str(root), extension_allow_tools=('queuedhost',))
    server = GatewayServer(config)
    info = Model(id='offline/model', input=('text',) if ending == 'image-denied' else ('text', 'image'),
                 context_window=128000, max_tokens=4096)
    stream.resolve_model = lambda name: info if name == 'offline/model' else None
    with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=('offline/model', stream)), \
         patch('xdog.claw.core.runtime.group.GroupRuntime.image_model_info', return_value=info):
        await server.start()
        runtime = server._orchestrator._runtimes['main']
        owner = runtime.get_or_create_session(allow_reset=False)
        try:
            yield server, runtime, owner, config, calls, marker, release
        finally:
            release.write_text('cleanup')
            await asyncio.wait_for(server.stop(), 10)


def review(panel, approve=True):
    assert panel.dialog['operation'] == 'queue_input'
    if approve:
        panel.handle_input(KeyEvent('right'))
    panel.handle_input(KeyEvent('enter'))
    for _ in range(160):
        panel.render(75)
        if panel.form._visible:
            break
        panel.handle_input(KeyEvent('pagedown'))
    assert panel.form._visible
    panel.handle_input(KeyEvent('enter', event_type=KeyEventType.REPEAT))
    panel.handle_input(KeyEvent('enter'))


@pytest.mark.parametrize('delivery', ['steer', 'follow_up'])
@pytest.mark.parametrize('ending', ['apply', 'deny', 'fail', 'error', 'image', 'image-denied', 'withdraw', 'source'])
async def test_real_native_callback_queue_receipts_draft_images_and_no_replay(tmp_path, delivery, ending):
    async with queued_server(tmp_path, delivery, ending) as (_, runtime, owner, config, calls, marker, release):
        async with native_ui(config) as (ui, wait):
            assert ui._extension_queued_inputs_supported
            ui._editor.set_text('PRIVATE ORIGINAL QUEUE DRAFT')
            ui._editor._cursor = 7
            image = attachment(PNG, 'private.png')
            ui._composer.items[image.id] = image
            ui._start_message('original user model input', ())
            await wait(lambda: ui._extension_dialog_panel is not None)
            panel = ui._extension_dialog_panel
            assert len(calls) == 1 and panel.dialog['images'] == []
            assert 'PRIVATE' not in str(panel.dialog) and PNG.hex() not in str(panel.dialog)
            assert bool(panel.dialog['image_summaries']) is (ending in {'image', 'image-denied'})
            review(panel, ending != 'deny')
            if ending in {'withdraw', 'source'}:
                await wait(marker.exists)
                assert not owner.agent.pending_inputs() and marker.read_text() == 'True'
                if ending == 'withdraw':
                    previous = ui._extension_queued_inputs_generation
                    ui._queue_request({'type': 'ping', 'extension_queued_inputs': 0})
                    await wait(lambda: not ui._extension_queued_inputs_supported)
                    ui._queue_request({'type': 'ping', 'extension_queued_inputs': 1})
                    await wait(lambda: ui._extension_queued_inputs_supported
                               and ui._extension_queued_inputs_generation != previous)
                else:
                    runtime._source_revision += 1
                release.write_text('callback success')
            await wait(lambda: ui._active_run_id is None)
            queued = [msg for msg in owner.agent.messages
                      if msg.role == 'user' and 'REVIEWED-QUEUE' in str(msg.content)]
            if ending in {'apply', 'image'}:
                assert len(queued) == 1
                text = queued[0].content if isinstance(queued[0].content, str) else queued[0].content[0].text
                assert text == '[User-approved message requested by extension queuedhost]\n\nREVIEWED-QUEUE /quit'
                parts = queued[0].content if not isinstance(queued[0].content, str) else ()
                assert sum(isinstance(part, ImageContent) for part in parts) == int(ending == 'image')
                saved = runtime.transcript_store.load_transcript(owner.meta.session_id)
                assert 'REVIEWED-QUEUE' in str(saved)
            else:
                assert not queued
            expected = ('original user model input\n\nPRIVATE ORIGINAL QUEUE DRAFT' if ending == 'fail'
                        else 'PRIVATE ORIGINAL QUEUE DRAFT')
            assert ui._editor.get_text() == expected
            assert ui._editor._cursor == (len(expected) if ending == 'fail' else 7)
            assert 'REVIEWED-QUEUE' not in ui._editor.get_text()
            assert ui._composer.items == {image.id: image}
            assert not ui._routed_inputs and not ui._extension_queued_inputs and not owner.agent.pending_inputs()
            assert owner.extensions.queued_input_handler is None and not ui._exit_requested


@pytest.mark.parametrize('version', [None, True, '1', 0])
async def test_queue_default_off_and_idle_command_cannot_borrow_active_owner(tmp_path, version):
    from packages.claw.tests.test_extension_message_gateway import handshake, peer
    async with queued_server(tmp_path) as (_, _, owner, config, calls, _marker, _release):
        async with peer(config) as (send, receive, _writer):
            caps = await handshake(send, receive, extension_message=1,
                **({'extension_queued_inputs': version} if version is not None else {}))
            assert caps['extension_queued_inputs'] == 0 and caps['extension_message'] == 1
            await send({'type': 'extension_control', 'request_id': 'idle', 'group_id': 'main',
                        'session_id': owner.meta.session_id, 'action': 'command', 'name': 'ext-queuedhost-request',
                        'args': json.dumps({})})
            result = await receive('extension_result')
            assert result['success'] and result['output'] == 'QUEUED-HOST-False' and not calls


async def test_pending_boolean_reads_actual_headless_command_owner_without_ui_or_queue_grant(tmp_path):
    async with queued_server(tmp_path) as (server, _runtime, owner, _config, calls, _marker, _release):
        result = await server._orchestrator.manage_extensions('main', 'command', 'ext-queuedhost-pending', '',
                                                             owner.meta.session_id)
        assert result['output'] == 'PENDING-MESSAGES false' and not calls
        assert owner.extensions.pending_messages is None and owner.extensions.queued_input_handler is None
