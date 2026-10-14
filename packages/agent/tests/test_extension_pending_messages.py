"""Live own-Agent boolean inspection is independent of queued-input and UI grants."""
from __future__ import annotations

import pytest
from xdog.agent.extensions.dialogs import validate_dialog


def test_pending_read_schema_cannot_request_queue_bodies_or_choose_an_owner() -> None:

    def _check_case(field, value):
        with pytest.raises(ValueError):
            validate_dialog(
                {
                    "id": "pending",
                    "kind": "session",
                    "operation": "pending_messages",
                    "title": "Read boolean",
                    field: value,
                }
            )

    for field, value in [
        ("target", "other"),
        ("child_id", "child"),
        ("session_id", "other"),
        ("text", "body"),
        ("images", []),
        ("offset", 0),
    ]:
        _check_case(field, value)
