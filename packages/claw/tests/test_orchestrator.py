"""Tests for orchestrator."""
import asyncio
from unittest.mock import AsyncMock

import pytest
from xdog.ai.types import AssistantMessage, DoneEvent, Model, ModelCost, StartEvent, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.channels.tui.channel import TuiChannel
from xdog.claw.config import ClawConfig
from xdog.claw.core.runtime.orchestrator import Orchestrator
from xdog.claw.core.types import Group, GroupConfig, QueueMode, UserInput

_TEST_MODEL = Model(
    id="test/dummy", name="dummy", api="openai-completions",
    provider="test", context_window=200_000, max_tokens=16_384,
    cost=ModelCost(),
)

def _make_mock_stream_fn(response_text="I am the assistant."):
    def stream_fn(model_id, context, options=None):
        msg = AssistantMessage(content=(TextContent(text=response_text),))

        async def _gen():
            yield StartEvent(partial=msg)
            yield DoneEvent(message=msg)

        fut = asyncio.get_running_loop().create_future()
        fut.set_result(msg)
        return EventStream.from_async_generator(_gen(), result_future=fut)

    return stream_fn

@pytest.fixture
def orch(tmp_path):
    config = ClawConfig(data_dir=str(tmp_path / "data"))
    o = Orchestrator(config, model=_TEST_MODEL, stream_fn=_make_mock_stream_fn(), data_dir=tmp_path / "data")
    o.register_group(Group(id="main", name="Main", is_main=True))
    return o

@pytest.mark.asyncio
async def test_handle_message_returns_response(orch):
    result = await orch.route_message(UserInput(group_id="main", content="hello", sender="user"))
    assert result is not None
    assert result.response_text


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


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_after_progress", [False, True])
async def test_channel_delivers_progress_before_turn_finishes(tmp_path, fail_after_progress):
    path = tmp_path / "input.txt"
    path.write_text("tool output should not be sent")
    release_final = asyncio.Event()
    progress_sent = asyncio.Event()
    final = (
        AssistantMessage(
            content=(TextContent(text="incomplete answer"),),
            stop_reason="error", error_message="model failed",
        )
        if fail_after_progress else AssistantMessage(content=(TextContent(text="Done."),))
    )

    async def before_message(index):
        if index == 1:
            await release_final.wait()

    stream_fn = _scripted_stream_fn([_progress_message("Checking.", path, "c1"), final], before_message)
    config = ClawConfig(data_dir=str(tmp_path / "data"))
    orch = Orchestrator(config, model=_TEST_MODEL, stream_fn=stream_fn)
    orch.register_group(Group(id="main", name="Main"))
    channel = TuiChannel()
    sent = []

    async def capture_send(group_id, text):
        sent.append(text)
        progress_sent.set()

    channel.send_message = capture_send
    orch.add_channel(channel)
    task = asyncio.create_task(channel.simulate_input("main", "Read the file"))
    try:
        await asyncio.wait_for(progress_sent.wait(), timeout=2)
        assert not task.done()
        assert sent == ["Checking."]
    finally:
        release_final.set()
        await asyncio.wait_for(task, timeout=2)

    assert sent == ["Checking.", "Error: model failed" if fail_after_progress else "Done."]


@pytest.mark.asyncio
async def test_queued_channel_turns_deliver_messages_once_in_order(tmp_path):
    path = tmp_path / "input.txt"
    path.write_text("tool output")
    started = asyncio.Event()
    release = asyncio.Event()

    async def before_message(index):
        if index == 0:
            started.set()
            await release.wait()

    messages = [
        _progress_message("First progress.", path, "c1"),
        AssistantMessage(content=(TextContent(text="First done."),)),
        _progress_message("Second progress.", path, "c2"),
        AssistantMessage(content=(TextContent(text="Second done."),)),
    ]
    config = ClawConfig(data_dir=str(tmp_path / "data"))
    orch = Orchestrator(config, model=_TEST_MODEL, stream_fn=_scripted_stream_fn(messages, before_message))
    orch.register_group(Group(id="main", name="Main"))
    channel = TuiChannel()
    channel.send_message = AsyncMock()
    orch.add_channel(channel)

    task = asyncio.create_task(channel.simulate_input("main", "first"))
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        await channel.simulate_input("main", "second")
    finally:
        release.set()
        await asyncio.wait_for(task, timeout=2)

    assert [call.args[1] for call in channel.send_message.await_args_list] == [
        "First progress.", "First done.", "Second progress.", "Second done.",
    ]


@pytest.mark.asyncio
async def test_direct_route_does_not_duplicate_tui_output_to_channels(orch):
    channel = TuiChannel()
    channel.send_message = AsyncMock()
    orch.add_channel(channel)

    result = await orch.route_message(UserInput(group_id="main", content="hello", sender="user"))

    assert result.response_text == "I am the assistant."
    channel.send_message.assert_not_awaited()

# ---------------------------------------------------------------------------
# Steer mode routing
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_steer_mode_calls_executor_steer(tmp_path):
    """When agent is running and STEER mode is configured, executor.steer() is called."""
    config = ClawConfig(data_dir=str(tmp_path / "data"))
    steer_config = GroupConfig(queue_mode=QueueMode.STEER)

    # Use a slow stream_fn so the agent is "running" when second message arrives
    call_count = [0]

    def slow_stream_fn(model_id, context, options=None):
        call_count[0] += 1
        msg = AssistantMessage(content=(TextContent(text="response"),))

        async def _gen():
            if call_count[0] == 1:
                # First call: simulate slow processing
                await asyncio.sleep(0.1)
            yield StartEvent(partial=msg)
            yield DoneEvent(message=msg)

        fut = asyncio.get_running_loop().create_future()
        fut.set_result(msg)
        return EventStream.from_async_generator(_gen(), result_future=fut)

    o = Orchestrator(config, model=_TEST_MODEL, stream_fn=slow_stream_fn, data_dir=tmp_path / "data")
    o.register_group(Group(id="steer_group", name="SteerGroup", config=steer_config))

    sent = []
    ch = TuiChannel()

    async def capture_send(group_id, text):
        sent.append(text)

    ch.send_message = capture_send
    o.add_channel(ch)

    steered = []
    runtime = o._runtimes["steer_group"]
    original_steer = runtime.steer
    def track_steer(content):
        steered.append(content)
        return original_steer(content)
    runtime.steer = track_steer

    # Send first message (starts the agent turn)
    task1 = asyncio.create_task(
        o.route_message(UserInput(group_id="steer_group", content="first", sender="user"))
    )
    await asyncio.sleep(0.02)  # let the first message start processing

    # Send second message while agent is running — should trigger steer
    await o.route_message(UserInput(group_id="steer_group", content="urgent", sender="user"))
    await task1

    assert len(steered) == 1
    assert steered[0] == "urgent"

    await o.stop()
