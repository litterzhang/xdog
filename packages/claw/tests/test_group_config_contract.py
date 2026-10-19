"""Claw admission settings must not advertise retired queue policies."""
from dataclasses import asdict

import pytest
from xdog.agent import AgentConfig, QueueMode
from xdog.claw.core.types import GroupConfig


@pytest.mark.parametrize("name,value", [
    ("queue_mode", "steer"), ("debounce_ms", 0), ("max_concurrent", 2), ("trigger_pattern", "@claw"),
])
def test_retired_group_policy_is_rejected_instead_of_silently_ignored(name, value):
    with pytest.raises(TypeError, match=name):
        GroupConfig(**{name: value})


def test_group_config_keeps_only_actual_admission_and_reset_settings():
    assert asdict(GroupConfig()) == {
        "daily_reset_hour": 4, "idle_reset_seconds": 0, "max_queued_messages": 50,
    }
    assert GroupConfig(max_queued_messages=2).max_queued_messages == 2


@pytest.mark.parametrize("mode", [QueueMode.ALL, QueueMode.ONE_AT_A_TIME])
def test_agent_native_explicit_input_modes_are_not_claw_queue_configuration(mode):
    config = AgentConfig(model="test/dummy", steering_mode=mode, follow_up_mode=mode)
    assert config.steering_mode is mode and config.follow_up_mode is mode
