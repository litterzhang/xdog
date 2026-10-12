"""Public Agent integration of Coding's single, opt-in compaction policy."""

from __future__ import annotations

import asyncio

import pytest
from xdog.agent import Agent, AgentConfig, AgentTool, AgentToolResult, CompactionConfig
from xdog.agent.events import CompactionEndEvent, CompactionFailedEvent, CompactionStartEvent, UsageEvent
from xdog.ai.types import AssistantMessage, TextContent, ToolCall, Usage, UserMessage
from xdog.ai.utils.event_stream import EventStream


def config(*, auto=False):
    return AgentConfig(
        model="offline/compact",
        system_prompt="SYSTEM_MUST_STAY",
        compaction=CompactionConfig(
            auto=auto,
            context_window=5000,
            output_reserve=128,
            safety_margin=100,
            instructions="Preserve goal, failures and materials",
        ),
    )


def history():
    return [
        UserMessage(content="old material " * 600),
        AssistantMessage(content=(TextContent(text="old answer " * 600),)),
        UserMessage(content="CURRENT_GOAL"),
    ]


async def test_manual_compact_exports_original_and_restores_without_enabling_auto():
    requests = []

    def stream(model, context, options):
        requests.append(context)
        assert not context.tools
        return EventStream.empty(
            AssistantMessage(
                content=(TextContent(text="concise summary of old material"),), usage=Usage(input=50, output=10)
            )
        )

    agent = Agent(stream, config=config())
    agent.replace_messages(history())
    original = agent.messages
    observed = []
    agent.subscribe(observed.append)
    result = await agent.compact()
    assert result is not None and result.removed_messages
    assert agent.messages == result.messages and agent.messages != original
    assert "CURRENT_GOAL" in str(agent.messages)
    assert agent.state.system_prompt == "SYSTEM_MUST_STAY"
    assert not agent._compaction_config.auto
    assert [e.type for e in observed] == ["compaction_start", "usage", "compaction_end"]
    assert observed[1].source == "compaction" and observed[1].usage.output == 10
    saved = agent.dump()
    await agent.aclose()
    target = Agent(stream, config=config())
    target.restore(saved)
    assert len(requests) == 1
    assert target.messages == result.messages
    assert target.dump()["compaction"] == saved["compaction"]
    await target.aclose()


async def test_one_prompt_many_tool_rounds_compacts_repeatedly_and_preserves_pairs():
    model_calls = []
    summary_calls = []

    async def execute(*args, **kwargs):
        return AgentToolResult(content=(TextContent(text="tool material " * 130),))

    def stream(model, context, options):
        if not context.tools:
            summary_calls.append(context)
            return EventStream.empty(
                AssistantMessage(
                    content=(TextContent(text="Goal and prior tool findings retained"),),
                    usage=Usage(input=10, output=5),
                )
            )
        model_calls.append(context)
        number = len(model_calls)
        assert context.system_prompt == "SYSTEM_MUST_STAY"
        assert "CURRENT_GOAL" in str(context.messages)
        if number <= 30:
            response = AssistantMessage(
                content=(ToolCall(id=f"call-{number}", name="read", arguments={}),),
                stop_reason="toolUse",
                usage=Usage(input=20, output=10),
            )
        else:
            response = AssistantMessage(content=(TextContent(text="finished"),))
        return EventStream.empty(response)

    async with Agent(
        stream, config=config(auto=True), tools=[AgentTool(name="read", parameters={"type": "object"}, execute=execute)]
    ) as agent:
        events = await asyncio.wait_for((await agent.prompt("CURRENT_GOAL")).collect(), 5)
        assert len(model_calls) == 31 and len(summary_calls) >= 2
        assert len([e for e in events if isinstance(e, CompactionEndEvent)]) == len(summary_calls)
        assert len([e for e in events if isinstance(e, UsageEvent) and e.source == "compaction"]) == len(summary_calls)
        assert not [e for e in events if isinstance(e, CompactionFailedEvent)]
        assert len({e.run_id for e in events}) == 1 and events[0].run_id
        assert [e.sequence for e in events] == sorted({e.sequence for e in events})
        snapshot = agent.dump()
        assert snapshot["compaction"]["generation"] == len(summary_calls)
        from xdog.agent.compaction import safe_cut_indices

        for context in model_calls:
            safe_cut_indices(context.messages)


async def test_default_off_does_not_issue_summary_requests():
    calls = []

    def stream(model, context, options):
        calls.append(context)
        return EventStream.empty(AssistantMessage())

    agent = Agent(stream, config=AgentConfig(model="offline/test"))
    agent.replace_messages(history())
    original = agent.messages
    await (await agent.prompt("next")).collect()
    assert len(calls) == 1 and agent.messages[: len(original)] == original
    await agent.aclose()


async def test_manual_summary_failure_preserves_history_and_reports_event():
    agent = Agent(lambda *args: EventStream.empty(AssistantMessage(content=())), config=config())
    agent.replace_messages(history())
    original = agent.dump()
    events = []
    agent.subscribe(events.append)
    with pytest.raises(ValueError, match="summary"):
        await agent.compact()
    assert agent.dump() == original
    assert isinstance(events[0], CompactionStartEvent) and isinstance(events[-1], CompactionFailedEvent)
    await agent.aclose()


async def test_close_during_manual_summary_settles_without_committing():
    started = asyncio.Event()
    cleaned = asyncio.Event()

    async def stream(*args):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    agent = Agent(stream, config=config())
    agent.replace_messages(history())
    original = agent.messages
    operation = asyncio.create_task(agent.compact())
    await asyncio.wait_for(started.wait(), 2)
    with pytest.raises(RuntimeError, match="idle"):
        await agent.compact()
    with pytest.raises(RuntimeError, match="processing"):
        await agent.prompt("new")
    await asyncio.wait_for(agent.aclose(), 2)
    with pytest.raises(asyncio.CancelledError):
        await operation
    assert agent.closed and cleaned.is_set() and agent.messages == original
    assert agent.dump()["compaction"]["generation"] == 0


async def test_auto_unknown_capacity_fails_without_calling_model():
    calls = []

    def stream(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())

    agent = Agent(stream, config=AgentConfig(model="offline/unknown", compaction=CompactionConfig(auto=True)))
    await (await agent.prompt("task")).collect()
    assert not calls and "Unknown model context capacity" in agent.state.error
    await agent.aclose()
