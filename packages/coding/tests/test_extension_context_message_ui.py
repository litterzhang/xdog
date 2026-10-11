"""Canonical custom records are non-triggering, reviewed and isolated from draft/user metadata."""
from __future__ import annotations

import asyncio
import json

import pytest
from xdog.agent.core import CustomAgentMessage
from xdog.ai.types import AssistantMessage, TextContent
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
from xdog.tui.keys import KeyEvent

from packages.coding.tests.test_rpc_extensions import close, session


@pytest.mark.parametrize('ending', ['apply', 'deny', 'fail', 'hidden', 'extension-first'])
async def test_actual_native_review_persists_custom_context_for_next_user_only(tmp_path, ending):
    owner, _ = session(tmp_path, extra=('contextrequest', 'messagerequest'))
    calls = []
    def stream(model, context, options):
        calls.append(context)
        return EventStream.empty(AssistantMessage(content=(TextContent(text='CONTEXT-ANSWER'),)))
    owner.agent.set_stream_fn(stream)
    ui = InteractiveMode(owner)
    async def wait(predicate):
        async with asyncio.timeout(12):
            while True:
                ui._poll()
                if predicate():
                    return
                await asyncio.sleep(.003)
    def answer(approve):
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
    try:
        ui._editor.set_text('PRIVATE CONTEXT DRAFT')
        ui._handle_slash_command('ext-contextrequest-request', json.dumps(
            {'fail': ending == 'fail', 'display': ending != 'hidden'}))
        await wait(lambda: ui._extension_dialog_panel is not None)
        assert ui._extension_dialog_panel.dialog['operation'] == 'context_message'
        assert not calls and not owner.messages
        answer(ending != 'deny')
        await wait(lambda: ui._command is None)
        assert not calls and not owner.messages and ui._editor.get_text() == 'PRIVATE CONTEXT DRAFT'
        if ending == 'fail':
            ui._handle_slash_command('extensions', 'unload contextrequest')
            await wait(lambda: ui._command is None)  # Explicit recovery; never bypass failed validation.
        if ending == 'extension-first':
            ui._handle_slash_command('ext-messagerequest-request', '{"text":"Extension-origin immediate turn"}')
            await wait(lambda: ui._extension_dialog_panel is not None)
            answer(True)
            await wait(lambda: ui._command is None and not ui._worker_active)
            assert len(calls) == 1 and 'RETAINED-CUSTOM-CONTENT' not in str(calls[0].messages)
            assert not any(isinstance(row, CustomAgentMessage) for row in owner.messages)
        ui._start_turn('Next explicit user prompt')
        await wait(lambda: not ui._worker_active)
        custom = [row for row in owner.messages if isinstance(row, CustomAgentMessage)]
        if ending in {'apply', 'hidden', 'extension-first'}:
            assert len(custom) == 1 and custom[0].data['details'] == {'opaque': 'PRIVATE-NOT-MODEL'}
            assert 'RETAINED-CUSTOM-CONTENT' in str(calls[-1].messages)
            assert 'PRIVATE-NOT-MODEL' not in str(calls[-1].messages)
            saved = owner.session_manager.load_session(owner.session_id)
            assert sum(isinstance(row, CustomAgentMessage) for row in saved.messages) == 1
        else:
            assert not custom and 'RETAINED-CUSTOM-CONTENT' not in str(calls[-1].messages)
        assert ui._editor.get_text() == 'PRIVATE CONTEXT DRAFT' and owner.permissions.mode == 'deny'
    finally:
        ui._request_exit()
        await close(owner)
