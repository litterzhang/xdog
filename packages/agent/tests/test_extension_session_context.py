from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from xdog.agent.extensions.dialogs import validate_answer, validate_dialog
from xdog.agent.extensions.runtime import ExtensionRuntime

FIXTURE = Path(__file__).parent / "fixtures/extensions"


def test_session_request_reply_is_bounded_metadata_not_transcript_or_mutation():
    request = validate_dialog({"id": "id", "kind": "session", "title": "Session", "operation": "snapshot"})
    reply = {"session_id": "current", "workspace": "/workspace", "group_id": "main"}
    assert validate_answer(request, reply) == reply
    for bad in ({**reply, "history": []}, {**reply, "session_id": True}, {**reply, "workspace": "x" * 8193}):
        with pytest.raises(ValueError):
            validate_answer(request, bad)
    browser = validate_dialog(
        {"id": "id", "kind": "session", "title": "Browser", "operation": "browse", "scope": "tree"}
    )
    assert validate_answer(browser, True) is True and validate_answer(browser, None) is None
    with pytest.raises(ValueError):
        validate_answer(browser, "true")
    with pytest.raises(ValueError):
        validate_dialog(
            {"id": "id", "kind": "session", "title": "Unsupported", "operation": "resume", "session_id": "x"}
        )


async def test_actual_worker_session_metadata_and_deferred_browser_tools_headless_refusal(tmp_path):
    root = tmp_path / "extensions"
    shutil.copytree(FIXTURE, root)
    runtime = ExtensionRuntime(["navigator"], root, tmp_path)
    requests = []

    async def answer(extension, request):
        requests.append(request)
        return (
            {"session_id": "current", "workspace": str(tmp_path), "group_id": "main"}
            if request["operation"] == "snapshot"
            else True
        )

    runtime.dialog_handler = answer
    try:
        assert await runtime.command("ext-navigator-browse", "tree") == "NAVIGATION-QUEUED-True"
        assert [item["operation"] for item in requests] == ["snapshot", "browse"]
        assert requests[-1]["scope"] == "tree"
        requests.clear()
        result = await runtime.tools()[0].execute("illegal", {})
        assert result.content[0].text == "TOOL-NAVIGATION-None-False" and not requests
        runtime.dialog_handler = None
        assert await runtime.command("ext-navigator-browse", "") == "NAVIGATION-UNAVAILABLE"
    finally:
        await runtime.aclose()
