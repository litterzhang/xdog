from __future__ import annotations

import pytest
from xdog.agent.child_config import ChildPresets, parse_child_start

PRESETS = {
    "reviewer": {
        "description": "Inspect without tools",
        "config": {"instructions": "REVIEW-INSTRUCTIONS", "tools": [], "inherit_context": False},
    }
}


def test_preset_resolution_is_immutable_bounded_and_explicit_flags_override_saved_defaults():
    values = {"reviewer": {"description": "Inspect without tools", "config": {**PRESETS["reviewer"]["config"]}}}
    presets = ChildPresets(values)
    values["reviewer"]["config"]["instructions"] = "MUTATED"
    task, access, config = parse_child_start("--preset reviewer --label custom task", presets)
    assert task == "task" and access == "read-only" and config.preset == "reviewer" and config.label == "custom"
    assert config.instructions == "REVIEW-INSTRUCTIONS" and config.tools == () and not config.inherit_context
    snapshot = presets.snapshot()
    snapshot[0]["config"]["instructions"] = "MUTATED-SNAPSHOT"
    assert presets.resolve({"preset": "reviewer"}).instructions == "REVIEW-INSTRUCTIONS"
    assert ChildPresets.from_snapshot(presets.snapshot()).resolve({"preset": "reviewer"}) == presets.resolve(
        {"preset": "reviewer"}
    )


@pytest.mark.parametrize(
    "bad",
    [
        {"../outside": {}},
        {"reviewer": {"command": "evil"}},
        {"reviewer": {"config": {"access": "inherit"}}},
        {"reviewer": {"config": {"permission_mode": "allow-all"}}},
        {"reviewer": {"config": {"preset": "other"}}},
        {"reviewer": {"config": {"max_tokens": 32001}}},
        {"reviewer": {"description": "x" * 1001}},
        {str(index): {} for index in range(33)},
        {"reviewer": {"config": {"tools": ["agent_spawn"]}}},
    ],
)
def test_preset_catalog_cannot_encode_execution_permissions_recursion_or_unbounded_resources(bad):
    with pytest.raises(ValueError):
        ChildPresets(bad)
