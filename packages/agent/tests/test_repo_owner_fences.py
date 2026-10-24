"""Regression fences for additive repositories, selected skills and owner receipts."""
from __future__ import annotations

import threading
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from dataclasses import replace
from typing import NoReturn

import pytest
from xdog.agent import Agent, AgentConfig, AgentTool, AgentToolResult, ToolRepo
from xdog.agent.capabilities import ApplicationPort, CapabilityAssembly, CapabilitySet
from xdog.agent.child_policy import ChildSkills, ParentHandle
from xdog.agent.skills import Skill, SkillRepo, create_skill_tool
from xdog.agent.skills.tool import _Reader


def _no_model(*args: object, **kwargs: object) -> NoReturn:
    raise AssertionError("Repository composition must not call a model")


def _catalog(*slugs: str) -> SkillRepo:
    repo = SkillRepo(builtin=False)
    for slug in slugs:
        repo.register(Skill(name=slug, slug=slug, description=slug, content=f"PRIVATE_BODY_{slug}"))
    return repo


def _parent(repo: SkillRepo) -> ParentHandle:
    return ParentHandle(alive=lambda: True, tools=lambda: (), skills=lambda: repo)


def _skill_tool(agent: Agent) -> AgentTool:
    return next(tool for tool in agent.local_tools if tool.name == "skill")


async def _load(tool: AgentTool, slug: str) -> AgentToolResult:
    assert tool.execute is not None
    return await tool.execute("read", {"action": "load", "slug": slug})


class _Provider:
    name = "fixture"
    scopes = ("application",)
    requirements: tuple[str, ...] = ()
    tool_names = ("cap_action", "cap_added")
    skill_names: tuple[str, ...] = ()
    default_skills: tuple[str, ...] = ()

    def __init__(self) -> None:
        self.actual = AgentTool(name="cap_action", parameters={"type": "object"})
        self.items = (self.actual,)
        self.closes = 0

    async def open(self, host: object) -> _Binding:
        return _Binding(self)


class _Binding:
    def __init__(self, provider: _Provider) -> None:
        self.provider = provider

    @property
    def tools(self) -> tuple[AgentTool, ...]:
        return self.provider.items

    async def aclose(self) -> None:
        self.provider.closes += 1


@pytest.fixture
async def capability_owner(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[tuple[_Provider, CapabilityAssembly]]:
    provider = _Provider()
    monkeypatch.setattr("xdog.agent.capabilities.runtime.discover", lambda scope: (provider,))
    owner = CapabilitySet("application", skill_repo=SkillRepo(builtin=False))
    await owner.start(ApplicationPort())
    try:
        yield provider, CapabilityAssembly(owner)
    finally:
        await owner.aclose()
    assert provider.closes == 1


@pytest.mark.parametrize("explicit_tool_repo", [False, True])
async def test_each_agent_gets_its_own_additive_skill_reader(capability_owner, explicit_tool_repo):
    provider, assembly = capability_owner
    alpha = _catalog("alpha", "hidden-alpha").select(("alpha",))
    beta = _catalog("beta", "hidden-beta").select(("beta",))
    kwargs = {"tool_repo": ToolRepo(builtin=False).select(())} if explicit_tool_repo else {}
    async with AsyncExitStack() as stack:
        first = await stack.enter_async_context(assembly.create_agent(
            _no_model, config=AgentConfig(model="offline"), skill_repo=alpha))
        second = await stack.enter_async_context(assembly.create_agent(
            _no_model, config=AgentConfig(model="offline"), skill_repo=beta, **kwargs))
        first_reader, second_reader = _skill_tool(first), _skill_tool(second)
        assert first_reader is not second_reader
        assert "skill" not in assembly.tool_repo.names()
        assert any(tool is provider.actual for tool in first.local_tools)
        assert any(tool is provider.actual for tool in second.local_tools)
        assert [row.slug for row in first._skills.list_skills()] == ["alpha"]
        assert [row.slug for row in second._skills.list_skills()] == ["beta"]
        for reader, allowed, excluded in ((first_reader, "alpha", "beta"), (second_reader, "beta", "alpha")):
            result = await _load(reader, allowed)
            assert not result.is_error and f"PRIVATE_BODY_{allowed}" in str(result.content)
            for slug in (excluded, "hidden-alpha", "hidden-beta"):
                denied = await _load(reader, slug)
                assert denied.is_error and f"PRIVATE_BODY_{slug}" not in str(denied.content)


async def test_failed_agent_creation_does_not_publish_its_reader(capability_owner):
    _, assembly = capability_owner
    with pytest.raises(ValueError):
        assembly.create_agent(_no_model, config=AgentConfig(model="offline"),
                              skill_repo=_catalog("alpha"), active_skills=("missing",))
    assert "skill" not in assembly.tool_repo.names()
    async with assembly.create_agent(_no_model, config=AgentConfig(model="offline"),
                                     skill_repo=_catalog("beta")) as agent:
        assert not (await _load(_skill_tool(agent), "beta")).is_error
        assert (await _load(_skill_tool(agent), "alpha")).is_error


@pytest.mark.parametrize("delegated", [False, True])
@pytest.mark.parametrize("selected", [(), ("alpha",)])
def test_selected_catalog_direct_reader_merge_and_delegation_agree(delegated, selected):
    original = _catalog("alpha", "beta")
    source = ChildSkills(original, ("alpha", "beta")) if delegated else original
    subset = source.select(selected)
    reader = _Reader(subset, None, 262144, 512)
    assert tuple(row.slug for row in subset.list_skills()) == selected
    assert tuple(row["slug"] for row in reader.listing(threading.Event())["skills"]) == selected
    if delegated:
        assert subset.slugs == selected
    for slug in ("alpha", "beta"):
        direct = subset.load_skill(slug)
        if slug in selected:
            assert direct == reader.load(slug, threading.Event())
        else:
            assert direct is None
            with pytest.raises(ValueError):
                reader.load(slug, threading.Event())
    for repo in (subset, SkillRepo(builtin=False).merge(subset)):
        parent = _parent(repo)
        assert parent.skill_catalog() == selected
        assert parent.capture_skills(None).slugs == selected
        for slug in set(("alpha", "beta")) - set(selected):
            with pytest.raises(ValueError):
                parent.capture_skills((slug,))
    # Selecting is independent: it must not narrow the original parent's inventory.
    assert {row.slug for row in source.list_skills()} == {"alpha", "beta"}


@pytest.mark.parametrize("selected", [(), ("alpha",)])
async def test_selected_child_tool_cannot_read_excluded_bodies(selected):
    original = _catalog("alpha", "beta")
    subset = ChildSkills(original, ("alpha", "beta")).select(selected)
    scoped = subset.tool(create_skill_tool(original), lambda: True)
    for slug in ("alpha", "beta"):
        if slug in selected:
            result = await _load(scoped, slug)
            assert not result.is_error and f"PRIVATE_BODY_{slug}" in str(result.content)
        else:
            with pytest.raises(ValueError):
                await _load(scoped, slug)


def test_child_subset_live_check_does_not_retain_excluded_parent_obligations():
    parent = _catalog("alpha", "beta")
    full = ChildSkills(parent, ("alpha", "beta"))
    subset = full.select(("alpha",))
    empty = full.select(())
    parent.unregister("beta")
    subset.check_live(parent)
    empty.check_live(None)
    with pytest.raises(ValueError):
        full.check_live(parent)
    parent.unregister("alpha")
    with pytest.raises(ValueError):
        subset.check_live(parent)


@pytest.mark.parametrize("change", ["add", "replace", "remove"])
async def test_capability_refresh_preserves_local_registration_receipts(capability_owner, change):
    provider, assembly = capability_owner
    repo = assembly.tool_repo
    local = AgentTool(name="local", parameters={"type": "object"})
    receipt = repo.register(local)
    old_local = AgentTool(name="replacement", parameters={"type": "object"})
    stale_receipt = repo.register(old_local)
    repo.unregister(old_local.name)
    replacement = replace(old_local, description="new local binding")
    replacement_receipt = repo.register(replacement)
    if change == "add":
        provider.items = (provider.actual, AgentTool(name="cap_added", parameters={"type": "object"}))
    elif change == "replace":
        provider.items = (replace(provider.actual, description="new capability binding"),)
    else:
        provider.items = ()
    assert assembly.tool_repo is repo
    assert repo.get("local") is local
    assert all(repo.get(tool.name) is tool for tool in provider.items)
    if change != "add":
        assert all(tool is not provider.actual for tool in repo.tools)
    stale_receipt.close()
    assert repo.get("replacement") is replacement
    receipt.close()
    replacement_receipt.close()
    receipt.close()  # Close remains idempotent after a refresh and withdrawal.
    assert repo.names() == frozenset(tool.name for tool in provider.items)


async def test_conflicting_additive_tool_does_not_replace_capability_or_local_receipt(capability_owner):
    provider, assembly = capability_owner
    repo = assembly.tool_repo
    local = AgentTool(name="local", parameters={"type": "object"})
    receipt = repo.register(local)
    impostor = replace(provider.actual, description="different same-name executor")
    with pytest.raises(ValueError, match="Duplicate"):
        assembly.create_agent(_no_model, config=AgentConfig(model="offline"),
                              tool_repo=ToolRepo(impostor, builtin=False))
    assert repo.get(provider.actual.name) is provider.actual
    assert repo.get(local.name) is local
    receipt.close()
    assert local.name not in repo.names()
