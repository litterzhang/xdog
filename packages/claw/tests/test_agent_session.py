"""Tests for AgentSession — agent turn execution, persistence, tools."""
import asyncio
from unittest.mock import AsyncMock

import pytest
from xdog.agent import MessageEndEvent
from xdog.agent.tools import create_filesystem_tool
from xdog.ai.types import (
    AssistantMessage,
    DoneEvent,
    StartEvent,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
    Usage,
    UserMessage,
)
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.core.persistence.transcript_store import TranscriptStore
from xdog.claw.core.prompt import init_workspace
from xdog.claw.core.runtime.group import GroupRuntime
from xdog.claw.core.types import Group, UserInput

# ---------------------------------------------------------------------------
# Mock stream_fn helpers
# ---------------------------------------------------------------------------

def make_stream_fn(response_text="", tool_calls=None):
    def stream_fn(model_id, context, options=None):
        parts = []
        if response_text:
            parts.append(TextContent(text=response_text))
        if tool_calls:
            for tc in tool_calls:
                parts.append(ToolCall(
                    id=tc["id"], name=tc["name"],
                    arguments=tc.get("arguments", {}),
                ))
        msg = AssistantMessage(content=tuple(parts))

        async def _gen():
            yield StartEvent(partial=msg)
            yield DoneEvent(message=msg)

        fut = asyncio.get_running_loop().create_future()
        fut.set_result(msg)
        return EventStream.from_async_generator(_gen(), result_future=fut)

    return stream_fn

def make_multi_round_stream_fn(rounds):
    call_count = [0]

    def stream_fn(model_id, context, options=None):
        idx = min(call_count[0], len(rounds) - 1)
        call_count[0] += 1
        text, tcs = rounds[idx]
        parts = []
        if text:
            parts.append(TextContent(text=text))
        if tcs:
            for tc in tcs:
                parts.append(ToolCall(
                    id=tc["id"], name=tc["name"],
                    arguments=tc.get("arguments", {}),
                ))
        msg = AssistantMessage(content=tuple(parts))

        async def _gen():
            yield StartEvent(partial=msg)
            yield DoneEvent(message=msg)

        fut = asyncio.get_running_loop().create_future()
        fut.set_result(msg)
        return EventStream.from_async_generator(_gen(), result_future=fut)

    stream_fn._call_count = call_count
    return stream_fn

def make_capturing_stream_fn(response_text="ok"):
    captured = {"model_id": None, "context": None, "options": None}

    def stream_fn(model_id, context, options=None):
        captured["model_id"] = model_id
        captured["context"] = context
        captured["options"] = options
        if hasattr(context, 'system_prompt'):
            captured["system_prompt"] = context.system_prompt
        msg = AssistantMessage(content=(TextContent(text=response_text),))

        async def _gen():
            yield StartEvent(partial=msg)
            yield DoneEvent(message=msg)

        fut = asyncio.get_running_loop().create_future()
        fut.set_result(msg)
        return EventStream.from_async_generator(_gen(), result_future=fut)

    stream_fn.captured = captured
    return stream_fn

def make_failing_stream_fn(error_msg="LLM error"):
    def stream_fn(model_id, context, options=None):
        async def _gen():
            raise RuntimeError(error_msg)
            yield  # noqa: E501

        fut = asyncio.get_running_loop().create_future()
        return EventStream.from_async_generator(_gen(), result_future=fut)

    return stream_fn

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_runtime(ws, tmp_path, *, stream_fn=None, tools=None, group_id="g1"):
    """Create a GroupRuntime for testing."""
    store = TranscriptStore(tmp_path / "sessions")
    return GroupRuntime(
        group=Group(id=group_id, name=group_id),
        data_dir=tmp_path,
        model="test/dummy",
        stream_fn=stream_fn,
        workspace_dir=ws,
        transcript_store=store,
    )

def _make_session(ws, tmp_path, *, stream_fn=None, tools=None, group_id="g1"):
    """Create an AgentSession via GroupRuntime."""
    runtime = _make_runtime(ws, tmp_path, stream_fn=stream_fn, tools=tools, group_id=group_id)
    # Override tools if provided
    if tools:
        runtime._tools = tools
    return runtime.get_or_create_session()

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def setup(tmp_path):
    ws = tmp_path / "workspace"
    init_workspace(ws, agent_name="TestBot")
    return ws, tmp_path

# ---------------------------------------------------------------------------
# Core turn lifecycle
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_turn_returns_response(setup):
    ws, tmp_path = setup
    session = _make_session(ws, tmp_path, stream_fn=make_stream_fn("Hello! I'm TestBot."))
    result = await session.run_turn(UserInput(group_id="g1", content="hi", sender="user"))
    assert result.response_text == "Hello! I'm TestBot."
    assert result.error is None


@pytest.mark.asyncio
async def test_cancelled_turn_aborts_agent_producer_and_persists(setup):
    ws, tmp_path = setup
    started = asyncio.Event()
    stopped = asyncio.Event()

    def stream_fn(model_id, context, options=None):
        msg = AssistantMessage(stop_reason="aborted")

        async def events():
            started.set()
            await options.cancel.wait()
            stopped.set()
            yield DoneEvent(message=msg)

        future = asyncio.get_running_loop().create_future()
        future.set_result(msg)
        return EventStream.from_async_generator(events(), result_future=future)

    session = _make_session(ws, tmp_path, stream_fn=stream_fn)
    task = asyncio.create_task(session.run_turn(UserInput(group_id="g1", content="hello", sender="user")))
    await asyncio.wait_for(started.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=2)

    assert stopped.is_set()
    assert not session.agent.state.is_streaming
    transcript = session._store.load_transcript(session.meta.session_id)
    assert transcript[0]["role"] == "user"


@pytest.mark.asyncio
async def test_message_delivery_only_includes_completed_assistant_text(setup):
    ws, tmp_path = setup
    session = _make_session(ws, tmp_path, stream_fn=make_stream_fn())
    messages = [
        UserMessage(content="user input"),
        AssistantMessage(content=(ThinkingContent(thinking="private reasoning"),)),
        AssistantMessage(content=(ToolCall(id="c1", name="filesystem", arguments={}),)),
        ToolResultMessage(tool_call_id="c1", tool_name="filesystem", content=(TextContent(text="tool output"),)),
        AssistantMessage(content=(TextContent(text=" \n"),)),
        AssistantMessage(content=(TextContent(text="failed partial"),), stop_reason="error"),
        AssistantMessage(content=(TextContent(text="aborted partial"),), stop_reason="aborted"),
        AssistantMessage(
            content=(
                ThinkingContent(thinking="more private reasoning"),
                TextContent(text="Checking."),
                TextContent(text="Please wait."),
                ToolCall(id="c2", name="filesystem", arguments={}),
            ),
            usage=Usage(input=10, output=5, total_tokens=15),
        ),
        AssistantMessage(
            content=(TextContent(text="Done."),),
            usage=Usage(input=20, output=5, total_tokens=25),
        ),
    ]

    async def events():
        for message in messages:
            yield MessageEndEvent(message=message)

    deliver = AsyncMock()
    result = await session._drain_events(events(), on_assistant_message=deliver)

    assert [call.args[0] for call in deliver.await_args_list] == ["Checking.\n\nPlease wait.", "Done."]
    assert result.usage == {"input": 30, "output": 10, "cache_read": 0, "cache_write": 0}


@pytest.mark.asyncio
async def test_run_turn_persists_transcript(setup):
    ws, tmp_path = setup
    runtime = _make_runtime(ws, tmp_path, stream_fn=make_stream_fn("response"))
    session = runtime.get_or_create_session()
    await session.run_turn(UserInput(group_id="g1", content="hello", sender="user"))
    meta = runtime.transcript_store.get_active_session("g1")
    transcript = runtime.transcript_store.load_transcript(meta.session_id)
    assert len(transcript) == 2  # user + assistant
    assert transcript[0]["role"] == "user"
    assert transcript[1]["role"] == "assistant"

@pytest.mark.asyncio
async def test_run_turn_includes_system_prompt(setup):
    ws, tmp_path = setup
    sfn = make_capturing_stream_fn("ok")
    session = _make_session(ws, tmp_path, stream_fn=sfn)
    await session.run_turn(UserInput(group_id="g1", content="hi", sender="user"))
    from xdog.ai.types import system_prompt_text
    prompt_text = system_prompt_text(sfn.captured["system_prompt"]) or ""
    assert "TestBot" in prompt_text

# ---------------------------------------------------------------------------
# Tool call loop
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tool_call_loop(setup):
    """Agent requests tool -> executes -> feeds result -> Agent responds."""
    ws, tmp_path = setup
    (ws / "hello.txt").write_text("Hello from file!")

    sfn = make_multi_round_stream_fn([
        ("", [{"id": "call_1", "name": "filesystem", "arguments": {"action": "read", "path": str(ws / "hello.txt")}}]),
        ("The file says: Hello from file!", None),
    ])

    tools = [create_filesystem_tool()]
    session = _make_session(ws, tmp_path, stream_fn=sfn, tools=tools)
    result = await session.run_turn(UserInput(group_id="g1", content="read hello.txt", sender="user"))

    assert result.error is None
    assert "Hello from file!" in result.response_text
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0]["name"] == "filesystem"


@pytest.mark.asyncio
async def test_tool_display_events_are_structured_and_lossless(setup):
    from xdog.claw.core.runtime.display_events import ToolFinished, ToolStarted

    ws, tmp_path = setup
    full_result = "result:" + "x" * 700
    tool = create_filesystem_tool()

    async def execute(tool_call_id, params, cancel, on_update, *, ctx=None):
        from xdog.agent import AgentToolResult
        return AgentToolResult(content=(TextContent(text=full_result),))

    tool = type(tool)(
        name=tool.name,
        description=tool.description,
        parameters=tool.parameters,
        execute=execute,
    )
    stream_fn = make_multi_round_stream_fn([
        ("", [{"id": "call-1", "name": "filesystem", "arguments": {"action": "read", "path": "x"}}]),
        ("done", None),
    ])
    session = _make_session(ws, tmp_path, stream_fn=stream_fn, tools=[tool])
    events = []

    await session.run_turn(
        UserInput(group_id="g1", content="read", sender="user"),
        on_display_event=events.append,
    )

    started = next(event for event in events if isinstance(event, ToolStarted))
    finished = next(event for event in events if isinstance(event, ToolFinished))
    assert started.tool_call_id == "call-1"
    assert finished.tool_call_id == "call-1"
    assert finished.result == full_result


def test_tool_display_wire_redacts_sensitive_arguments() -> None:
    from xdog.claw.core.runtime.display_events import ToolStarted

    event = ToolStarted(
        tool_call_id="call-1",
        name="bash",
        arguments={
            "command": "curl example",
            "authorization": "Bearer secret",
            "nested": {"api_key": "secret", "safe": "visible"},
        },
    )

    wire = event.to_wire()
    assert wire["arguments"]["authorization"] == "<redacted>"
    assert wire["arguments"]["nested"]["api_key"] == "<redacted>"
    assert wire["arguments"]["nested"]["safe"] == "visible"


def test_tool_display_wire_redacts_inline_result_secrets() -> None:
    from xdog.claw.core.runtime.display_events import ToolFinished

    wire = ToolFinished(
        tool_call_id="call-1",
        name="bash",
        result="Authorization: Bearer abc123\napi_key=xyz987\nvisible",
        is_error=False,
    ).to_wire()

    assert "abc123" not in wire["result"]
    assert "xyz987" not in wire["result"]
    assert "visible" in wire["result"]

@pytest.mark.asyncio
async def test_write_file_tool(setup):
    """write_file tool creates a file in workspace."""
    ws, tmp_path = setup

    sfn = make_multi_round_stream_fn([
        ("", [{"id": "c1", "name": "filesystem", "arguments": {"action": "write", "path": str(ws / "output.txt"), "content": "written by agent"}}]),
        ("File written successfully.", None),
    ])

    tools = [create_filesystem_tool()]
    session = _make_session(ws, tmp_path, stream_fn=sfn, tools=tools)
    result = await session.run_turn(UserInput(group_id="g1", content="write file", sender="user"))

    assert result.error is None
    assert (ws / "output.txt").read_text() == "written by agent"

# ---------------------------------------------------------------------------
# _extract_previous_summary
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# extract_final_text
# ---------------------------------------------------------------------------
