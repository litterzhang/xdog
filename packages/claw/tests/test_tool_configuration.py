"""Tool selection must reach every group, including newly registered groups."""
from __future__ import annotations

import pytest
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import _build_model_and_options, _groups_from_config
from xdog.claw.core.runtime.group import GroupRuntime
from xdog.claw.core.runtime.orchestrator import Orchestrator
from xdog.claw.core.tools import create_tools, default_enabled_tools, registered_names
from xdog.claw.core.types import Group


def test_registry_distinguishes_defaults_from_no_tools():
    assert "generate_image" in registered_names()
    assert "generate_image" not in default_enabled_tools()
    assert {tool.name for tool in create_tools()} == set(default_enabled_tools())
    assert "generate_image" not in {tool.name for tool in create_tools(image_model="antigravity/image")}
    assert create_tools(enabled=()) == []
    assert [tool.name for tool in create_tools(enabled=("filesystem",))] == ["filesystem"]


def test_registry_requires_image_model_and_rejects_unknown_tools():
    with pytest.raises(ValueError, match="image_model configuration"):
        create_tools(enabled=("generate_image",))
    with pytest.raises(ValueError, match="Unknown tools"):
        create_tools(enabled=("typo",))
    tool = create_tools(enabled=("generate_image",), image_model="antigravity/image")[0]
    assert tool.name == "generate_image"
    assert "antigravity/image" in tool.description
    assert "model" not in tool.parameters["properties"]


@pytest.mark.parametrize("groups", [
    (),
    (GroupDef(id="work"), GroupDef(id="other")),
])
def test_gateway_passes_tool_configuration_to_groups(groups):
    config = ClawConfig(
        model="copilot/chat", enabled_tools=("filesystem", "generate_image"),
        image_model="antigravity/image", groups=groups,
    )
    for group in _groups_from_config(config):
        assert group.enabled_tools == config.enabled_tools
        assert group.image_model == config.image_model


def test_group_runtime_constructs_only_enabled_tools(tmp_path):
    runtime = GroupRuntime(
        group=Group(id="main", enabled_tools=("filesystem", "generate_image"), image_model="antigravity/image"),
        data_dir=tmp_path,
    )
    tools = runtime.tools
    assert {tool.name for tool in tools} == {"filesystem", "generate_image"}
    assert "antigravity/image" in next(tool.description for tool in tools if tool.name == "generate_image")


def test_orchestrator_applies_configuration_to_dynamic_groups(tmp_path, monkeypatch):
    groups = []

    def create(group, *args, **kwargs):
        from types import SimpleNamespace
        groups.append(group)
        return SimpleNamespace(goal_manager=SimpleNamespace(_route_fn=None))

    monkeypatch.setattr(GroupRuntime, "create", create)
    orch = Orchestrator(ClawConfig(
        data_dir=str(tmp_path), enabled_tools=("generate_image",), image_model="antigravity/image",
    ))
    orch.auto_register_group("weixin:someone")
    assert groups[0].enabled_tools == ("generate_image",)
    assert groups[0].image_model == "antigravity/image"
    orch.register_group(Group(id="disabled", enabled_tools=()))
    assert groups[1].enabled_tools == ()


def test_gateway_requires_explicit_primary_model():
    with pytest.raises(ValueError, match="No primary model configured"):
        _build_model_and_options(ClawConfig())


def test_gateway_cli_reports_missing_model_before_starting_daemon(tmp_path, monkeypatch):
    from importlib import import_module
    from unittest.mock import Mock

    from click.testing import CliRunner
    from xdog.claw.cli.cli import cli
    from xdog.claw.config import save_config

    path = tmp_path / "config.yaml"
    save_config(ClawConfig(), path)
    start = Mock()
    monkeypatch.setattr(import_module("xdog.claw.cli.cli"), "_daemonize", start)
    result = CliRunner().invoke(cli, ["gateway", "start", "--config", str(path)])
    assert result.exit_code == 1
    assert "No primary model configured" in result.output
    start.assert_not_called()
