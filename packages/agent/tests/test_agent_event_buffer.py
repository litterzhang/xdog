"""Observation backpressure is bounded and is not a persistence barrier."""

from __future__ import annotations

import asyncio

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.agent.event_stream import AgentEventStream
from xdog.agent.events import AgentEndEvent
from xdog.ai.types import AssistantMessage, StartEvent, TextContent, TextDeltaEvent
from xdog.ai.utils.event_stream import EventStream


async def test_bounded_send_waits_for_consumer_without_losing_events():
    stream = AgentEventStream[int](max_buffered_events=1)
    await stream.send(1)
    blocked = asyncio.create_task(stream.send(2))
    await asyncio.sleep(0)
    assert not blocked.done() and stream.buffered_events == 1
    iterator = stream.__aiter__()
    assert await anext(iterator) == 1
    await asyncio.wait_for(blocked, 1)
    stream.end("result")
    assert await stream.result() == "result"
    assert [event async for event in iterator] == [2]
    assert await stream.collect() == []


async def test_close_full_buffer_releases_producer_and_preserves_terminal():
    stream = AgentEventStream[int](max_buffered_events=1)
    await stream.send(1)
    blocked = asyncio.create_task(stream.send(2))
    await asyncio.sleep(0)
    stream.send_final(99)
    await stream.close()
    await asyncio.wait_for(blocked, 1)
    assert await stream.collect() == [1, 99]


async def test_result_wait_does_not_steal_events_or_cancel_shared_result():
    stream = AgentEventStream[int]()
    waiter = asyncio.create_task(stream.result())
    await stream.send(1)
    await asyncio.sleep(0)
    assert stream.buffered_events == 1
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    stream.end("value")
    assert await stream.result() == "value"
    assert await stream.collect() == [1]


async def test_sync_send_full_is_explicit_not_silent_drop():
    stream = AgentEventStream[int](max_buffered_events=1)
    stream.send_sync(1)
    with pytest.raises(asyncio.QueueFull):
        stream.send_sync(2)
    stream.end()
    assert await stream.collect() == [1]


async def test_agent_close_settles_full_buffer_without_consumer():
    cleaned = asyncio.Event()
    emitted = []

    def stream(*args):
        async def generate():
            message = AssistantMessage(content=(TextContent(text="partial"),))
            try:
                yield StartEvent(partial=message)
                while True:
                    emitted.append(1)
                    yield TextDeltaEvent(delta="x", partial=message)
            finally:
                cleaned.set()

        return EventStream.from_async_generator(generate())

    agent = Agent(stream, config=AgentConfig(model="offline/buffer", event_buffer_size=8))
    events = await agent.prompt("start")
    for _ in range(100):
        if events.buffered_events == 8:
            break
        await asyncio.sleep(0)
    assert events.buffered_events == 8
    assert len(emitted) < 10
    await asyncio.wait_for(agent.aclose(), 2)
    assert agent.closed and cleaned.is_set()
    buffered = await events.collect()
    assert len(buffered) == 9
    assert isinstance(buffered[-1], AgentEndEvent)
    assert await events.result() is None  # No new RunResult protocol.


@pytest.mark.parametrize("size", [0, -1, True, 1.5])
def test_high_level_buffer_requires_positive_integer(size):
    with pytest.raises(ValueError, match="event_buffer_size"):
        Agent(lambda *args: None, config=AgentConfig(event_buffer_size=size))
