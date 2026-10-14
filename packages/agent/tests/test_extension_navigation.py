from __future__ import annotations

from pathlib import Path

import pytest
from xdog.agent.extensions.dialogs import validate_answer, validate_dialog

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


def test_invalid_navigation_actions_targets_are_not_guessed_coerced_or_interpreted() -> None:

    def _check_case(target):
        with pytest.raises(ValueError):
            request(**target)

    for target in [
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
    ]:
        _check_case(target)


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
