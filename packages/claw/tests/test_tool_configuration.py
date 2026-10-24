"""Tool selection must reach every group, including newly registered groups."""
from __future__ import annotations

import pytest
from xdog.agent import AgentTool
from xdog.agent.tools import ToolRepo
from xdog.ai.types import AssistantMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import _build_model_and_options, _groups_from_config
from xdog.claw.core.runtime.group import GroupRuntime
from xdog.claw.core.runtime.orchestrator import Orchestrator
from xdog.claw.core.tools import AVAILABLE_TOOL_NAMES, DEFAULT_TOOL_NAMES, new_tool_repo
from xdog.claw.core.types import Group


def test_repo_contains_only_actual_selected_tools():
    repo = new_tool_repo()
    assert isinstance(repo, ToolRepo)
    assert repo.names() == frozenset(DEFAULT_TOOL_NAMES)
    assert {'generate_image', 'submit_result'}.isdisjoint(repo.names())
    assert new_tool_repo(image_model="antigravity/image").names() == repo.names()
    assert new_tool_repo(()).tools == ()
    assert new_tool_repo([]).tools == ()
    assert new_tool_repo(("filesystem",)).names() == {'filesystem'}
    assert new_tool_repo(("submit_result",)).names() == {'submit_result'}
    assert AVAILABLE_TOOL_NAMES == repo.names() | {'generate_image', 'submit_result'}


def test_groups_own_isolated_actual_tool_repositories(tmp_path):
    first = GroupRuntime(group=Group(id='one', enabled_tools=()), data_dir=tmp_path)
    second = GroupRuntime(group=Group(id='two', enabled_tools=()), data_dir=tmp_path)
    actual = AgentTool('custom')
    first._tools = [actual]
    first._enabled_tools = ('custom',)
    assert first.tools == [actual]
    assert not second.tools
    assert 'custom' not in second.tool_repo.names()
    assert 'custom' not in new_tool_repo().names()


def test_repo_requires_image_model_and_rejects_unknown_tools():
    with pytest.raises(ValueError, match="image_model configuration"):
        new_tool_repo(("generate_image",))
    with pytest.raises(ValueError, match="Unknown tools"):
        new_tool_repo(("typo",))
    tool = new_tool_repo(("generate_image",), image_model="antigravity/image").get('generate_image')
    assert "antigravity/image" in tool.description
    assert "model" not in tool.parameters["properties"]


def test_unselected_optional_tools_are_not_constructed(monkeypatch):
    from unittest.mock import Mock

    image = Mock(side_effect=AssertionError('unselected image tool'))
    memory = Mock(side_effect=AssertionError('unselected memory tool'))
    monkeypatch.setattr('xdog.claw.core.tools.create_generate_image_tool', image)
    monkeypatch.setattr('xdog.claw.core.tools.create_memory_tool', memory)
    assert new_tool_repo(('filesystem',)).names() == {'filesystem'}
    image.assert_not_called()
    memory.assert_not_called()


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


async def test_session_retirement_disposes_only_its_original_repo(tmp_path, monkeypatch):
    monkeypatch.setattr('xdog.agent.capabilities.runtime.discover', lambda scope: ())
    runtime = GroupRuntime(group=Group(id='main', enabled_tools=()), data_dir=tmp_path,
                           stream_fn=lambda *_: EventStream.empty(AssistantMessage()))
    try:
        first = await runtime.prepare_session(allow_reset=False)
        old_repo = first._tool_repo
        assert runtime.tool_repo.tools == first._child_tools
        runtime.reset_session()
        assert runtime.tool_repo.tools == ()
        second = await runtime.prepare_session(allow_reset=False)
        assert runtime.tool_repo.tools == second._child_tools
        assert second._child_tools[0] is not first._child_tools[0]
        for name in old_repo.names():
            old_repo.unregister(name)
        first.dispose()
        assert runtime.tool_repo.tools == second._child_tools
        assert tuple(second.agent.state.tools) == runtime.tool_repo.tools
    finally:
        await runtime.aclose()
    assert runtime.tool_repo.tools == ()


@pytest.mark.parametrize('failure', ['duplicate', 'publish'])
async def test_failed_inventory_replacement_keeps_original_repo_and_permissions(tmp_path, monkeypatch, failure):
    monkeypatch.setattr('xdog.agent.capabilities.runtime.discover', lambda scope: ())
    runtime = GroupRuntime(group=Group(id='main', enabled_tools=('current_time',)), data_dir=tmp_path,
                           stream_fn=lambda *_: EventStream.empty(AssistantMessage()))
    try:
        session = await runtime.prepare_session(allow_reset=False)
        agent = session.agent
        agent.set_active_tools(['current_time'], expected_revision=agent.tool_selection_revision)
        repo, tools, revision = runtime.tool_repo, agent.state.tools, agent.tool_selection_revision
        if failure == 'duplicate':
            runtime._tools = [AgentTool('current_time'), AgentTool('current_time')]
        else:
            runtime._tools = [AgentTool('replacement')]
            def refuse(_tools):
                raise ValueError('publication refused')
            monkeypatch.setattr(agent, 'set_tools', refuse)
        with pytest.raises(ValueError):
            runtime.publish_tools(session)
        assert runtime.tool_repo is session._tool_repo is repo
        assert agent.state.tools is tools
        assert repo.tools == agent.local_tools
        assert agent.tool_selection_revision == revision
        assert agent.active_tool_selection == ('current_time',)
    finally:
        await runtime.aclose()


def test_selected_image_tool_does_not_bypass_existing_validation(monkeypatch):
    from unittest.mock import Mock

    invalid = Mock(side_effect=ValueError('configured model rejected'))
    monkeypatch.setattr('xdog.claw.core.tools.create_generate_image_tool', invalid)
    with pytest.raises(ValueError, match='configured model rejected'):
        new_tool_repo(('generate_image',), image_model='configured/image')
    invalid.assert_called_once_with(model='configured/image')


def test_group_skill_repo_registers_explicit_shared_and_group_sources(tmp_path):
    from xdog.agent.skills import SkillRepo

    workspace = tmp_path / 'custom-workspace'
    runtime = GroupRuntime(group=Group(id='main', workspace=str(workspace)), data_dir=tmp_path / 'data')
    shared = runtime.skill_repo.save_skill('shared-example', 'shared body')
    local = runtime.skill_repo.save_skill('local-example', 'local body', scope='group')
    assert shared.path == tmp_path / 'data' / 'skills' / 'shared-example' / 'SKILL.md'
    assert local.path == workspace / 'skills' / 'local-example' / 'SKILL.md'
    isolated = SkillRepo(builtin=False)
    other = GroupRuntime(group=Group(id='isolated'), data_dir=tmp_path / 'data', skill_repo=isolated)
    assert other.skill_repo is isolated
    assert other.skill_repo.load_skill('shared-example') is None
    with pytest.raises(ValueError, match='Duplicate skill'):
        runtime.skill_repo.save_skill('shared-example', 'conflicting body', scope='group')
