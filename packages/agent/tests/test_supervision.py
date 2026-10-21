from __future__ import annotations

import asyncio

import pytest

from test_support.child_runtime.lifecycle import factory


async def test_stream_adapter_cannot_change_during_a_turn():
    agent = factory(None, None)
    original = agent.stream_fn
    stream = await agent.prompt("task")
    with pytest.raises(RuntimeError, match="active turn"):
        agent.set_stream_fn(original)
    async for _ in stream:
        pass
    agent.set_stream_fn(original)


async def test_immediate_producer_cancellation_always_releases_idle_state():
    agent = factory(None, None)
    await agent.prompt("task")
    async with asyncio.timeout(3):
        await agent.cancel_and_wait()
        await agent.wait_for_idle()
    assert not agent.is_streaming
