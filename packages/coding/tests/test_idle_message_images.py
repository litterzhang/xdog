"""Actual idle command, native/RPC whole review and original result-delivery fence."""
from __future__ import annotations

import asyncio
import base64
import json

import pytest
from xdog.ai.types import AssistantMessage, ImageContent, Model, TextContent
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
from xdog.coding.modes.rpc.extensions import RpcExtensions
from xdog.coding.modes.rpc.navigation import RpcNavigation
from xdog.tui.attachments import attachment
from xdog.tui.keys import KeyEvent

from packages.agent.tests.test_idle_message_images import PNG
from packages.coding.tests.test_rpc_compaction import wait
from packages.coding.tests.test_rpc_extensions import close, session


def setup(tmp_path):
    owner, _ = session(tmp_path, extra=('messagerequest',))
    calls = []
    def stream(model, context, options):
        calls.append(context)
        return EventStream.empty(AssistantMessage(content=(TextContent(text='IMAGE REPLY'),)))
    owner.agent.set_stream_fn(stream)
    owner.model_info = Model(id=owner.model, input=('text', 'image'))
    return owner, calls


@pytest.mark.parametrize('ending', ['apply', 'fail', 'old-client', 'withdraw', 'delivery-fail', 'model-change'])
async def test_rpc_idle_images_private_review_and_no_execution_before_complete_success_delivery(tmp_path, ending):
    owner, calls = setup(tmp_path)
    events, delivered, release = [], asyncio.Event(), asyncio.Event()
    async def send(value):
        delivered.set()
        await release.wait()
        if ending == 'delivery-fail':
            raise OSError('owned failure')
        events.append(value)
    nav = RpcNavigation(owner, events.append)
    rpc = RpcExtensions(owner, events.append, navigation=nav, send=send)
    rpc.negotiate({'extension_messages': 1, 'extension_message_images': int(ending != 'old-client')})
    try:
        rpc.start({'request_id': 'image', 'session_id': owner.session_id, 'action': 'command',
            'name': 'ext-messagerequest-request', 'args': json.dumps({
                'text': '', 'images': True, 'fail': ending == 'fail'})}, turn_running=False)
        if ending != 'old-client':
            await wait(lambda: any(row['type'] == 'extension_dialog' for row in events))
            event = next(row for row in events if row['type'] == 'extension_dialog')
            assert event['dialog']['image_summaries'] and PNG not in json.dumps(events)
            assert event['dialog']['images'] == [] and not calls
            rpc.answer({'id': event['dialog']['id'], 'request_id': event['request_id'],
                        'session_id': event['session_id'], 'scope': event['scope'], 'value': True})
        await wait(delivered.is_set)
        assert not calls and not owner.messages and PNG not in json.dumps(events)
        if ending == 'withdraw':
            rpc.negotiate({'extension_message_images': 0})
            rpc.negotiate({'extension_message_images': 1})
        if ending == 'model-change':
            owner.model_info = Model(id=owner.model, input=('text',))
        release.set()
        await asyncio.gather(rpc.task, return_exceptions=True)
        await asyncio.sleep(0)
        if rpc.prompt_task is not None:
            await rpc.prompt_task
        assert len(calls) == int(ending == 'apply')
        assert PNG not in json.dumps(events)  # Ordinary RPC emits no private source-image bodies either.
        if calls:
            actual = owner.messages[0].content
            assert isinstance(actual[1], ImageContent) and actual[1].data == PNG
            assert 'extension messagerequest' in actual[0].text
        assert owner.permissions.mode == 'deny'
    finally:
        release.set()
        await rpc.aclose()
        await nav.aclose()
        await close(owner)


@pytest.mark.parametrize('ending', ['apply', 'deny', 'fail', 'admission-fail'])
async def test_native_image_only_private_grant_survives_only_review_and_callback_success(tmp_path, ending, monkeypatch):
    owner, calls = setup(tmp_path)
    ui = InteractiveMode(owner)
    if ending == 'admission-fail':
        async def reject(message, images=(), *, input_origin='user'):
            assert input_origin == 'extension'
            raise ValueError('Owned pre-turn refusal')
        monkeypatch.setattr(ui, '_async_agent_turn', reject)
    async def until(predicate):
        async with asyncio.timeout(8):
            while True:
                ui._poll()
                if predicate():
                    return
                await asyncio.sleep(.003)
    try:
        ui._editor.set_text('PRIVATE COMPOSER DRAFT')
        retained = attachment(base64.b64decode(PNG), 'composer.png')
        ui._composer.items[retained.id] = retained
        ui._handle_slash_command('ext-messagerequest-request', json.dumps({
            'text': '', 'images': True, 'fail': ending == 'fail'}))
        await until(lambda: ui._extension_dialog_panel is not None)
        panel = ui._extension_dialog_panel
        assert panel.dialog['images'] == [] and PNG not in json.dumps(panel.dialog)
        assert panel.dialog['image_summaries'] and not calls
        if ending == 'deny':
            panel.handle_input(KeyEvent('escape'))
        else:
            panel.handle_input(KeyEvent('right'))
            panel.handle_input(KeyEvent('enter'))
            for _ in range(160):
                panel.render(90)
                if panel.form._visible:
                    break
                panel.handle_input(KeyEvent('pagedown'))
            assert panel.form._visible and not calls
            panel.handle_input(KeyEvent('enter'))
        await until(lambda: ui._command is None and not ui._worker_active)
        assert len(calls) == int(ending == 'apply')
        if calls:
            assert owner.messages[0].content[1].data == PNG
        assert ui._editor.get_text() == 'PRIVATE COMPOSER DRAFT' and ui._composer.items == {retained.id: retained}
        assert owner.permissions.mode == 'deny' and not ui._message_inputs._pending
    finally:
        ui._request_exit()
        await close(owner)
