"""Reviewed message text is provenance-labelled, bounded and not command authority."""
from __future__ import annotations

import pytest
from xdog.agent.extensions.dialogs import validate_sdk_dialog


def dialog(**changes):
    return {'id': 'request', 'kind': 'session', 'operation': 'message', 'title': 'Review message',
            'text': '  /quit\n', **changes}


def test_message_request_preserves_literal_text_without_borrowing_scope_or_images():
    with pytest.raises(ValueError, match='removed'):
        validate_sdk_dialog(dialog())
    for fields in ({'text': None}, {'text': '  '}, {'text': '\x00'}, {'text': 'x' * 16385},
                   {'target': {}}, {'session_id': 'other'}, {'branch_id': 'other'}, {'index': 0},
                   {'scope': 'parent'}, {'revision': 0}, {'images': None}, {'image_refs': []},
                   {'origin': 'user'}, {'delivery': 'steer'}, {'kind': 'confirm'}):
        with pytest.raises(ValueError):
            validate_sdk_dialog(dialog(**fields))
