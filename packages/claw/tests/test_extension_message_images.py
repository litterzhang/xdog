"""Idle typed images remain private to the reviewed message grant and canonical model admission."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest
from xdog.ai.types import AssistantMessage, ImageContent, Model, TextContent
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer
from xdog.tui.attachments import attachment

from packages.claw.tests.test_extension_message_ui import PNG, native_ui, review


@asynccontextmanager
async def image_server(tmp_path):
    root = tmp_path / 'extensions/messagerequest'
    root.mkdir(parents=True)
    (root / 'extension.yaml').write_text('name: messagerequest\nentry_point: main\n')
    (root / 'main.py').write_text(f'''import asyncio
import json
from pathlib import Path
from xdog.ai.types import ImageContent

def setup(api):
    async def command(args, context):
        values = json.loads(args)
        queued = await api.request_message(values.get('text', '/quit'),
            images=(ImageContent(data={base64.b64encode(PNG).decode()!r}, mime_type='image/png'),))
        if values.get('hold'):
            Path({str(tmp_path / 'reviewed')!r}).write_text(str(queued))
            async with asyncio.timeout(10):
                while not Path({str(tmp_path / 'release')!r}).exists():
                    await asyncio.sleep(.003)
        if values.get('fail'):
            raise RuntimeError('Failed image message callback')
        return 'IMAGE-MESSAGE-' + str(queued)
    api.command('images', 'Request exact typed images', command)
''')
    calls = []
    def stream(model, context, options):
        calls.append(context)
        return EventStream.empty(AssistantMessage(content=(TextContent(text='IMAGE-MESSAGE-DONE'),)))
    config = ClawConfig(model='offline/vision', data_dir=str(tmp_path / 'data'),
        socket_path=str(tmp_path / 'socket'), pid_file=str(tmp_path / 'pid'),
        groups=(GroupDef(id='main', is_main=True, workspace=str(tmp_path)),),
        extensions=('messagerequest',), extension_dir=str(root.parent))
    model = [Model(id='offline/vision', input=('text', 'image'))]
    server = GatewayServer(config)
    with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=('offline/vision', stream)), \
         patch('xdog.claw.core.runtime.group.GroupRuntime.image_model_info', side_effect=lambda: model[0]):
        await server.start()
        runtime = server._orchestrator._runtimes['main']
        owner = runtime.get_or_create_session(allow_reset=False)
        try:
            yield server, owner, config, model, calls
        finally:
            (tmp_path / 'release').write_text('cleanup')
            await asyncio.wait_for(server.stop(), 10)


@pytest.mark.parametrize('ending', ['apply', 'image-only', 'deny', 'fail', 'old-client', 'withdraw',
                                     'model-before', 'model-after', 'stale-dispatch', 'result-fail'])
async def test_private_image_grant_review_generation_callback_and_canonical_delivery(tmp_path, monkeypatch, ending):
    async with image_server(tmp_path) as (_, owner, config, model, calls):
        async with native_ui(config) as (ui, wait):
            assert ui._extension_message_images_supported
            actual_inputs = []
            original_turn = owner.run_turn
            async def run(input, *args, **kwargs):
                actual_inputs.append(input)
                return await original_turn(input, *args, **kwargs)
            monkeypatch.setattr(owner, 'run_turn', run)
            if ending == 'old-client':
                ui._queue_request({'type': 'ping', 'extension_message_images': 0})
                await wait(lambda: not ui._extension_message_images_supported)
                assert ui._extension_message_supported
            if ending == 'model-before':
                model[0] = Model(id='offline/vision', input=('text',))
            if ending == 'stale-dispatch':
                original_start = ui._start_message
                def stale(value, images, *, requested_message=None):
                    ui._queue_request({'type': 'ping', 'extension_message_images': 0})
                    ui._queue_request({'type': 'ping', 'extension_message_images': 1})
                    original_start(value, images, requested_message=requested_message)
                monkeypatch.setattr(ui, '_start_message', stale)
            if ending == 'result-fail':
                writers = set()
                original_write, original_drain = asyncio.StreamWriter.write, asyncio.StreamWriter.drain
                def write(writer, data):
                    if b'"message_delivery"' in data:
                        writers.add(writer)
                    return original_write(writer, data)
                async def drain(writer):
                    if writer in writers:
                        writers.discard(writer)
                        raise BrokenPipeError('Image message complete result failed')
                    await original_drain(writer)
                monkeypatch.setattr(asyncio.StreamWriter, 'write', write)
                monkeypatch.setattr(asyncio.StreamWriter, 'drain', drain)
            ui._editor.set_text('PRIVATE UNSENT IMAGE DRAFT')
            ui._editor._cursor = 5
            private = attachment(PNG, 'user-private.png')
            ui._composer.items[private.id] = private
            ui._extension_command('ext-messagerequest-images', json.dumps({
                'text': '' if ending == 'image-only' else '/quit',
                'fail': ending == 'fail', 'hold': ending in {'withdraw', 'model-after'}}))
            if ending not in {'old-client', 'model-before'}:
                await wait(lambda: ui._extension_dialog_panel is not None)
                panel = ui._extension_dialog_panel
                assert panel.dialog['images'] == [] and base64.b64encode(PNG).decode() not in str(panel.dialog)
                assert panel.dialog['image_summaries'] == [{'mime_type': 'image/png', 'bytes': len(PNG),
                                                          'sha256': hashlib.sha256(PNG).hexdigest()}]
                assert not calls
                review(panel, ending != 'deny')
            if ending in {'withdraw', 'model-after'}:
                await wait(lambda: (tmp_path / 'reviewed').exists())
                assert not calls
                if ending == 'withdraw':
                    old = ui._extension_message_images_generation
                    ui._queue_request({'type': 'ping', 'extension_message_images': 0})
                    await wait(lambda: not ui._extension_message_images_supported)
                    ui._queue_request({'type': 'ping', 'extension_message_images': 1})
                    await wait(lambda: ui._extension_message_images_supported
                               and ui._extension_message_images_generation != old)
                else:
                    model[0] = Model(id='offline/vision', input=('text',))
                (tmp_path / 'release').write_text('callback success')
            await wait(lambda: ui._extension_pending is None and ui._active_run_id is None)
            assert len(calls) == int(ending in {'apply', 'image-only'})
            if calls:
                assert len(actual_inputs) == 1 and actual_inputs[0].metadata == {'input_origin': 'extension'}
                assert actual_inputs[0].images == (
                    ImageContent(data=base64.b64encode(PNG).decode(), mime_type='image/png'),)
                assert 'requested by extension messagerequest' in str(calls[0].messages)
                assert 'PRIVATE UNSENT' not in str(calls[0].messages)
                saved = owner._store.load_transcript(owner.meta.session_id)
                assert 'image/png' in str(saved)
            assert ui._editor.get_text() == 'PRIVATE UNSENT IMAGE DRAFT' and ui._editor._cursor == 5
            assert ui._composer.items == {private.id: private} and not ui._active_images
            assert not ui._exit_requested
