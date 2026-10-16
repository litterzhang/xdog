from __future__ import annotations

from pathlib import Path

import pytest
from xdog.agent.extensions.dialogs import validate_answer, validate_sdk_dialog

FIXTURE = Path(__file__).parent / "fixtures/extensions"


def test_session_request_reply_is_bounded_metadata_not_transcript_or_mutation():
    request = validate_sdk_dialog({"id": "id", "kind": "session", "title": "Session", "operation": "snapshot"})
    reply = {"session_id": "current", "workspace": "/workspace", "group_id": "main"}
    assert validate_answer(request, reply) == reply
    for bad in ({**reply, "history": []}, {**reply, "session_id": True}, {**reply, "workspace": "x" * 8193}):
        with pytest.raises(ValueError):
            validate_answer(request, bad)
    with pytest.raises(ValueError, match='removed'):
        validate_sdk_dialog(
            {'id': 'id', 'kind': 'session', 'title': 'Browser', 'operation': 'browse', 'scope': 'tree'})
