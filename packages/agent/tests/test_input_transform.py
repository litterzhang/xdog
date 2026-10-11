from __future__ import annotations

import asyncio
from dataclasses import replace

from xdog.agent import Agent, AgentConfig, InputConsumedEvent, InputRejectedEvent, InputTransformedEvent
from xdog.ai.types import AssistantMessage, TextContent, UserMessage
from xdog.ai.utils.event_stream import EventStream


async def drain(agent, text):
    events = await agent.prompt(text)
    return [event async for event in events]


async def test_initial_and_identified_steer_follow_up_are_transformed_once_before_admission():
    seen, contexts = [], []

    async def transform(message, source, cancel):
        text = message.content if isinstance(message.content, str) else "\n".join(part.text for part in message.content)
        seen.append((text, source))
        return replace(message, content="filtered-" + text)

    def stream(model, context, options):
        contexts.append(context)
        return EventStream.empty(AssistantMessage(content=(TextContent(text="done"),)))

    agent = Agent(stream, config=AgentConfig(model="test/model"), input_transform=transform)
    agent.queue_input("steer", "steered", "steer")
    agent.queue_input("later", "follow", "follow_up")
    events = await drain(agent, "initial")
    assert seen == [("initial", "prompt"), ("steered", "steer"), ("follow", "follow_up")]
    assert len([event for event in events if isinstance(event, InputTransformedEvent)]) == 3
    consumed = [event for event in events if isinstance(event, InputConsumedEvent)]
    assert [event.input.id for event in consumed] == ["steer", "later"]
    assert [event.input.content for event in consumed] == ["steered", "follow"]  # Receipt keeps recovery identity.
    assert not agent.pending_inputs()
    assert [message.content for message in agent.state.messages if isinstance(message, UserMessage)] == [
        "filtered-initial",
        "filtered-steered",
        "filtered-follow",
    ]


async def test_rejected_selected_input_is_not_consumed_and_caller_can_recover_original_without_replay():
    contexts = []

    async def transform(message, source, cancel):
        if source == "steer":
            raise ValueError("Rejected before admission")
        return message

    agent = Agent(
        lambda *args: contexts.append(args) or EventStream.empty(AssistantMessage()),
        config=AgentConfig(model="test/model"),
        input_transform=transform,
    )
    agent.queue_input("recover", "original", "steer")
    events = await drain(agent, "initial")
    assert not contexts and not any(isinstance(event, InputConsumedEvent) for event in events)
    assert any(isinstance(event, InputRejectedEvent) and event.input_id == "recover" for event in events)
    recovered = agent.drain_inputs()
    assert recovered[0].content == "original" and recovered[0].id == "recover"


async def test_abort_settles_pre_admission_hook_before_idle_and_recovers_original():
    entered, settled = asyncio.Event(), asyncio.Event()
    calls = []

    async def transform(message, source, cancel):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            settled.set()

    agent = Agent(
        lambda *args: calls.append(args) or EventStream.empty(AssistantMessage()),
        config=AgentConfig(model="test/model"),
        input_transform=transform,
    )
    stream = await agent.prompt("initial")

    async def consume():
        return [event async for event in stream]

    task = asyncio.create_task(consume())
    await entered.wait()
    agent.abort()
    events = await asyncio.wait_for(task, 3)
    assert settled.is_set() and not agent.is_streaming and not calls
    assert any(isinstance(event, InputRejectedEvent) and event.original.content for event in events)
    assert not any(isinstance(message, UserMessage) for message in agent.state.messages)


async def test_plain_continue_queue_preserves_delivery_and_original_queue_on_rejection():
    seen = []

    async def transform(message, source, cancel):
        seen.append(source)
        if source == "follow_up":
            raise ValueError("Refused plain queued input")
        return message

    agent = Agent(
        lambda *args: EventStream.empty(AssistantMessage(content=(TextContent(text="done"),))),
        config=AgentConfig(model="test/model"),
        input_transform=transform,
    )
    await drain(agent, "initial")
    agent.follow_up("recover plain followup")
    stream = await agent.continue_()
    events = [event async for event in stream]
    assert seen == ["prompt", "follow_up"]
    assert not agent._follow_up_queue
    recovered = agent.drain_rejected_messages()
    assert recovered[0].content == "recover plain followup"
    assert any(isinstance(event, InputRejectedEvent) and event.source == "follow_up" for event in events)
    assert not any("recover plain" in str(message.content) for message in agent.state.messages)
    await drain(agent, "new unrelated input")
    assert seen == ["prompt", "follow_up", "prompt"]
    assert not agent.drain_rejected_messages()  # Explicit recovery consumed its one quarantine record.



async def test_identified_input_images_are_preserved_through_transformed_receipt_and_history():
    from xdog.ai.types import ImageContent

    image = ImageContent(data="typed-image-owned-by-caller", mime_type="image/png")

    async def transform(message, source, cancel):
        parts = message.content if not isinstance(message.content, str) else (TextContent(text=message.content),)
        return replace(
            message,
            content=(
                TextContent(text="transformed queue"),
                *[part for part in parts if isinstance(part, ImageContent)],
            ),
        )

    agent = Agent(
        lambda *args: EventStream.empty(AssistantMessage(content=(TextContent(text="done"),))),
        config=AgentConfig(model="test/model"),
        input_transform=transform,
    )
    agent.queue_input("image-follow", "original queue", "follow_up", images=(image,))
    events = await drain(agent, "initial")
    receipt = next(event for event in events if isinstance(event, InputConsumedEvent))
    assert receipt.input.id == "image-follow" and receipt.input.images == (image,)
    users = [message for message in agent.state.messages if isinstance(message, UserMessage)]
    assert users[-1].content == (TextContent(text="transformed queue"), image)
