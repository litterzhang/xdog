"""Real Coding command/broker/full review dispatches canonical compaction, not a prompt."""
from __future__ import annotations

import asyncio
import json

import pytest
from xdog.ai.types import AssistantMessage, TextContent
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
from xdog.tui.keys import KeyEvent, KeyEventType

from packages.coding.tests.test_rpc_compaction import history
from packages.coding.tests.test_rpc_extensions import close, session


@pytest.mark.parametrize('ending', ['apply', 'deny', 'cancel', 'fail', 'literal-cancel', 'after-browser'])
async def test_coding_extension_compaction_requires_review_and_successful_callback(tmp_path, ending, monkeypatch):
    owner, _ = session(tmp_path, extra=('compactionrequest',))
    original = history(owner)
    calls = []
    def stream(model, context, options):
        calls.append(context)
        return EventStream.empty(AssistantMessage(content=(TextContent(text='reviewed summary'),)))
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
        ui._editor.set_text('UNSENT COMPACTION DRAFT')
        instructions = 'cancel' if ending == 'literal-cancel' else 'Keep COMPACTION-FOCUS'
        ui._handle_slash_command('ext-compactionrequest-request', json.dumps({
            'instructions': instructions, 'fail': ending == 'fail', 'after_browser': ending == 'after-browser'}))
        await wait(lambda: ui._extension_dialog_panel is not None)
        panel = ui._extension_dialog_panel
        assert panel.dialog['operation'] == 'compact' and not calls
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
        await wait(lambda: ui._command is None)
        if ending in {'apply', 'literal-cancel', 'after-browser'}:
            assert len(calls) == 1 and not calls[0].tools and instructions in str(calls[0].messages)
            assert owner.messages != original and owner.session_data.compaction['recovery_branch_id']
            assert 'Extension-requested compaction completed' in str(ui._chat_log.render(160))
        else:
            assert not calls and owner.messages == original and owner.session_data.compaction is None
        if ending == 'after-browser':
            assert '"browser": false' in outputs[-1]
        assert ui._editor.get_text() == 'UNSENT COMPACTION DRAFT' and owner.permissions.mode == 'deny'
    finally:
        ui._request_exit()
        await close(owner)
