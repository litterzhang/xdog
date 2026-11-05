"""Tests for orchestrator."""
import asyncio

import pytest
from xdog.ai.types import AssistantMessage, DoneEvent, Model, ModelCost, StartEvent, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.config import ClawConfig
from xdog.claw.core.runtime.orchestrator import Orchestrator
from xdog.claw.core.types import Group

_TEST_MODEL = Model(
    id="test/dummy", name="dummy", api="openai-completions",
    provider="test", context_window=200_000, max_tokens=16_384,
    cost=ModelCost(),
)

def _make_mock_stream_fn(response_text="I am the assistant."):
    def stream_fn(model_id, context, options=None):
        assert isinstance(model_id, str)
        msg = AssistantMessage(content=(TextContent(text=response_text),))

        async def _gen():
            yield StartEvent(partial=msg)
            yield DoneEvent(message=msg)

        fut = asyncio.get_running_loop().create_future()
        fut.set_result(msg)
        return EventStream.from_async_generator(_gen(), result_future=fut)

    return stream_fn


@pytest.fixture
async def orch(tmp_path):
    config = ClawConfig(data_dir=str(tmp_path / "data"))
    o = Orchestrator(config, model=_TEST_MODEL, stream_fn=_make_mock_stream_fn(), data_dir=tmp_path / "data")
    o.register_group(Group(id="main", name="Main", is_main=True))
    try:
        yield o
    finally:
        await o.stop()


def _scripted_stream_fn(messages, before_message):
    """Script model rounds with a gate to prove delivery before turn completion."""
    index = 0

    def stream_fn(model_id, context, options=None):
        nonlocal index
        current = index
        index += 1
        msg = messages[current]

        async def events():
            await before_message(current)
            yield StartEvent(partial=msg)
            yield DoneEvent(message=msg)

        future = asyncio.get_running_loop().create_future()
        future.set_result(msg)
        return EventStream.from_async_generator(events(), result_future=future)

    return stream_fn


def _progress_message(text, path, tool_id):
    return AssistantMessage(content=(
        TextContent(text=text),
        ToolCall(id=tool_id, name="filesystem", arguments={"action": "read", "path": str(path)}),
    ))


# ---------------------------------------------------------------------------
# Explicit Agent input control
# ---------------------------------------------------------------------------
