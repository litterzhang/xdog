"""The SDK and application hosts share capability composition, not lifetimes."""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from xdog.agent import Agent, AgentConfig, AgentTool, AgentToolResult, ToolRepo
from xdog.agent.capabilities import ApplicationPort, CapabilityAssembly, CapabilitySet
from xdog.agent.skills import SkillRepo
from xdog.agent.tool_effects import ToolEffect
from xdog.agent.types import BeforeToolCallResult
from xdog.ai.types import AssistantMessage, TextContent, ToolCall, ToolResultMessage, system_prompt_text
from xdog.ai.utils.event_stream import EventStream


async def execute(*args, **kwargs):
    return AgentToolResult.from_text("called")


def tool(name):
    return AgentTool(name=name, description=name, parameters={"type": "object"}, execute=execute)


class Provider:
    requirements = ()

    def __init__(self, name, *, scope="application", skill=None):
        self.name, self.scopes = name, (scope,)
        self.tool_names = (name,)
        self.default_skills = (skill,) if skill else ()
        self.opens = self.closes = 0
        self.actual = tool(name)
        self.items = (self.actual,)
        provider = self

        class Binding:
            @property
            def tools(self):
                return provider.items

            def tool_effect(self, candidate, args):
                return ToolEffect("execute", "unknown") if candidate in provider.items else None

            async def aclose(self):
                provider.closes += 1

        self.binding = Binding()

    async def open(self, host):
        self.opens += 1
        return self.binding


def sets(monkeypatch, *providers):
    monkeypatch.setattr(
        "xdog.agent.capabilities.runtime.discover", lambda scope: tuple(p for p in providers if scope in p.scopes)
    )
    return CapabilitySet("application"), CapabilitySet("session")


async def test_projection_is_inert_and_owns_no_provider_lifetime(monkeypatch):
    provider = Provider("application", skill="application")
    app, session = sets(monkeypatch, provider)
    assembly = CapabilityAssembly(app, session, app)
    assert assembly.sets == (app, session)
    assert assembly.declared_tools == {"application"}
    assert not assembly.tools and not assembly.default_skills
    assert provider.opens == provider.closes == 0
    await app.start(ApplicationPort())
    try:
        assert assembly.tools == (provider.actual,) and assembly.default_skills == {"application"}
        assert assembly.active_skills(["explicit"]) == {"explicit", "application"}
    finally:
        await app.aclose()
    assert not assembly.tools and not assembly.default_skills and provider.closes == 1


async def test_merge_retires_only_owned_identities_and_preserves_base_tools(monkeypatch):
    provider = Provider("one")
    app, _ = sets(monkeypatch, provider)
    assembly = CapabilityAssembly(app)
    await app.start(object())
    try:
        base = tool("base")
        assert assembly.compose_tools([base]) == (base, provider.actual)
        original = provider.actual
        provider.items = (tool("one"),)
        assert assembly.compose_tools((base, original)) == (base, provider.items[0])
        provider.items = ()
        assert assembly.compose_tools((base, original)) == (base,)
        provider.items = (tool("one"),)
        counterfeit = tool("one")
        with pytest.raises(ValueError, match="Duplicate"):
            assembly.compose_tools((counterfeit,))
        assert assembly.tool_effect(counterfeit, {}) is None
        assert assembly.tool_effect(provider.items[0], {}).access == "execute"
        assert assembly.tool_effect(original, {}) is None
    finally:
        await app.aclose()


async def test_cross_scope_collisions_fail_even_when_filtered(monkeypatch):
    first, second = Provider("same"), Provider("same", scope="session")
    app, session = sets(monkeypatch, first, second)
    await app.start(object())
    await session.start(object())
    try:
        assembly = CapabilityAssembly(app, session)
        with pytest.raises(ValueError, match="across capability scopes"):
            assembly.compose_tools()
    finally:
        await session.aclose()
        await app.aclose()


async def test_publish_does_not_reset_explicit_active_selection(monkeypatch):
    provider = Provider("app")
    app, _ = sets(monkeypatch, provider)
    await app.start(object())
    assembly = CapabilityAssembly(app)
    agent = Agent(lambda *_: EventStream.empty(AssistantMessage()), tools=[tool("base")])
    try:
        assembly.publish_tools(agent)
        agent.set_active_tools([], expected_revision=agent.tool_selection_revision)
        original = agent.tool_selection_revision
        publish = Mock(wraps=agent.set_tools)
        monkeypatch.setattr(agent, "set_tools", publish)
        assembly.publish_tools(agent)
        publish.assert_not_called()
        assert agent.tool_selection_revision == original and not agent.active_tools
    finally:
        await agent.aclose()
        await app.aclose()


async def test_sdk_factory_loads_default_and_explicit_skills_and_retains_permission_hook(tmp_path, monkeypatch):
    provider = Provider("app", skill="default")
    app, _ = sets(monkeypatch, provider)
    for slug in ("default", "explicit"):
        path = tmp_path / slug
        path.mkdir()
        (path / "SKILL.md").write_text(f"---\nname: {slug}\ndescription: example\n---\nBODY-{slug}")
    manager = SkillRepo(builtin=False, packaged={}).register(directory=tmp_path, name="shared")
    await app.start(object())
    assembly = CapabilityAssembly(app)
    contexts, permissions = [], []

    def model(_model, context, _options):
        contexts.append(context)
        return EventStream.empty(
            AssistantMessage(content=(ToolCall(id="effect", name="app", arguments={}),), stop_reason="toolUse")
            if context.messages[-1].role == "user"
            else AssistantMessage(content=(TextContent(text="done"),))
        )

    async def deny(ctx, cancel=None):
        permissions.append(ctx)
        return BeforeToolCallResult(block=True, reason="host declined")

    agent = assembly.create_agent(
        model,
        skill_repo=manager,
        config=AgentConfig(model="offline"),
        active_skills=["explicit"],
        before_tool_call=deny,
    )
    try:
        assert type(agent) is Agent
        stream = await agent.prompt("try tool")
        async for _ in stream:
            pass
        assert agent.active_skills == ("default", "explicit")
        prompt = system_prompt_text(contexts[0].system_prompt) or ""
        assert "BODY-default" in prompt and "BODY-explicit" in prompt
        assert permissions
        result = next(item for item in agent.messages if isinstance(item, ToolResultMessage))
        assert result.is_error and "host declined" in str(result.content)
        # Agent snapshots contain no live provider or application assembly.
        snapshot = agent.dump()
        assert "capabilities" not in snapshot and "assembly" not in snapshot
    finally:
        await agent.aclose()
    assert provider.closes == 0  # Factory never transfers application ownership.
    await app.aclose()
    assert provider.closes == 1


async def test_independent_capability_subsets_open_only_their_own_bindings(monkeypatch):
    first, second = Provider("first"), Provider("second")
    catalog, _ = sets(monkeypatch, first, second)
    async with catalog.select(["first"]).open() as a:
        async with catalog.select(["second"]).open() as b:
            async with a.create_agent(lambda *_: EventStream.empty(AssistantMessage())) as one:
                async with b.create_agent(lambda *_: EventStream.empty(AssistantMessage())) as two:
                    assert "first" in {t.name for t in one.local_tools}
                    assert "second" not in {t.name for t in one.local_tools}
                    assert "second" in {t.name for t in two.local_tools}
                    assert first.opens == second.opens == 1
    assert first.closes == second.closes == 1
    assert not catalog.started


def test_projection_rejects_fake_sources():
    with pytest.raises(TypeError):
        CapabilityAssembly(object())


async def test_sdk_factory_rejects_unopened_and_closed_owners(monkeypatch, tmp_path):
    provider = Provider("app")
    app, _ = sets(monkeypatch, provider)
    assembly = CapabilityAssembly(app)
    manager = SkillRepo(builtin=False, packaged={}).register(directory=tmp_path, name="shared")

    def stream(*_):
        return EventStream.empty(AssistantMessage())

    assert not app.started
    with pytest.raises(RuntimeError, match="Start each"):
        assembly.create_agent(stream, skill_repo=manager)
    await app.start(object())
    assert app.started
    await app.aclose()
    assert not app.started
    with pytest.raises(RuntimeError, match="Start each"):
        assembly.create_agent(stream, skill_repo=manager)


async def test_plain_agent_does_not_discover_or_register_capabilities(monkeypatch):
    def forbidden(scope):
        raise AssertionError("Plain Agent must not discover installed capabilities")

    monkeypatch.setattr("xdog.agent.capabilities.runtime.discover", forbidden)
    agent = Agent(lambda *_: EventStream.empty(AssistantMessage()))
    try:
        assert not agent.state.tools and not agent.active_skills
    finally:
        await agent.aclose()


async def test_factory_default_skill_must_resolve(monkeypatch, tmp_path):
    provider = Provider("app", skill="missing")
    app, _ = sets(monkeypatch, provider)
    await app.start(object())
    try:
        with pytest.raises(ValueError, match="missing"):
            CapabilityAssembly(app).create_agent(
                lambda *_: pytest.fail("No model call"),
                skill_repo=SkillRepo(builtin=False, packaged={}).register(directory=tmp_path, name="shared"),
            )
        assert provider.opens == 1 and provider.closes == 0
    finally:
        await app.aclose()


@pytest.mark.parametrize("parts", [False, True])
async def test_restore_reassembles_current_defaults_without_changing_runtime_authority(monkeypatch, tmp_path, parts):
    from packages.agent.tests.test_skill_lifecycle import make_agent

    path = tmp_path / "guard"
    path.mkdir()
    (path / "SKILL.md").write_text("---\nname: guard\ndescription: session instructions\n---\nCURRENT_SESSION_RULE")
    manager = SkillRepo(builtin=False, packaged={}).register(directory=tmp_path, name="shared")
    provider = Provider("app", skill="guard")
    app, _ = sets(monkeypatch, provider)
    await app.start(object())
    assembly = CapabilityAssembly(app)
    source, _ = make_agent(manager)
    checks = []

    async def deny(context, cancel=None):
        checks.append(context)
        return BeforeToolCallResult(block=True, reason="host denied")

    target = assembly.create_agent(
        lambda *_: pytest.fail("restore cannot call a model"),
        skill_repo=manager,
        config=AgentConfig(model="offline/test"),
        tool_repo=ToolRepo(*[tool("base")], builtin=False),
        before_tool_call=deny,
    )
    target.set_active_tools(["base"], expected_revision=target.tool_selection_revision)
    inventory, revision = target.state.tools, target.tool_selection_revision
    saved = source.dump_metadata() if parts else source.dump()
    try:
        for _ in range(2):
            if parts:
                assembly.restore_agent_parts(target, saved, source.messages, allowed_models=["offline/test"])
            else:
                assembly.restore_agent(target, saved, allowed_models=["offline/test"])
            assert target.active_skills == ("guard",)
            assert str(target.state.system_prompt).count("CURRENT_SESSION_RULE") == 1
            assert target.state.tools == inventory and target.tool_selection_revision == revision
            assert {tool.name for tool in target.active_tools} == {"base"}
            assert target._before_tool_call is deny and not checks
        # Plain restore deliberately keeps the old snapshot-only contract.
        target.restore(source.dump())
        assert not target.active_skills
    finally:
        await target.aclose()
        await source.aclose()
        await app.aclose()


@pytest.mark.parametrize("failure", ["default_missing", "saved_changed", "saved_missing", "model", "adapter"])
@pytest.mark.parametrize("parts", [False, True])
async def test_restore_reassembly_failures_are_atomic(monkeypatch, tmp_path, failure, parts):
    from packages.agent.tests.test_skill_lifecycle import make_agent

    for slug in ("guard", "current"):
        directory = tmp_path / slug
        directory.mkdir()
        (directory / "SKILL.md").write_text(f"---\nname: {slug}\n---\nBODY-{slug}")
    manager = SkillRepo(builtin=False, packaged={}).register(directory=tmp_path, name="shared")
    provider = Provider("app", skill="current")
    app, _ = sets(monkeypatch, provider)
    await app.start(object())
    assembly = CapabilityAssembly(app)
    source, _ = make_agent(manager, ("guard",))
    target = assembly.create_agent(
        lambda *_: pytest.fail("No model calls"), skill_repo=manager, config=AgentConfig(model="offline/test")
    )
    target.set_active_tools([], expected_revision=target.tool_selection_revision)
    before, revision = target.dump(), target.tool_selection_revision
    if failure in ("default_missing", "saved_missing"):
        (tmp_path / ("current" if failure == "default_missing" else "guard") / "SKILL.md").unlink()
    if failure == "saved_changed":
        (tmp_path / "guard" / "SKILL.md").write_text("CHANGED")
    if failure == "adapter":

        class Adapter:
            def resolve_model(self, name):
                return None

            def __call__(self, *args):
                pytest.fail("No model calls")

        target.set_stream_fn(Adapter())
    allowed = ["not-allowed"] if failure == "model" else ["offline/test"]
    try:
        with pytest.raises(ValueError):
            if parts:
                assembly.restore_agent_parts(target, source.dump_metadata(), source.messages, allowed_models=allowed)
            else:
                assembly.restore_agent(target, source.dump(), allowed_models=allowed)
        assert target.dump() == before and target.tool_selection_revision == revision
        assert not target.active_tools
    finally:
        await source.aclose()
        await target.aclose()
        await app.aclose()


@pytest.mark.parametrize("lifecycle", ["pending", "active", "consumed"])
@pytest.mark.parametrize("parts", [False, True])
async def test_reassembly_preserves_turn_lifecycle_and_does_not_rearm_defaults(monkeypatch, tmp_path, lifecycle, parts):
    from xdog.ai.types import UserMessage

    for slug, scope in [("guard", "session"), ("once", "turn")]:
        directory = tmp_path / slug
        directory.mkdir()
        (directory / "SKILL.md").write_text(f"---\nname: {slug}\nmetadata:\n  scope: {scope}\n---\nRULE-{slug}")
    manager = SkillRepo(builtin=False, packaged={}).register(directory=tmp_path, name="shared")
    provider = Provider("app", skill="guard")
    provider.default_skills = ("guard", "once")
    app, _ = sets(monkeypatch, provider)
    await app.start(object())
    assembly = CapabilityAssembly(app)
    source = Agent(
        lambda *_: pytest.fail("source stays idle"),
        config=AgentConfig(model="offline/test"),
        skills=manager,
        active_skills=["once"],
    )
    source.append_message(UserMessage(content="saved request"))
    saved = source.dump_metadata() if parts else source.dump()
    saved["skills"][0]["lifecycle"] = lifecycle
    if lifecycle == "active":
        saved.update(run_id="original-run", run_active=True)
    calls = []

    def stream(model, context, options):
        calls.append(context)
        return EventStream.empty(AssistantMessage(content=(TextContent(text="done"),)))

    target = assembly.create_agent(stream, skill_repo=manager)
    try:
        if parts:
            assembly.restore_agent_parts(target, saved, source.messages)
        else:
            assembly.restore_agent(target, saved)
        assert not calls
        assert set(target.active_skills) == ({"guard"} if lifecycle == "consumed" else {"once", "guard"})
        if lifecycle == "active":
            await (await target.continue_()).collect()
        else:
            await (await target.prompt("first")).collect()
        assert ("RULE-once" in str(calls[0].messages)) is (lifecycle != "consumed")
        assert target.active_skills == ("guard",)
        await (await target.prompt("second")).collect()
        assert "RULE-once" not in str(calls[-1].messages)
        # A saved session after consumption has no turn record. Defaults cannot
        # resurrect it even across another explicit SDK reassembly.
        assembly.restore_agent(target, target.dump())
        assert target.active_skills == ("guard",)
    finally:
        await source.aclose()
        await target.aclose()
        await app.aclose()


async def test_legacy_restore_keeps_partial_patch_and_pending_turn_lifetime(monkeypatch, tmp_path):
    for slug, scope in [("guard", "session"), ("once", "turn")]:
        path = tmp_path / slug
        path.mkdir()
        (path / "SKILL.md").write_text(f"---\nname: {slug}\nmetadata:\n  scope: {scope}\n---\nRULE-{slug}")
    manager = SkillRepo(builtin=False, packaged={}).register(directory=tmp_path, name="shared")
    provider = Provider("app", skill="guard")
    app, _ = sets(monkeypatch, provider)
    await app.start(object())
    assembly = CapabilityAssembly(app)
    target = Agent(lambda *_: pytest.fail("No model calls"), skills=manager, active_skills=["once"])
    try:
        assembly.restore_agent(target, {"system_prompt": "LEGACY_BASE"})
        assert target.active_skills == ("once", "guard")
        assert {item["slug"]: item["lifecycle"] for item in target.dump()["skills"]} == {
            "once": "pending",
            "guard": "active",
        }
        assert "LEGACY_BASE" in str(target.state.system_prompt)
        before = target.dump()
        (tmp_path / "guard" / "SKILL.md").unlink()
        target.set_active_skills(["once"])
        before = target.dump()
        with pytest.raises(ValueError):
            assembly.restore_agent(target, {"system_prompt": "MUST_NOT_ADOPT"})
        assert target.dump() == before
    finally:
        await target.aclose()
        await app.aclose()


async def test_public_offline_reassembly_example(tmp_path):
    from packages.agent.examples.capability_restore import demonstrate

    assert await demonstrate(tmp_path) == {
        "active_skills": ["guard"],
        "declared_skills": ["guard"],
        "tools": 1,
        "model_calls": 0,
        "snapshot_skill": "guard",
        "closed": ["application"],
    }


async def test_restore_rejects_unopened_and_closed_assembly_without_mutation(monkeypatch, tmp_path):
    app, _ = sets(monkeypatch, Provider("app"))
    assembly = CapabilityAssembly(app)
    agent = Agent(
        lambda *_: pytest.fail("No model"),
        skills=SkillRepo(builtin=False, packaged={}).register(directory=tmp_path, name="shared"),
    )
    before = agent.dump()
    try:
        with pytest.raises(RuntimeError, match="Start each"):
            assembly.restore_agent(agent, before)
        await app.start(object())
        await app.aclose()
        with pytest.raises(RuntimeError, match="Start each"):
            assembly.restore_agent_parts(agent, agent.dump_metadata(), ())
        assert agent.dump() == before
    finally:
        await agent.aclose()
        await app.aclose()


async def test_reassembled_agent_still_uses_original_host_permission_hook(monkeypatch, tmp_path):
    path = tmp_path / "guard"
    path.mkdir()
    (path / "SKILL.md").write_text("---\nname: guard\ndescription: instructions\n---\nRESTORED_RULE")
    manager = SkillRepo(builtin=False, packaged={}).register(directory=tmp_path, name="shared")
    provider = Provider("app", skill="guard")
    app, _ = sets(monkeypatch, provider)
    await app.start(object())
    assembly = CapabilityAssembly(app)
    source = Agent(lambda *_: pytest.fail("source stays idle"), config=AgentConfig(model="offline/test"))
    checks, effects = [], []

    async def execute_base(*args, **kwargs):
        effects.append("executed")
        return AgentToolResult.from_text("must not happen")

    base = AgentTool(name="base", parameters={"type": "object"}, execute=execute_base)

    async def deny(context, cancel=None):
        checks.append(context)
        return BeforeToolCallResult(block=True, reason="original host permission")

    def stream(model, context, options):
        assert {tool.name for tool in context.tools} == {"base"}
        if context.messages[-1].role == "user":
            return EventStream.empty(
                AssistantMessage(content=(ToolCall(id="read", name="base", arguments={}),), stop_reason="toolUse")
            )
        return EventStream.empty(AssistantMessage(content=(TextContent(text="done"),)))

    target = assembly.create_agent(
        stream, skill_repo=manager, tool_repo=ToolRepo(*[base], builtin=False), before_tool_call=deny
    )
    target.set_active_tools(["base"], expected_revision=target.tool_selection_revision)
    try:
        assembly.restore_agent(target, source.dump(), allowed_models=["offline/test"])
        assert not checks and not effects
        await (await target.prompt("test authority")).collect()
        assert len(checks) == 1 and not effects
        result = next(message for message in target.messages if isinstance(message, ToolResultMessage))
        assert result.is_error and "original host permission" in str(result.content)
    finally:
        await target.aclose()
        await source.aclose()
        await app.aclose()
