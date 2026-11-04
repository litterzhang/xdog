"""Public loop producers have explicit terminal failure and cancellation ownership."""
from __future__ import annotations

import asyncio

import pytest
from xdog.agent.agent_loop import agent_loop, agent_loop_continue
from xdog.agent.core import AgentContext
from xdog.agent.event_stream import AgentEventStream
from xdog.agent.types import AgentLoopConfig
from xdog.ai.types import StreamOptions, UserMessage


@pytest.mark.parametrize('continuation', [False, True])
async def test_public_loop_hook_failure_reaches_iteration_and_result_without_orphan(continuation):
    errors = []
    loop = asyncio.get_running_loop()
    previous = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, event: errors.append(event))
    failure = RuntimeError('host hook failed')
    async def broken():
        raise failure
    def no_model(*args):
        raise AssertionError('No model request')
    try:
        context = AgentContext(messages=[UserMessage(content='input')])
        config = AgentLoopConfig(get_steering_messages=broken)
        stream = (agent_loop_continue(context, config, no_model, 'offline', StreamOptions()) if continuation
                  else agent_loop([], context, config, no_model, 'offline', StreamOptions()))
        with pytest.raises(RuntimeError) as iteration:
            await stream.collect()
        with pytest.raises(RuntimeError) as result:
            await stream.result()
        assert iteration.value is result.value is failure
        await stream.aclose()
        await asyncio.sleep(0)
        assert errors == []
    finally:
        loop.set_exception_handler(previous)


async def test_stream_close_cancels_and_joins_actual_producer_cleanup():
    stream = AgentEventStream[str]()
    entered, cleaned = asyncio.Event(), asyncio.Event()
    async def produce():
        try:
            entered.set()
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            cleaned.set()
    stream.bind_producer(asyncio.create_task(produce()))
    await entered.wait()
    await stream.aclose()
    assert cleaned.is_set() and stream.closed
    with pytest.raises(asyncio.CancelledError):
        await stream.result()
    with pytest.raises(asyncio.CancelledError):
        await stream.collect()


async def test_prestart_close_and_cancelled_result_waiter_do_not_hang_or_cancel_shared_result():
    stream = AgentEventStream[str]()
    stream.bind_producer(asyncio.create_task(asyncio.sleep(100)))
    await stream.aclose()
    with pytest.raises(asyncio.CancelledError):
        await stream.result()
    shared = AgentEventStream[str]()
    waiter = asyncio.create_task(shared.result())
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    shared.end('actual result')
    assert await shared.result() == 'actual result'


async def test_bounded_close_does_not_deadlock_and_keeps_already_buffered_events():
    stream = AgentEventStream[str](max_buffered_events=1)
    waiting = asyncio.Event()
    async def produce():
        await stream.send('first')
        waiting.set()
        await stream.send('blocked')
    stream.bind_producer(asyncio.create_task(produce()))
    await waiting.wait()
    await stream.aclose()
    iterator = stream.__aiter__()
    assert await anext(iterator) == 'first'
    with pytest.raises(asyncio.CancelledError):
        await anext(iterator)


async def test_close_reports_actual_cleanup_failure_after_joining():
    stream = AgentEventStream[str]()
    entered, finish = asyncio.Event(), asyncio.Event()
    failure = RuntimeError('cleanup failed')
    async def produce():
        try:
            entered.set()
            await asyncio.Event().wait()
        finally:
            await finish.wait()
            raise failure
    stream.bind_producer(asyncio.create_task(produce()))
    await entered.wait()
    closing = asyncio.create_task(stream.aclose())
    await asyncio.sleep(0)
    closing.cancel()
    await asyncio.sleep(0)
    assert not closing.done()
    finish.set()
    with pytest.raises(RuntimeError) as error:
        await closing
    assert error.value is failure
    with pytest.raises(RuntimeError):
        await stream.result()


async def test_close_retrieves_settled_producer_before_its_queued_done_callback():
    stream = AgentEventStream[str]()
    producer = asyncio.create_task(asyncio.sleep(100))
    stream.bind_producer(producer)
    producer.cancel()
    await asyncio.sleep(0)  # Task is done, its callbacks may still be queued.
    await stream.aclose()
    with pytest.raises(asyncio.CancelledError):
        await stream.result()


async def test_agent_idle_requires_producer_settlement_not_only_agent_end():
    from xdog.agent import Agent, AgentConfig, AgentEndEvent
    from xdog.ai.types import AssistantMessage, TextContent
    from xdog.ai.utils.event_stream import EventStream
    agent = Agent(lambda *_: EventStream.empty(AssistantMessage(content=(TextContent(text="done"),))),
                  config=AgentConfig(model="test"))
    seen = []
    agent.subscribe(lambda event: seen.append(agent.is_idle) if isinstance(event, AgentEndEvent) else None)
    assert agent.is_idle
    stream = await agent.prompt("test")
    async for _ in stream:
        pass
    await agent.wait_for_idle()
    await asyncio.sleep(0)
    assert seen == [False]
    assert agent.is_idle
    await agent.aclose()
    assert not agent.is_idle
