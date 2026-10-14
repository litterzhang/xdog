from __future__ import annotations

from pathlib import Path

import pytest
from xdog.agent.extensions.dialogs import validate_answer, validate_dialog

FIXTURE = Path(__file__).parent / "fixtures/extensions"


def request(operation="read", **kwargs):
    return validate_dialog(
        {"kind": "editor", "id": "request", "title": "Command editor", "operation": operation, **kwargs}
    )


def test_editor_snapshot_codec_has_typed_fields_and_no_boolean_cursor_or_coercion():
    value = {"text": "draft", "cursor": 3, "image_count": 1, "revision": "a" * 64}
    assert validate_answer(request(), value) == value
    for bad in (
        {**value, "cursor": True},
        {**value, "image_count": False},
        {**value, "cursor": 99},
        {**value, "revision": "stale"},
        {**value, "extra": "not part of snapshot"},
    ):
        with pytest.raises(ValueError):
            validate_answer(request(), bad)
    write = request("replace", text="new draft", revision="a" * 64)
    assert validate_answer(write, {"applied": False, "reason": "Conflict"})["applied"] is False
    assert validate_answer(write, None) is None
    with pytest.raises(ValueError):
        request("replace", text="new draft", revision="guessed")
