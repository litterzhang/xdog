from __future__ import annotations

import pytest
from xdog.agent import Agent, AgentConfig, HandledInput, InputConsumedEvent, InputHandledEvent
from xdog.agent.extensions.input import decision
from xdog.ai.types import AssistantMessage, ImageContent, TextContent, UserMessage
from xdog.ai.utils.event_stream import EventStream


async def drain(agent, text):
    return [event async for event in await agent.prompt(text)]


async def test_handled_initial_input_never_calls_model_or_fabricates_history_and_preserves_images():
    calls = []

    async def handle(message, source, cancel):
        return HandledInput("Explicit extension feedback")

    agent = Agent(
        lambda *args: calls.append(args) or EventStream.empty(AssistantMessage()),
        config=AgentConfig(model="test/model"),
        input_transform=handle,
    )
    image = ImageContent(data="caller-owned-original", mime_type="image/png")
    events = [event async for event in await agent.prompt("handled request", images=[image])]
    assert not calls and not agent.state.messages and not agent.state.error
    receipt = next(event for event in events if isinstance(event, InputHandledEvent))
    assert receipt.feedback == "Explicit extension feedback" and receipt.original.content[1] is image
    assert agent.drain_handled_inputs() == [receipt] and not agent.drain_handled_inputs()


async def test_identified_handled_steer_and_follow_up_get_distinct_receipts_without_history_admission():
    calls = []

    async def handle(message, source, cancel):
        return message if source == "prompt" else HandledInput("Handled " + source)

    agent = Agent(
        lambda *args: calls.append(args) or EventStream.empty(AssistantMessage(content=(TextContent(text="done"),))),
        config=AgentConfig(model="test/model"),
        input_transform=handle,
    )
    agent.queue_input("steer-id", "handler steer", "steer")
    agent.queue_input("follow-id", "handler follow", "follow_up")
    events = await drain(agent, "ordinary initial input")
    assert len(calls) == 1
    assert [event.input_id for event in events if isinstance(event, InputHandledEvent)] == ["steer-id", "follow-id"]
    assert not any(isinstance(event, InputConsumedEvent) for event in events)
    assert not agent.pending_inputs() and not agent.drain_inputs()
    assert len([message for message in agent.state.messages if isinstance(message, UserMessage)]) == 1


async def test_all_handled_initial_and_queued_input_does_not_call_model_with_old_history():
    calls = []

    async def handle(message, source, cancel):
        return HandledInput("Handled " + source)

    agent = Agent(
        lambda *args: calls.append(args) or EventStream.empty(AssistantMessage()),
        config=AgentConfig(model="test/model"),
        input_transform=handle,
    )
    agent.replace_messages(
        [UserMessage(content="old user"), AssistantMessage(content=(TextContent(text="old answer"),))]
    )
    agent.queue_input("follow", "handled queued request", "follow_up")
    events = await drain(agent, "handled new request")
    assert not calls and len(agent.state.messages) == 2
    assert len([event for event in events if isinstance(event, InputHandledEvent)]) == 2


def test_handled_requires_explicit_bounded_feedback_not_implicit_drop_or_command_execution() -> None:

    def _check_case(value):
        with pytest.raises(ValueError):
            decision(value)

    for value in [
        {"action": "handled"},
        {"action": "handled", "feedback": ""},
        {"action": "handled", "feedback": True},
        {"action": "handled", "feedback": "x", "images": []},
        {"action": "handled", "feedback": "x" * 65537},
    ]:
        _check_case(value)


async def test_empty_prompt_list_keeps_existing_explicit_continuation_behavior():
    calls = []
    agent = Agent(
        lambda *args: calls.append(args) or EventStream.empty(AssistantMessage(content=(TextContent(text="done"),))),
        config=AgentConfig(model="test/model"),
    )
    agent.replace_messages([UserMessage(content="existing accepted input")])
    events = await agent.prompt([])
    async for _event in events:
        pass
    assert (
        len(calls) == 1
    )  # No newly supplied input to intercept; caller explicitly requested old-context continuation.


async def test_handled_initial_replacement_preserves_one_at_a_time_steering_boundaries():
    from xdog.agent import QueueMode

    counts = []

    async def handle(message, source, cancel):
        return HandledInput("Initial handled") if source == "prompt" else message

    def stream(model, context, options):
        counts.append(len([message for message in context.messages if isinstance(message, UserMessage)]))
        return EventStream.empty(AssistantMessage(content=(TextContent(text="done"),)))

    agent = Agent(
        stream, config=AgentConfig(model="test/model", steering_mode=QueueMode.ONE_AT_A_TIME), input_transform=handle
    )
    agent.queue_input("one", "first steer", "steer")
    agent.queue_input("two", "second steer", "steer")
    await drain(agent, "handled initial")
    assert counts == [1, 2]


async def test_identified_handled_input_awaits_settlement_without_model_or_fake_history():
    calls, rows = [], []
    async def transform(message, source, cancel):
        return message if source == 'prompt' else HandledInput('handled')
    async def settled(pending):
        rows.append(pending)
        raise OSError('Owned handled journal failure')
    agent = Agent(lambda *args: calls.append(args) or EventStream.empty(AssistantMessage()),
                  config=AgentConfig(model='test/model'), input_transform=transform, input_settled=settled)
    agent.queue_input('handled', 'NEVER SEND TO MODEL', 'steer')
    await drain(agent, 'initial')
    assert rows == [()] and not calls and not agent.pending_inputs()
    assert 'handled journal failure' in agent.state.error
