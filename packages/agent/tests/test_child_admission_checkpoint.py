"""UI-046: prospective history/feedback before visible receipts; crash is never replay."""

from __future__ import annotations

import asyncio

import pytest
from xdog.agent import Agent, AgentConfig, HandledInput, InputConsumedEvent, InputHandledEvent
from xdog.ai.types import AssistantMessage, UserMessage
from xdog.ai.utils.event_stream import EventStream


@pytest.mark.parametrize("handled", [False, True])
@pytest.mark.parametrize("failure", [False, True])
async def test_checkpoint_precedes_public_receipt_and_failure_refuses_admission(handled, failure):
    entered, release = asyncio.Event(), asyncio.Event()
    calls, events = [], []

    async def transform(message, source, cancel):
        return HandledInput("HANDLED FEEDBACK") if handled else message

    async def checkpoint(event):
        if entered.is_set():
            return
        assert not any(isinstance(e, (InputConsumedEvent, InputHandledEvent)) for e in events)
        assert not agent.messages and not calls
        entered.set()
        await release.wait()
        if failure:
            raise OSError("Owned admission checkpoint refused")

    agent = Agent(
        lambda *args: calls.append(args) or EventStream.empty(AssistantMessage()),
        config=AgentConfig(model="offline/model"),
        input_transform=transform,
    )
    agent.set_input_checkpoint(checkpoint)
    agent.subscribe(events.append)
    agent.queue_input("original-id", "ORIGINAL INPUT", "steer")
    stream = await agent.prompt("INITIAL")
    work = asyncio.create_task(stream.collect())
    try:
        await asyncio.wait_for(entered.wait(), 3)
        assert not agent.messages and not calls
        release.set()
        await asyncio.wait_for(work, 3)
        if failure:
            assert not any(isinstance(e, (InputConsumedEvent, InputHandledEvent)) for e in events)
            assert not any(isinstance(m, UserMessage) for m in agent.messages)
            assert "checkpoint refused" in agent.state.error and not calls
            assert agent.pending_inputs()[0].content == "ORIGINAL INPUT"
        elif handled:
            assert not calls and not agent.messages
            assert len([e for e in events if isinstance(e, InputHandledEvent)]) == 2
        else:
            assert calls and any(isinstance(e, InputConsumedEvent) for e in events)
    finally:
        release.set()
        await agent.cancel_and_wait()
        await asyncio.gather(work, return_exceptions=True)
