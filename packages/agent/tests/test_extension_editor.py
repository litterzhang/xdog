from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from xdog.agent.extensions.dialogs import validate_answer, validate_dialog
from xdog.agent.extensions.runtime import ExtensionRuntime

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


async def test_real_worker_command_editor_capability_tools_headless_and_callbacks_are_independent(tmp_path):
    root = tmp_path / "extensions"
    shutil.copytree(FIXTURE, root)
    runtime = ExtensionRuntime(["editor"], root, tmp_path)
    requests = []

    async def answer(extension, value):
        requests.append(value)
        if value["operation"] == "read":
            return {"text": "draft", "cursor": 5, "image_count": 1, "revision": "a" * 64}
        assert value["text"] == "draft EXTENSION-COMPOSED" and value["revision"] == "a" * 64
        return {"applied": True, "reason": "Captured draft replaced"}

    runtime.dialog_handler = answer
    try:
        assert await runtime.command("ext-editor-compose", "") == "EDITOR-APPLIED-True"
        assert [item["kind"] for item in requests] == ["editor", "editor"]
        requests.clear()
        result = await runtime.tools()[0].execute("illegal", {})
        assert result.content[0].text == "TOOL-EDITOR-None-False" and not requests
        runtime.dialog_handler = None
        assert await runtime.command("ext-editor-compose", "") == "EDITOR-UNAVAILABLE"
    finally:
        await runtime.aclose()
