"""Compaction requests are explicit host operations, not arbitrary callback authority."""
from __future__ import annotations

import pytest
from xdog.agent.extensions.dialogs import validate_sdk_dialog


def dialog(**changes):
    return {'id': 'request', 'kind': 'session', 'operation': 'compact', 'title': 'Review compaction',
            'instructions': 'Keep context', **changes}


def test_compaction_request_canonical_instructions_cannot_borrow_navigation_fields():
    with pytest.raises(ValueError, match='removed'):
        validate_sdk_dialog(dialog())
    for fields in ({'instructions': None}, {'instructions': ' x '}, {'instructions': '\x00'},
                   {'instructions': 'x' * 16385}, {'target': {}}, {'session_id': 'other'},
                   {'branch_id': 'other'}, {'index': 0}, {'scope': 'parent'}, {'revision': 0},
                   {'kind': 'confirm'}):
        with pytest.raises(ValueError):
            validate_sdk_dialog(dialog(**fields))
