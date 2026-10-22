"""Tool selection must reach every group, including newly registered groups."""
from __future__ import annotations

import pytest
from xdog.agent import AgentTool
from xdog.agent.tools import ToolContext, ToolRegistry
from xdog.ai.types import AssistantMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import _build_model_and_options, _groups_from_config
from xdog.claw.core.runtime.group import GroupRuntime
from xdog.claw.core.runtime.orchestrator import Orchestrator
from xdog.claw.core.tools import new_tool_registry
from xdog.claw.core.types import Group


def test_registry_distinguishes_defaults_from_no_tools():
    registry = new_tool_registry()
    assert isinstance(registry, ToolRegistry)
    assert registry.registered_names() == {
        'bash', 'filesystem', 'current_time', 'submit_result',
        'memory', 'message', 'skill', 'todo', 'generate_image',
    }
    assert {'generate_image', 'submit_result'}.isdisjoint(registry.default_names())
    assert {tool.name for tool in registry.create_tools()} == set(registry.default_names())
    tools = registry.create_tools(context=ToolContext(image_model="antigravity/image"))
    assert "generate_image" not in {tool.name for tool in tools}
    assert registry.create_tools(names=()) == []
    assert registry.create_tools(names=[]) == []
    assert [tool.name for tool in registry.create_tools(names=("filesystem",))] == ["filesystem"]
    assert [tool.name for tool in registry.create_tools(names=("submit_result",))] == ["submit_result"]
    assert registry.tools == ()  # Inventory discovery/creation never publishes by itself.


def test_groups_own_isolated_sdk_registries_and_factory_context(tmp_path):
    first = GroupRuntime(group=Group(id='one', enabled_tools=()), data_dir=tmp_path)
    second = GroupRuntime(group=Group(id='two', enabled_tools=()), data_dir=tmp_path)
    seen = []
    def factory(context: ToolContext) -> AgentTool:
        seen.append(context)
        return AgentTool('custom')
    first.tool_registry.register_factory('custom', factory)
    first._enabled_tools = ('custom',)
    assert [tool.name for tool in first.tools] == ['custom']
    assert seen == [ToolContext(cwd=first.workspace_dir)]
    assert not second.tools
    assert 'custom' not in second.tool_registry.registered_names()
    assert 'custom' not in new_tool_registry().registered_names()


def test_registry_requires_image_model_and_rejects_unknown_tools():
    with pytest.raises(ValueError, match="image_model configuration"):
        new_tool_registry().create_tools(names=("generate_image",))
    with pytest.raises(ValueError, match="Unknown tools"):
        new_tool_registry().create_tools(names=("typo",))
    tool = new_tool_registry().create_tools(
        names=("generate_image",), context=ToolContext(image_model="antigravity/image"))[0]
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
        return SimpleNamespace()

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


async def test_session_retirement_disposes_only_its_original_registry_binding(tmp_path, monkeypatch):
    monkeypatch.setattr('xdog.agent.capabilities.runtime.discover', lambda scope: ())
    runtime = GroupRuntime(group=Group(id='main', enabled_tools=()), data_dir=tmp_path,
                           stream_fn=lambda *_: EventStream.empty(AssistantMessage()))
    try:
        first = await runtime.prepare_session(allow_reset=False)
        old_registration = first._tool_registration
        assert runtime.tool_registry.tools == first._child_tools
        runtime.reset_session()
        assert runtime.tool_registry.tools == ()
        second = await runtime.prepare_session(allow_reset=False)
        assert runtime.tool_registry.tools == second._child_tools
        assert second._child_tools[0] is not first._child_tools[0]
        old_registration.close()
        first.dispose()
        assert runtime.tool_registry.tools == second._child_tools
        assert tuple(second.agent.state.tools) == runtime.tool_registry.tools
    finally:
        await runtime.aclose()
    assert runtime.tool_registry.tools == ()
