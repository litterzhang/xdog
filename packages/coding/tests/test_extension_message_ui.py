"""Real Coding command/broker/full review dispatches a canonical provenance-labelled prompt."""
from __future__ import annotations

import asyncio
import base64
import json

import pytest
from xdog.ai.types import AssistantMessage, TextContent
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
from xdog.tui.attachments import attachment
from xdog.tui.keys import KeyEvent, KeyEventType

from packages.coding.tests.test_rpc_extensions import close, session


@pytest.mark.parametrize('ending', ['apply', 'deny', 'cancel', 'fail', 'after-browser'])
async def test_coding_extension_message_requires_review_and_successful_callback(tmp_path, ending, monkeypatch):
    owner, _ = session(tmp_path, extra=('messagerequest',))
    calls = []
    def stream(model, context, options):
        calls.append(context)
        return EventStream.empty(AssistantMessage(content=(TextContent(text='MESSAGE REPLY'),)))
    owner.agent.set_stream_fn(stream)
    outputs = []
    original_command = owner.extension_command
    async def capture(*args, **kwargs):
        result = await original_command(*args, **kwargs)
        outputs.append(result)
        return result
    monkeypatch.setattr(owner, 'extension_command', capture)
    ui = InteractiveMode(owner)
    async def wait(predicate):
        async with asyncio.timeout(10):
            while True:
                ui._poll()
                if predicate():
                    return
                await asyncio.sleep(.003)
    try:
        ui._editor.set_text('UNSENT MESSAGE DRAFT')
        image = attachment(base64.b64decode(
            'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5ioAAAAASUVORK5CYII='),
            'retained.png')
        ui._composer.items[image.id] = image
        instructions = '  /quit\n'
        ui._handle_slash_command('ext-messagerequest-request', json.dumps({
            'text': instructions, 'fail': ending == 'fail', 'after_browser': ending == 'after-browser'}))
        await wait(lambda: ui._extension_dialog_panel is not None)
        panel = ui._extension_dialog_panel
        assert panel.dialog['operation'] == 'message' and not calls
        if ending == 'cancel':
            panel.handle_input(KeyEvent('escape'))
        else:
            if ending != 'deny':
                panel.handle_input(KeyEvent('right'))
            panel.handle_input(KeyEvent('enter'))
            assert not calls
            for _ in range(160):
                panel.render(80)
                if panel.form._visible:
                    break
                panel.handle_input(KeyEvent('pagedown'))
            assert panel.form._visible
            panel.handle_input(KeyEvent('enter', event_type=KeyEventType.REPEAT))
            assert not calls
            panel.handle_input(KeyEvent('enter'))
        await wait(lambda: ui._command is None and not ui._worker_active)
        if ending in {'apply', 'after-browser'}:
            assert len(calls) == 1
            assert owner.messages[0].content[0].text == (
                '[User-approved message requested by extension messagerequest]\n\n' + instructions)
            assert not ui._exit_requested
        else:
            assert not calls and not owner.messages
        if ending == 'after-browser':
            assert '"browser": false' in outputs[-1]
        assert ui._editor.get_text() == 'UNSENT MESSAGE DRAFT' and owner.permissions.mode == 'deny'
        assert ui._composer.items[image.id] is image
        assert all(len(row.content) == 1 for row in owner.messages if row.role == 'user')
    finally:
        ui._request_exit()
        await close(owner)
