"""Tests for agent_loop — 1:1 port from agent-loop.test.ts.

Covers:
- Basic event emission with AgentMessage types
- Custom message type handling via convertToLlm
- transformContext applied before convertToLlm
- Tool calls and results with full event lifecycle
- Parallel tool execution with source-order result emission
- Steering message injection after tool calls complete
- agentLoopContinue: throws when empty
- agentLoopContinue: continues without user message events
- agentLoopContinue: custom message types as last message
"""

import asyncio
from typing import Any

import pytest
from xdog.agent import (
    AgentContext,
    AgentEvent,
    AgentLoopConfig,
    AgentMessage,
    AgentTool,
    AgentToolResult,
    MessageEndEvent,
    MessageStartEvent,
    StreamFn,
    ToolExecutionEndEvent,
)
from xdog.agent.agent_loop import agent_loop, agent_loop_continue
from xdog.ai.types import (
    AssistantMessage,
    CostBreakdown,
    DoneEvent,
    Message,
    StreamOptions,
    TextContent,
    ToolCall,
    ToolResultMessage,
    Usage,
    UserMessage,
)
from xdog.ai.utils.event_stream import EventStream as AiEventStream

# ---------------------------------------------------------------------------
# Test helpers (matching TS test helpers)
# ---------------------------------------------------------------------------

def create_usage() -> Usage:
    return Usage(0, 0, 0, 0, 0, CostBreakdown(0, 0, 0, 0, 0))

def create_assistant_message(
    content: tuple | list,
    stop_reason: str = "stop",
) -> AssistantMessage:
    if isinstance(content, list):
        content = tuple(content)
    return AssistantMessage(
        content=content,
        api="openai-completions",
        provider="copilot",
        model="mock",
        usage=create_usage(),
        stop_reason=stop_reason,
        timestamp=0,
    )

def create_user_message(text: str) -> UserMessage:
    return UserMessage(content=text)

def identity_converter(messages: list[AgentMessage]) -> list[Message]:
    """Simple identity converter — passes through standard messages."""
    return [
        m for m in messages
        if isinstance(m, (UserMessage, AssistantMessage, ToolResultMessage))
    ]

def create_mock_stream_fn(responses: list[AssistantMessage]) -> StreamFn:
    """Create a StreamFn that returns pre-built EventStream responses.

    Mimics the TS MockAssistantStream: each call returns a stream that
    pushes a ``DoneEvent`` on the next microtask.
    """
    call_index = [0]

    def fn(model_id: Any, context: Any, options: Any = None) -> AiEventStream[AssistantMessage]:
        idx = call_index[0]
        call_index[0] += 1
        msg = responses[idx] if idx < len(responses) else responses[-1]

        stream: AiEventStream[AssistantMessage] = AiEventStream()

        async def _push() -> None:
            await asyncio.sleep(0)  # yield to event loop (like queueMicrotask)
            stop = msg.stop_reason if msg.stop_reason in ("stop", "length", "toolUse") else "stop"
            await stream.send(DoneEvent(stop_reason=stop, message=msg))
            stream.set_result(msg)
            await stream.close()

        asyncio.ensure_future(_push())
        return stream

    return fn  # type: ignore[return-value]

# ---------------------------------------------------------------------------
# agentLoop tests
# ---------------------------------------------------------------------------

class TestAgentLoopWithAgentMessage:
    """Tests matching TS ``describe("agentLoop with AgentMessage", ...)``."""

    @pytest.mark.asyncio
    async def test_handle_custom_message_types_via_convert_to_llm(self) -> None:
        """Should handle custom message types via convertToLlm."""
        # Custom message type (notification)
        from dataclasses import dataclass, field
        from typing import Literal

        @dataclass(frozen=True)
        class CustomNotification:
            role: Literal["notification"] = field(default="notification", init=False)
            text: str = ""
            timestamp: float = 0.0

        notification = CustomNotification(text="This is a notification")

        context = AgentContext(
            system_prompt="You are helpful.",
            messages=[notification],  # type: ignore[list-item]
            tools=[],
        )

        user_prompt: AgentMessage = create_user_message("Hello")

        converted_messages: list[Message] = []

        def custom_converter(messages: list[AgentMessage]) -> list[Message]:
            nonlocal converted_messages
            # Filter out notifications, convert rest
            converted_messages = [
                m for m in messages
                if isinstance(m, (UserMessage, AssistantMessage, ToolResultMessage))
            ]
            return converted_messages

        config = AgentLoopConfig(
            convert_to_llm=custom_converter,
        )

        stream_fn = create_mock_stream_fn([
            create_assistant_message((TextContent(text="Response"),)),
        ])

        stream = agent_loop([user_prompt], context, config, stream_fn=stream_fn, model="mock/test-model", options=StreamOptions())
        async for _ in stream:
            pass

        # The notification should have been filtered out in convertToLlm
        assert len(converted_messages) == 1  # Only user message
        assert converted_messages[0].role == "user"

    @pytest.mark.asyncio
    async def test_apply_transform_context_before_convert_to_llm(self) -> None:
        """Should apply transformContext before convertToLlm."""
        context = AgentContext(
            system_prompt="You are helpful.",
            messages=[
                create_user_message("old message 1"),
                create_assistant_message((TextContent(text="old response 1"),)),
                create_user_message("old message 2"),
                create_assistant_message((TextContent(text="old response 2"),)),
            ],
            tools=[],
        )

        user_prompt: AgentMessage = create_user_message("new message")

        transformed_messages: list[AgentMessage] = []
        converted_messages: list[Message] = []

        async def transform(messages: list[AgentMessage]) -> list[AgentMessage]:
            nonlocal transformed_messages
            # Keep only last 2 messages (prune old ones)
            transformed_messages = messages[-2:]
            return transformed_messages

        def converter(messages: list[AgentMessage]) -> list[Message]:
            nonlocal converted_messages
            converted_messages = [
                m for m in messages
                if isinstance(m, (UserMessage, AssistantMessage, ToolResultMessage))
            ]
            return converted_messages

        config = AgentLoopConfig(
            transform_context=transform,
            convert_to_llm=converter,
        )

        stream_fn = create_mock_stream_fn([
            create_assistant_message((TextContent(text="Response"),)),
        ])

        stream = agent_loop([user_prompt], context, config, stream_fn=stream_fn, model="mock/test-model", options=StreamOptions())
        async for _ in stream:
            pass

        # transformContext should have been called first, keeping only last 2
        assert len(transformed_messages) == 2
        # Then convertToLlm receives the pruned messages
        assert len(converted_messages) == 2

    @pytest.mark.asyncio
    async def test_execute_tool_calls_in_parallel_emit_results_in_source_order(self) -> None:
        """Should execute tool calls in parallel and emit results in source order.

        Proves parallelism by having "first" block on an event that "second"
        sets — this can only succeed if both tools run concurrently.
        """
        first_resolved = False
        parallel_observed = False
        release_first: asyncio.Event = asyncio.Event()

        async def execute_echo(
            tool_call_id: str,
            params: dict[str, Any],
            cancel: Any = None,
            on_update: Any = None,
            **kwargs: Any,
        ) -> AgentToolResult:
            nonlocal first_resolved, parallel_observed

            if params["value"] == "first":
                # Block until "second" releases us (proves parallelism)
                await release_first.wait()
                first_resolved = True
            if params["value"] == "second":
                if not first_resolved:
                    parallel_observed = True
                # Release "first" — only works if both are running concurrently
                release_first.set()

            return AgentToolResult(
                content=(TextContent(text=f"echoed: {params['value']}"),),
                details={"value": params["value"]},
            )

        tool = AgentTool(
            name="echo",
            description="Echo tool",
            parameters={
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
            },
            label="Echo",
            execute=execute_echo,
        )

        context = AgentContext(
            system_prompt="",
            messages=[],
            tools=[tool],
        )

        user_prompt: AgentMessage = create_user_message("echo both")

        config = AgentLoopConfig(
            convert_to_llm=identity_converter,
            tool_execution="parallel",
        )

        call_index = [0]

        def stream_fn(model_id: Any, ctx: Any, options: Any = None) -> AiEventStream[AssistantMessage]:
            stream: AiEventStream[AssistantMessage] = AiEventStream()

            async def _push() -> None:
                await asyncio.sleep(0)
                if call_index[0] == 0:
                    msg = create_assistant_message(
                        (
                            ToolCall(id="tool-1", name="echo", arguments={"value": "first"}),
                            ToolCall(id="tool-2", name="echo", arguments={"value": "second"}),
                        ),
                        "toolUse",
                    )
                    await stream.send(DoneEvent(stop_reason="toolUse", message=msg))
                    stream.set_result(msg)
                else:
                    msg = create_assistant_message((TextContent(text="done"),))
                    await stream.send(DoneEvent(stop_reason="stop", message=msg))
                    stream.set_result(msg)
                call_index[0] += 1
                await stream.close()

            asyncio.ensure_future(_push())
            return stream

        events: list[AgentEvent] = []
        result_stream = agent_loop(
            [user_prompt], context, config, stream_fn=stream_fn, model="mock/test-model", options=StreamOptions(),  # type: ignore[arg-type]
        )

        async for event in result_stream:
            events.append(event)

        # Collect tool result message_end events with tool call IDs
        tool_result_ids: list[str] = []
        for event in events:
            if isinstance(event, MessageEndEvent):
                msg = event.message
                if isinstance(msg, ToolResultMessage):
                    tool_result_ids.append(msg.tool_call_id)

        assert parallel_observed is True
        assert tool_result_ids == ["tool-1", "tool-2"]

    @pytest.mark.asyncio
    async def test_inject_queued_messages_after_tool_calls_complete(self) -> None:
        """Should inject queued messages after all tool calls complete."""
        executed: list[str] = []

        async def execute_echo(
            tool_call_id: str,
            params: dict[str, Any],
            cancel: Any = None,
            on_update: Any = None,
            **kwargs: Any,
        ) -> AgentToolResult:
            executed.append(params["value"])
            return AgentToolResult(
                content=(TextContent(text=f"ok:{params['value']}"),),
                details={"value": params["value"]},
            )

        tool = AgentTool(
            name="echo",
            description="Echo tool",
            parameters={
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
            },
            label="Echo",
            execute=execute_echo,
        )

        context = AgentContext(
            system_prompt="",
            messages=[],
            tools=[tool],
        )

        user_prompt: AgentMessage = create_user_message("start")
        queued_user_message: AgentMessage = create_user_message("interrupt")

        queued_delivered = False
        call_index = [0]
        saw_interrupt_in_context = False

        async def get_steering() -> list[AgentMessage]:
            nonlocal queued_delivered
            # Return steering message after tool execution has started
            if len(executed) >= 1 and not queued_delivered:
                queued_delivered = True
                return [queued_user_message]
            return []

        config = AgentLoopConfig(
            convert_to_llm=identity_converter,
            tool_execution="sequential",
            get_steering_messages=get_steering,
        )

        def stream_fn(model_id: Any, ctx: Any, options: Any = None) -> AiEventStream[AssistantMessage]:
            nonlocal saw_interrupt_in_context
            # Check if interrupt message is in context on second call
            if call_index[0] == 1:
                for m in ctx.messages:
                    if isinstance(m, UserMessage) and isinstance(m.content, str) and m.content == "interrupt":
                        saw_interrupt_in_context = True

            stream: AiEventStream[AssistantMessage] = AiEventStream()

            async def _push() -> None:
                await asyncio.sleep(0)
                if call_index[0] == 0:
                    msg = create_assistant_message(
                        (
                            ToolCall(id="tool-1", name="echo", arguments={"value": "first"}),
                            ToolCall(id="tool-2", name="echo", arguments={"value": "second"}),
                        ),
                        "toolUse",
                    )
                    await stream.send(DoneEvent(stop_reason="toolUse", message=msg))
                    stream.set_result(msg)
                else:
                    msg = create_assistant_message((TextContent(text="done"),))
                    await stream.send(DoneEvent(stop_reason="stop", message=msg))
                    stream.set_result(msg)
                call_index[0] += 1
                await stream.close()

            asyncio.ensure_future(_push())
            return stream

        events: list[AgentEvent] = []
        result_stream = agent_loop(
            [user_prompt], context, config, stream_fn=stream_fn, model="mock/test-model", options=StreamOptions(),  # type: ignore[arg-type]
        )

        async for event in result_stream:
            events.append(event)

        # Both tools should execute before steering is injected
        assert executed == ["first", "second"]

        tool_ends = [
            e for e in events if isinstance(e, ToolExecutionEndEvent)
        ]
        assert len(tool_ends) == 2
        assert not tool_ends[0].is_error
        assert not tool_ends[1].is_error

        # Queued message should appear in events after both tool result messages
        event_sequence: list[str] = []
        for event in events:
            if isinstance(event, MessageStartEvent):
                msg = event.message
                if isinstance(msg, ToolResultMessage):
                    event_sequence.append(f"tool:{msg.tool_call_id}")
                elif isinstance(msg, UserMessage) and isinstance(msg.content, str):
                    event_sequence.append(msg.content)

        assert "interrupt" in event_sequence
        assert event_sequence.index("tool:tool-1") < event_sequence.index("interrupt")
        assert event_sequence.index("tool:tool-2") < event_sequence.index("interrupt")

        # Interrupt message should be in context when second LLM call is made
        assert saw_interrupt_in_context is True

# ---------------------------------------------------------------------------
# agentLoopContinue tests
# ---------------------------------------------------------------------------

class TestAgentLoopContinueWithAgentMessage:
    """Tests matching TS ``describe("agentLoopContinue with AgentMessage", ...)``."""

    def test_throw_when_context_has_no_messages(self) -> None:
        """Should throw when context has no messages."""
        context = AgentContext(
            system_prompt="You are helpful.",
            messages=[],
            tools=[],
        )

        config = AgentLoopConfig(
            convert_to_llm=identity_converter,
        )

        with pytest.raises(ValueError, match="Cannot continue: no messages in context"):
            agent_loop_continue(context, config, stream_fn=lambda m, c, o: None, model="x", options=StreamOptions())

    @pytest.mark.asyncio
    async def test_continue_from_existing_context_without_user_events(self) -> None:
        """Should continue from existing context without emitting user message events."""
        user_message: AgentMessage = create_user_message("Hello")

        context = AgentContext(
            system_prompt="You are helpful.",
            messages=[user_message],
            tools=[],
        )

        config = AgentLoopConfig(
            convert_to_llm=identity_converter,
        )

        stream_fn = create_mock_stream_fn([
            create_assistant_message((TextContent(text="Response"),)),
        ])

        events: list[AgentEvent] = []
        stream = agent_loop_continue(context, config, stream_fn=stream_fn, model="mock/test-model", options=StreamOptions())

        async for event in stream:
            events.append(event)

        messages = await stream.result()

        # Should only return the new assistant message (not the existing user message)
        assert len(messages) == 1
        assert messages[0].role == "assistant"

        # Should NOT have user message events (that's the key difference)
        message_end_events = [e for e in events if isinstance(e, MessageEndEvent)]
        assert len(message_end_events) == 1
        assert message_end_events[0].message.role == "assistant"


@pytest.mark.parametrize("execution", ["sequential", "parallel"])
async def test_tool_exception_logs_are_marked_and_have_matching_error_outcomes(caplog, execution) -> None:
    import logging

    from xdog.agent.agent_loop import run_agent_loop

    async def execute(tool_call_id, params, cancel, on_update, **kwargs):
        raise RuntimeError(f"follow-up failed: {tool_call_id}")

    tool = AgentTool(name="agent", label="Agent", description="Follow up", parameters={}, execute=execute)
    calls = tuple(ToolCall(id=f"call-{i}", name="agent", arguments={}) for i in range(2))
    events: list[AgentEvent] = []

    async def emit(event: AgentEvent) -> None:
        events.append(event)

    with caplog.at_level(logging.ERROR, logger="xdog.agent.agent_loop"):
        messages = await run_agent_loop(
            [create_user_message("follow up")], AgentContext(system_prompt="", messages=[], tools=[tool]),
            AgentLoopConfig(tool_execution=execution), emit,
            stream_fn=create_mock_stream_fn([
                create_assistant_message(calls, "toolUse"),
                create_assistant_message((TextContent(text="done"),)),
            ]),
            model="mock/test-model", options=StreamOptions(),
        )
    records = [record for record in caplog.records if record.name == "xdog.agent.agent_loop"]
    assert len(records) == 2
    ends = [event for event in events if isinstance(event, ToolExecutionEndEvent)]
    results = [message for message in messages if isinstance(message, ToolResultMessage)]
    assert [event.tool_call_id for event in ends] == [call.id for call in calls]
    assert [result.tool_call_id for result in results] == [call.id for call in calls]
    for record, end, result in zip(records, ends, results, strict=True):
        assert record.levelno == logging.ERROR
        assert record.xdog_tool_error_represented is True
        assert record.tool_call_id == end.tool_call_id == result.tool_call_id
        assert record.getMessage() == "Tool agent execution failed"
        assert record.exc_info is not None
        assert isinstance(record.exc_info[1], RuntimeError)
        assert str(record.exc_info[1]) == f"follow-up failed: {end.tool_call_id}"
        assert end.is_error and end.result.is_error and result.is_error
        assert end.result.content == result.content == (
            TextContent(text=f"Tool error: follow-up failed: {end.tool_call_id}"),
        )


@pytest.mark.parametrize("execution", ["sequential", "parallel"])
async def test_unrepresented_finalizer_exception_log_is_not_marked(caplog, execution) -> None:
    import logging

    from xdog.agent.agent_loop import run_agent_loop

    async def execute(tool_call_id, params, cancel, on_update, **kwargs):
        raise RuntimeError("tool failed")

    async def after_tool_call(context, cancel):
        raise ValueError("finalization failed before tool outcome")

    tool = AgentTool(name="agent", label="Agent", description="Follow up", parameters={}, execute=execute)
    events: list[AgentEvent] = []

    async def emit(event: AgentEvent) -> None:
        events.append(event)

    with caplog.at_level(logging.ERROR, logger="xdog.agent.agent_loop"):
        with pytest.raises(ValueError, match="finalization failed before tool outcome"):
            await run_agent_loop(
                [create_user_message("follow up")], AgentContext(system_prompt="", messages=[], tools=[tool]),
                AgentLoopConfig(tool_execution=execution, after_tool_call=after_tool_call), emit,
                stream_fn=create_mock_stream_fn([
                    create_assistant_message((ToolCall(id="call-1", name="agent", arguments={}),), "toolUse"),
                ]),
                model="mock/test-model", options=StreamOptions(),
            )
    loop_failure = [record for record in caplog.records if record.getMessage() == "Agent loop failed"]
    assert len(loop_failure) == 1
    assert not hasattr(loop_failure[0], "xdog_tool_error_represented")
    assert loop_failure[0].exc_info is not None
    assert isinstance(loop_failure[0].exc_info[1], ValueError)
    assert not any(isinstance(event, ToolExecutionEndEvent) for event in events)
