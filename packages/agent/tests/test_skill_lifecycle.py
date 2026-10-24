"""Skill bodies have an owning prompt run, not an anonymous history message."""

from __future__ import annotations

import asyncio

import pytest
from xdog.agent import Agent, AgentConfig, AgentTool, AgentToolResult
from xdog.agent.skills import SkillRepo
from xdog.agent.types import HandledInput
from xdog.ai.types import AssistantMessage, SystemPromptBlock, TextContent, ToolCall, UserMessage
from xdog.ai.utils.event_stream import EventStream


@pytest.fixture
def manager(tmp_path):
    for slug, scope, body in [("guard", "session", "SESSION_GUARD"), ("once", "turn", "TURN_PROCEDURE")]:
        directory = tmp_path / slug
        directory.mkdir()
        (directory / "SKILL.md").write_text(
            f"---\nname: {slug}\ndescription: procedure\nmetadata:\n  scope: {scope}\n---\n{body}"
        )
    return SkillRepo(builtin=False, packaged={}).register(directory=tmp_path, name="shared")


def make_agent(manager, selected=(), **kwargs):
    calls = []

    def stream(model, context, options):
        calls.append(context)
        return EventStream.empty(AssistantMessage(content=(TextContent(text="ok"),)))

    base = (SystemPromptBlock(text="STATIC", cache=True), SystemPromptBlock(text="DYNAMIC", cache=False))
    return Agent(
        stream,
        config=AgentConfig(model="offline/test", system_prompt=base),
        skills=manager,
        active_skills=selected,
        **kwargs,
    ), calls


async def prompt(agent, text="user request"):
    async for _ in await agent.prompt(text):
        pass
    await agent.wait_for_idle()


@pytest.mark.parametrize("constructor", [True, False])
async def test_turn_skill_one_run_only_without_history_or_prefix_churn(manager, constructor):
    agent, calls = make_agent(manager, ("guard", "once") if constructor else ("guard",))
    if not constructor:
        agent.set_active_skills(["guard", "once"])
    prefix = agent.state.system_prompt
    assert not agent.messages
    await prompt(agent)
    assert "TURN_PROCEDURE" in str(calls[0].messages)
    assert "TURN_PROCEDURE" not in str(agent.messages)
    assert agent.state.system_prompt == prefix
    await prompt(agent)
    assert "TURN_PROCEDURE" not in str(calls[1].messages)
    assert calls[1].system_prompt == prefix and "SESSION_GUARD" in str(prefix)


@pytest.mark.parametrize("outcome", ["handled", "rejected", "transform_failure"])
async def test_no_model_entry_preserves_pending_turn_skill(manager, outcome):
    agent, calls = make_agent(manager, ["once"])

    async def transform(message, source, cancel):
        if outcome == "handled":
            return HandledInput("handled without model")
        raise ValueError("input rejected")

    async def context_failure(messages):
        raise ValueError("context failed")

    if outcome == "transform_failure":
        agent.set_transform_context(context_failure)
    else:
        agent.set_input_transform(transform)
    await prompt(agent)
    assert not calls
    agent.set_input_transform(None)
    agent.set_transform_context(None)
    await prompt(agent)
    assert "TURN_PROCEDURE" in str(calls[0].messages)
    await prompt(agent)
    assert "TURN_PROCEDURE" not in str(calls[1].messages)


async def test_turn_body_survives_tool_rounds_and_followup_not_next_prompt(manager):
    calls = []

    async def execute(*args, **kwargs):
        return AgentToolResult(content=(TextContent(text="done"),))

    def stream(model, context, options):
        calls.append(context)
        message = (
            AssistantMessage(content=(ToolCall(id="one", name="read_test", arguments={}),), stop_reason="toolUse")
            if len(calls) == 1
            else AssistantMessage(content=(TextContent(text="ok"),))
        )
        return EventStream.empty(message)

    agent = Agent(
        stream,
        config=AgentConfig(model="offline/test"),
        skills=manager,
        active_skills=["once"],
        tools=[AgentTool(name="read_test", parameters={"type": "object"}, execute=execute)],
    )
    agent.follow_up("within original run")
    await prompt(agent)
    assert len(calls) == 3 and all("TURN_PROCEDURE" in str(c.messages) for c in calls)
    await prompt(agent)
    assert "TURN_PROCEDURE" not in str(calls[-1].messages)


async def test_busy_activation_refused_and_cancel_after_model_entry_retires(manager):
    started = asyncio.Event()

    async def held_stream(model, context, options):
        started.set()
        await options.cancel.wait()
        raise asyncio.CancelledError

    agent = Agent(held_stream, config=AgentConfig(model="offline/test"), skills=manager, active_skills=["once"])

    async def consume():
        await prompt(agent)

    task = asyncio.create_task(consume())
    await asyncio.wait_for(started.wait(), 2)
    try:
        with pytest.raises(RuntimeError, match="active|busy"):
            agent.set_active_skills(["guard"])
    finally:
        agent.abort()
        await asyncio.wait_for(task, 2)
    calls = []

    def stream(model, context, options):
        calls.append(context)
        return EventStream.empty(AssistantMessage())

    agent.set_stream_fn(stream)
    await prompt(agent)
    assert "TURN_PROCEDURE" not in str(calls[-1].messages)


async def test_clear_restore_and_continue_never_recreate_owned_transient_history(manager):
    agent, calls = make_agent(manager, ["once"])
    original = UserMessage(content="## Active skill: once\n\nUSER_TEXT_NOT_OWNED")
    agent.append_message(original)
    snapshot = agent.dump()
    assert "TURN_PROCEDURE" not in str(snapshot)
    async for _ in await agent.continue_():
        pass
    assert "TURN_PROCEDURE" not in str(calls[-1].messages)
    agent.clear_messages()
    agent.restore(snapshot)
    await prompt(agent)
    assert "TURN_PROCEDURE" in str(calls[-1].messages)
    agent.restore(agent.dump())
    await prompt(agent)
    assert "TURN_PROCEDURE" not in str(calls[-1].messages)
    assert "USER_TEXT_NOT_OWNED" in str(agent.messages)


@pytest.mark.parametrize("failure", ["raise", "error_message"])
async def test_provider_error_after_entry_retires_turn_skill(manager, failure):
    agent, calls = make_agent(manager, ["guard", "once"])

    def failed(model, context, options):
        calls.append(context)
        if failure == "raise":
            raise OSError("offline provider failed")
        return EventStream.empty(AssistantMessage(stop_reason="error", error_message="offline provider failed"))

    agent.set_stream_fn(failed)
    await prompt(agent)
    assert len(calls) == 1 and "TURN_PROCEDURE" in str(calls[0].messages)
    assert agent.active_skills == ("guard",)
    agent.set_stream_fn(lambda *args: EventStream.empty(AssistantMessage()))
    seen = []

    async def capture(messages):
        seen.extend(messages)
        return messages

    agent.set_transform_context(capture)
    with pytest.raises(RuntimeError, match="assistant"):
        await agent.continue_()
    agent.follow_up("Explicit retry after provider error")
    async for _ in await agent.continue_():
        pass
    await agent.wait_for_idle()
    assert "TURN_PROCEDURE" not in str(seen)
    await prompt(agent)
    assert "TURN_PROCEDURE" not in str(seen)
    assert "SESSION_GUARD" in str(agent.state.system_prompt)


@pytest.mark.parametrize("stage", ["input", "context"])
async def test_cancel_before_model_entry_preserves_pending_turn_skill(manager, stage):
    agent, calls = make_agent(manager, ["once"])
    started, release = asyncio.Event(), asyncio.Event()

    async def input_transform(message, source, cancel):
        started.set()
        await cancel.wait()
        raise asyncio.CancelledError

    async def context_transform(messages):
        started.set()
        await release.wait()
        return messages

    if stage == "input":
        agent.set_input_transform(input_transform)
    else:
        agent.set_transform_context(context_transform)
    task = asyncio.create_task(prompt(agent))
    try:
        await asyncio.wait_for(started.wait(), 2)
        agent.abort()
    finally:
        release.set()
        await asyncio.wait_for(task, 2)
    assert not calls and agent.active_skills == ("once",)
    agent.set_input_transform(None)
    agent.set_transform_context(None)
    await prompt(agent)
    assert "TURN_PROCEDURE" in str(calls[0].messages)
    await prompt(agent)
    assert "TURN_PROCEDURE" not in str(calls[1].messages)


@pytest.mark.parametrize("invalid", ["capabilities", "base64"])
async def test_image_validation_failure_before_entry_does_not_consume_turn_skill(manager, invalid):
    from xdog.ai.types import ImageContent, Model

    class Adapter:
        def __init__(self):
            self.calls = []

        def resolve_model(self, model):
            return Model(id=model, input=("text",) if invalid == "capabilities" else ("text", "image"))

        def __call__(self, model, context, options):
            self.calls.append(context)
            return EventStream.empty(AssistantMessage())

    adapter = Adapter()
    agent = Agent(adapter, config=AgentConfig(model="offline/image"), skills=manager, active_skills=["once"])
    image = ImageContent(data="not base64" if invalid == "base64" else "eA==", mime_type="image/png")
    async for _ in await agent.prompt("Reject incompatible image without a provider call", images=[image]):
        pass
    await agent.wait_for_idle()
    assert not adapter.calls and agent.state.error
    assert ("base64" if invalid == "base64" else "advertise") in agent.state.error
    assert agent.active_skills == ("once",)
    # Explicit host repair, not implicit pruning of invalid image history.
    agent.clear_messages()
    await prompt(agent)
    assert "TURN_PROCEDURE" in str(adapter.calls[0].messages)
    await prompt(agent)
    assert "TURN_PROCEDURE" not in str(adapter.calls[1].messages)


async def test_activation_captures_body_until_explicit_rearm_without_reloading_on_tool_round(manager):
    agent, calls = make_agent(manager, ["once"])
    skill = manager.load_skill("once")
    assert skill is not None and skill.path is not None
    original = skill.path.read_text()
    skill.path.write_text(original.replace("TURN_PROCEDURE", "CHANGED_BEFORE_RUN"))

    async def execute(*args, **kwargs):
        skill.path.write_text(original.replace("TURN_PROCEDURE", "CHANGED_DURING_RUN"))
        return AgentToolResult(content=(TextContent(text="done"),))

    def stream(model, context, options):
        calls.append(context)
        return EventStream.empty(
            AssistantMessage(
                content=(ToolCall(id="change", name="change_fixture", arguments={}),), stop_reason="toolUse"
            )
            if len(calls) == 1 else AssistantMessage()
        )

    agent.set_stream_fn(stream)
    agent.set_tools([AgentTool(name="change_fixture", parameters={"type": "object"}, execute=execute)])
    await prompt(agent)
    assert len(calls) == 2
    for context in calls:
        assert "TURN_PROCEDURE" in str(context.messages)
        assert "CHANGED_BEFORE_RUN" not in str(context.messages)
        assert "CHANGED_DURING_RUN" not in str(context.messages)
    assert "TURN_PROCEDURE" not in str(agent.messages)
    agent.set_active_skills(["once"])
    await prompt(agent)
    assert "CHANGED_DURING_RUN" in str(calls[-1].messages)
    await prompt(agent)
    assert "CHANGED_DURING_RUN" not in str(calls[-1].messages)
