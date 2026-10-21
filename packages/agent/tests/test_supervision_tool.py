"""SDK configuration reserves every child-control tool name."""

from __future__ import annotations

import pytest
from xdog.agent.supervision_config import ChildConfig

RESERVED_NAMES = ("agent", "agent_spawn", "agent_control", "update_plan")


@pytest.mark.parametrize("name", RESERVED_NAMES)
def test_child_config_keeps_merged_and_legacy_tool_names_reserved(name):
    with pytest.raises(ValueError):
        ChildConfig.from_dict({"tools": [name]})
