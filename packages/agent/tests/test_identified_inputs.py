from __future__ import annotations

import asyncio

import pytest
from xdog.agent import Agent, AgentConfig, InputConsumedEvent, QueueMode
from xdog.ai.types import AssistantMessage, DoneEvent, TextContent, UserMessage
from xdog.ai.utils.event_stream import EventStream


def done_stream(model, context, options=None):
    message = AssistantMessage(content=(TextContent(text="answer"),))

    async def generate():
        yield DoneEvent(message=message)

    future = asyncio.get_running_loop().create_future()
    future.set_result(message)
    return EventStream.from_async_generator(generate(), result_future=future)


def test_identified_queue_edit_remove_move_preserves_duplicate_message_identity() -> None:
    agent = Agent(done_stream, config=AgentConfig(model="test/dummy"))
    agent.queue_input("one", "same", "steer")
    agent.queue_input("two", "same", "steer")
    agent.queue_input("three", "later", "follow_up")
    assert agent.change_input("two", "edit", "edited")
    assert [item.content for item in agent.pending_inputs()] == ["same", "edited", "later"]
    assert agent.change_input("two", "move", direction=-1)
    assert [item.id for item in agent.pending_inputs()] == ["two", "one", "three"]
    assert agent.change_input("one", "remove")
    assert not agent.change_input("unknown", "remove")
    assert not agent.change_input("two", "move", direction=-1)
    assert not agent.change_input("three", "edit", "")
    with pytest.raises(ValueError):
        agent.queue_input("two", "duplicate", "steer")


async def test_receipt_requires_history_admission_and_selected_messages_cannot_be_edited() -> None:
    agent = Agent(done_stream, config=AgentConfig(model="test/dummy"))
    agent.queue_input("one", "steering", "steer")
    selected = agent._dequeue_steering()
    assert agent.pending_inputs()[0].selected
    assert not agent.change_input("one", "edit", "too late")
    assert not agent.change_input("one", "remove")
    # Abort before admission can still return the selected original content.
    assert agent.drain_inputs()[0].content == "steering"
    assert not agent.pending_inputs()
    assert selected[0].content == "steering"

    agent.queue_input("two", "steering", "steer")
    stream = await agent.prompt("original")
    receipts = [event async for event in stream if isinstance(event, InputConsumedEvent)]
    assert [event.input.id for event in receipts] == ["two"]
    assert any(isinstance(message, UserMessage) and message.content == "steering" for message in agent.state.messages)
    assert not agent.pending_inputs()
    assert not agent.drain_inputs()


async def test_native_follow_up_admitted_after_steering_in_the_same_agent_loop() -> None:
    agent = Agent(done_stream, config=AgentConfig(model="test/dummy", steering_mode=QueueMode.ONE_AT_A_TIME))
    agent.queue_input("steer-1", "one", "steer")
    agent.queue_input("steer-2", "two", "steer")
    agent.queue_input("follow", "three", "follow_up")
    stream = await agent.prompt("original")
    receipts = [event.input.id async for event in stream if isinstance(event, InputConsumedEvent)]
    assert receipts == ["steer-1", "steer-2", "follow"]
    users = [message.content if isinstance(message.content, str) else "".join(part.text for part in message.content)
             for message in agent.state.messages if isinstance(message, UserMessage)]
    assert users == ["original", "one", "two", "three"]
    assert not agent.has_queued_messages()


def test_scoped_drain_does_not_remove_other_inputs_or_legacy_messages() -> None:
    agent = Agent(done_stream, config=AgentConfig(model="test/dummy"))
    agent.steer("legacy")
    agent.queue_input("owned:one", "one", "steer")
    agent.queue_input("other:two", "two", "follow_up")
    assert [item.id for item in agent.drain_inputs("owned:")] == ["owned:one"]
    assert [item.id for item in agent.pending_inputs()] == ["other:two"]
    assert agent._steering_queue[0].content == "legacy"
    agent.clear_follow_up_queue()
    assert not agent.pending_inputs()
    agent.queue_input("three", "three", "steer")
    agent.reset()
    assert not agent.pending_inputs()
    assert not agent.has_queued_messages()


async def test_input_settlement_is_awaited_before_next_model_request_and_failure_stops_continuation():
    calls, settlements = [], []
    entered, release = asyncio.Event(), asyncio.Event()
    def stream(*args):
        calls.append(args)
        return done_stream(*args)
    async def settled(rows):
        settlements.append(rows)
        entered.set()
        await release.wait()
        raise OSError('Owned admission journal failure')
    agent = Agent(stream, config=AgentConfig(model='test/dummy'), input_settled=settled)
    agent.queue_input('identified', 'QUEUED ADMITTED INPUT', 'follow_up')
    output = await agent.prompt('initial')
    collecting = asyncio.create_task(output.collect())
    try:
        await asyncio.wait_for(entered.wait(), 3)
        assert len(calls) == 1 and not agent.pending_inputs()
        assert any(isinstance(message, UserMessage) and message.content == 'QUEUED ADMITTED INPUT'
                   for message in agent.messages)
        assert settlements == [()]
        release.set()
        await collecting
        assert len(calls) == 1 and 'admission journal failure' in agent.state.error
    finally:
        release.set()
        await agent.cancel_and_wait()
        await asyncio.gather(collecting, return_exceptions=True)
