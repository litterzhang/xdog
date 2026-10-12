"""Owned shutdown, independent of event consumption or host provider ownership."""

from __future__ import annotations

import asyncio

import pytest
from xdog.agent import Agent, AgentConfig, AgentTool, AgentToolResult
from xdog.ai.types import AssistantMessage, DoneEvent, StartEvent, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream


def agent_with(stream):
    return Agent(stream, config=AgentConfig(model="offline/close"))


async def test_close_idle_is_idempotent_and_permanently_denies_new_work():
    calls = []

    def stream(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())

    agent = agent_with(stream)
    async with agent as same:
        assert same is agent
        assert not agent.closed
    assert agent.closed
    await agent.aclose()
    agent.reset()  # Clearing history must never resurrect a closed runtime.
    for operation in (lambda: agent.prompt("new"), agent.continue_):
        with pytest.raises(RuntimeError, match="clos"):
            await operation()
    for operation in (
        lambda: agent.steer("new"),
        lambda: agent.follow_up("new"),
        lambda: agent.queue_input("id", "new", "steer"),
    ):
        with pytest.raises(RuntimeError, match="clos"):
            operation()
    assert not calls


async def test_close_before_producer_first_step_never_calls_model():
    calls = []

    def stream(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())

    agent = agent_with(stream)
    events = await agent.prompt("new")
    await agent.aclose()
    assert agent.closed and events.closed and not calls
    assert agent._producer_task.done()


async def test_close_cancels_waiting_adapter_without_event_consumer():
    started = asyncio.Event()
    cleaned = asyncio.Event()
    signals = []

    async def stream(model, context, options):
        signals.append(options.cancel)
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    agent = agent_with(stream)
    events = await agent.prompt("new")
    await asyncio.wait_for(started.wait(), 2)
    await asyncio.wait_for(agent.aclose(), 2)
    assert signals[0].is_set() and cleaned.is_set()
    assert events.closed and agent.closed


@pytest.mark.parametrize("finish_normally", [False, True])
async def test_pull_stream_generator_is_closed_before_agent_settles(finish_normally):
    started = asyncio.Event()
    cleaned = asyncio.Event()
    message = AssistantMessage(content=(TextContent(text="reply"),))

    def stream(*args):
        result = asyncio.get_running_loop().create_future()

        async def generate():
            try:
                started.set()
                yield StartEvent(partial=message)
                if finish_normally:
                    result.set_result(message)
                    yield DoneEvent(stop_reason="stop", message=message)
                    await asyncio.Event().wait()  # Result must not drain beyond done.
                else:
                    await asyncio.Event().wait()
            finally:
                cleaned.set()

        return EventStream.from_async_generator(generate(), result)

    agent = agent_with(stream)
    events = await agent.prompt("new")
    await asyncio.wait_for(started.wait(), 2)
    if finish_normally:
        await asyncio.wait_for(events.collect(), 2)
        assert cleaned.is_set()  # Not eventual async-generator GC.
    await asyncio.wait_for(agent.aclose(), 2)
    assert cleaned.is_set() and agent.closed


async def test_cancelled_close_waiter_does_not_abandon_parallel_tool_cleanup():
    both_started = asyncio.Event()
    cleaning = asyncio.Event()
    release = asyncio.Event()
    started = []
    settled = []
    signals = []

    async def execute(call_id, args, cancel, on_update, **kwargs):
        started.append(call_id)
        signals.append(cancel)
        if len(started) == 2:
            both_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await release.wait()
            settled.append(call_id)
        return AgentToolResult(content=())

    def stream(*args):
        return EventStream.empty(
            AssistantMessage(
                content=tuple(ToolCall(id=str(i), name="hold", arguments={}) for i in range(2)), stop_reason="toolUse"
            )
        )

    agent = agent_with(stream)
    agent.set_tools([AgentTool(name="hold", parameters={"type": "object"}, execute=execute)])
    await agent.prompt("new")
    await asyncio.wait_for(both_started.wait(), 2)
    try:
        waiter = asyncio.create_task(agent.aclose())
        await asyncio.wait_for(cleaning.wait(), 2)
        assert not agent.closed
        with pytest.raises(RuntimeError, match="clos"):
            await agent.prompt("must not start")
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert not agent.closed
        release.set()
        await asyncio.wait_for(asyncio.gather(agent.aclose(), agent.aclose()), 2)
        assert agent.closed and sorted(settled) == ["0", "1"]
        assert all(signal.is_set() for signal in signals)
    finally:
        release.set()
        await agent.cancel_and_wait()


async def test_close_from_own_tool_rejected_without_self_deadlock():
    errors = []
    calls = []

    async def execute(*args, **kwargs):
        with pytest.raises(RuntimeError, match="own.*run|run.*own") as error:
            await agent.aclose()
        errors.append(str(error.value))
        return AgentToolResult(content=())

    def stream(*args):
        calls.append(args)
        return EventStream.empty(
            AssistantMessage(
                content=(ToolCall(id="one", name="self_close", arguments={}),) if len(calls) == 1 else (),
                stop_reason="toolUse" if len(calls) == 1 else "stop",
            )
        )

    agent = agent_with(stream)
    agent.set_tools([AgentTool(name="self_close", parameters={"type": "object"}, execute=execute)])
    await asyncio.wait_for((await agent.prompt("new")).collect(), 2)
    assert len(errors) == 1 and not agent.closed
    await agent.aclose()


async def test_shared_provider_is_not_closed_by_agent():
    class Adapter:
        closed = False

        def __call__(self, *args):
            return EventStream.empty(AssistantMessage())

        async def aclose(self):
            self.closed = True

    adapter = Adapter()
    async with agent_with(adapter) as agent:
        await (await agent.prompt("new")).collect()
    assert not adapter.closed


async def test_stream_cleanup_failure_is_not_reported_as_complete_close():
    class BrokenStream(EventStream):
        async def aclose(self):
            raise OSError("cannot release stream")

    agent = agent_with(lambda *args: BrokenStream.empty(AssistantMessage()))
    await asyncio.wait_for((await agent.prompt("new")).collect(), 2)
    with pytest.raises(RuntimeError, match="cleanup.*incomplete"):
        await agent.aclose()
    assert not agent.closed
    with pytest.raises(RuntimeError, match="cleanup.*incomplete"):
        await agent.prompt("must not start")
