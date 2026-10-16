"""Extension-origin failure is never a grant to overwrite/capture the user's composer."""
from __future__ import annotations

import base64

import pytest
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
from xdog.tui.attachments import attachment

from packages.coding.tests.extension_session_helpers import PNG, close, session


@pytest.mark.parametrize('origin', ['user', 'extension'])
@pytest.mark.parametrize('kind', ['input_handled', 'input_rejected', 'error'])
async def test_origin_controls_error_handled_and_rejected_attachment_restoration(tmp_path, origin, kind):
    owner, calls = session(tmp_path)
    ui = InteractiveMode(owner)
    original = attachment(base64.b64decode(PNG), 'private-original.png')
    proposed = attachment(base64.b64decode(PNG), 'proposal.png')
    ui._composer.items[original.id] = original
    ui._editor.set_text('PRIVATE DRAFT')
    try:
        if kind == 'error':
            ui._active_images = (proposed,)
            ui._active_input_origin = origin
        ui._handle_ui_event({'type': kind, 'id': '', 'source': 'prompt', 'input_origin': origin,
            'original': 'Rejected input', 'images': (proposed,), 'feedback': 'Handled', 'message': 'Failed',
            'generation': ui._worker_generation})
        assert (proposed.id in ui._composer.items) is (origin == 'user')
        assert ui._composer.items[original.id] is original and not calls
        if origin == 'extension':
            assert ui._editor.get_text() == 'PRIVATE DRAFT'
    finally:
        ui._request_exit()
        await close(owner)


async def test_unconsumed_extension_queue_reports_failure_without_automatic_draft_merge(tmp_path):
    owner, calls = session(tmp_path)
    ui = InteractiveMode(owner)
    image = attachment(base64.b64decode(PNG), 'extension.png')
    ui._editor.set_text('PRIVATE DRAFT')
    try:
        ui._handle_ui_event({'type': 'extension_input_queued', 'id': 'extq-owned', 'content': 'EXTENSION TEXT',
            'delivery': 'follow_up', 'images': (image,), 'extension_session': owner.session_id})
        ui._handle_input_event({'type': 'inputs_settled',
                               'inputs': [{'id': 'extq-owned', 'content': 'EXTENSION TEXT'}]})
        assert ui._editor.get_text() == 'PRIVATE DRAFT' and not ui._composer.items
        assert not ui._routed_inputs and not ui._routed_images and not hasattr(ui, '_extension_input_ids')
        assert 'EXTENSION TEXT' not in str(ui._chat_log.render(160)) and not calls
    finally:
        ui._request_exit()
        await close(owner)
