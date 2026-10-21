"""Child queue leases use the real owning Agent queue/checkpoint, not mirrored UI records."""

from __future__ import annotations

import pytest
from xdog.agent.supervision_queue import request


def test_malformed_child_queue_commands_fail_before_mutation() -> None:

    def _check_case(value):
        with pytest.raises(ValueError):
            request(value)

    for value in [
        {"action": []},
        {"action": "edit"},
        {"action": "list", "content": "extra"},
        {"action": "move", "id": "input", "revision": "a" * 64, "direction": True},
        {"action": "edit", "id": "input", "revision": "a" * 64, "content": "x" * 16001},
    ]:
        _check_case(value)
