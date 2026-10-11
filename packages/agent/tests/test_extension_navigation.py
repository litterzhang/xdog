from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from xdog.agent.extensions.dialogs import validate_answer, validate_dialog
from xdog.agent.extensions.runtime import ExtensionRuntime

FIXTURE = Path(__file__).parent / "fixtures/extensions"


def request(**overrides):
    target = {"action": "new", "session_id": "", "branch_id": "", "index": -1}
    return validate_dialog(
        {
            "kind": "session",
            "id": "nav",
            "title": "Navigate",
            "operation": "navigate",
            "target": {**target, **overrides},
        }
    )


@pytest.mark.parametrize(
    "target",
    [
        {"action": "bad"},
        {"action": True},
        {"action": "new", "session_id": "target"},
        {"action": "resume"},
        {"action": "resume", "session_id": "target", "index": 0},
        {"action": "branch"},
        {"action": "goto"},
        {"action": "goto", "index": True},
        {"action": "fork", "session_id": "target"},
        {"action": "resume", "session_id": "../target"},
        {"action": "new", "extra": "ignored"},
    ],
)
def test_invalid_navigation_actions_targets_are_not_guessed_coerced_or_interpreted(target):
    with pytest.raises(ValueError):
        request(**target)


def test_valid_targets_are_snapshotted_and_replies_mean_queued_only():
    for target in (
        {},
        {"action": "resume", "session_id": "target"},
        {"action": "branch", "branch_id": "branch"},
        {"action": "goto", "index": 0},
        {"action": "fork", "index": 1},
    ):
        dialog = request(**target)
        assert validate_answer(dialog, True) is True and validate_answer(dialog, None) is None
        with pytest.raises(ValueError):
            validate_answer(dialog, {"activated": True})


async def test_worker_navigation_requires_actual_command_review_and_headless_refuses(tmp_path):
    root = tmp_path / "extensions"
    shutil.copytree(FIXTURE, root)
    runtime = ExtensionRuntime(["navigator"], root, tmp_path)
    requests = []

    async def answer(extension, dialog):
        if dialog["operation"] == "snapshot":
            return {"session_id": "current", "workspace": str(tmp_path), "group_id": ""}
        requests.append(dialog)
        return True

    runtime.dialog_handler = answer
    try:
        assert await runtime.command("ext-navigator-navigate", "new") == "NAVIGATION-REVIEWED-True"
        assert requests[0]["operation"] == "navigate" and requests[0]["target"]["action"] == "new"
        runtime.dialog_handler = None
        assert await runtime.command("ext-navigator-navigate", "new") == "NAVIGATION-UNAVAILABLE"
    finally:
        await runtime.aclose()
