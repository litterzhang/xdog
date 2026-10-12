"""Core agent loop: thought-action-observation cycle with tool calling.

This module implements the central loop that:

1. Sends the current context to the LLM
2. Streams the assistant response (iterating the LLM's EventStream directly)
3. If tool calls are present, validates and executes them (sequential or parallel)
4. Checks for steering/follow-up messages after tool execution
5. Continues until no more tool calls or injected messages remain

The loop communicates progress via an ``AgentEventStream[AgentEvent]`` that
callers consume asynchronously.

Two API styles are provided:

* **Stream-based** (``agent_loop`` / ``agent_loop_continue``): Return an
  ``AgentEventStream`` with a ``.result()`` future yielding the new messages.
* **Imperative** (``run_agent_loop`` / ``run_agent_loop_continue``): Accept
  an ``emit`` callback and return the final ``list[AgentMessage]``.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from dataclasses import replace
from typing import Any

from xdog.agent.core import (
    AgentContext,
    AgentMessage,
    AgentTool,
    AgentToolResult,
    StreamFn,
)
from xdog.agent.event_stream import AgentEventStream
from xdog.agent.events import (
    AgentEndEvent,
    AgentEvent,
    AgentStartEvent,
    MessageEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
    TurnEndEvent,
    TurnStartEvent,
)
from xdog.agent.types import (
    AfterToolCallContext,
    AgentEventSink,
    AgentLoopConfig,
    BeforeToolCallContext,
)
from xdog.ai.types import (
    AssistantMessage,
    Context,
    Message,
    StreamOptions,
    TextContent,
    Tool,
    ToolCall,
    ToolResultMessage,
)
from xdog.ai.utils.validation import validate_tool_arguments

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public API — Stream-based
# ---------------------------------------------------------------------------

def agent_loop(
    prompts: list[AgentMessage],
    context: AgentContext,
    config: AgentLoopConfig,
    stream_fn: StreamFn,
    model: str,
    options: StreamOptions,
    cancel: asyncio.Event | None = None,
    tool_ctx: dict[str, Any] | None = None,
) -> AgentEventStream[AgentEvent]:
    """Start a new agent loop with prompt messages and return an event stream.

    Parameters
    ----------
    prompts:
        Messages to add to context at the start (typically a UserMessage).
    context:
        System prompt, message history, and available tools.
    config:
        Behavioral callbacks (convert_to_llm, hooks, steering, etc.).
    stream_fn:
        The function that calls the LLM.
    model:
        Model name string passed to stream_fn.
    options:
        StreamOptions passed to stream_fn.
    cancel:
        Optional cancellation event.
    """
    stream: AgentEventStream[AgentEvent] = AgentEventStream()

    async def _emit_to_stream(event: AgentEvent) -> None:
        await stream.send(event)

    async def _run_and_close() -> None:
        try:
            messages = await _run_agent_loop_impl(
                prompts, context, config, _emit_to_stream, stream_fn, model, options, cancel, tool_ctx,
            )
            stream.end(messages)
        except Exception:
            stream.end([])
            raise

    asyncio.ensure_future(_run_and_close())
    return stream


def agent_loop_continue(
    context: AgentContext,
    config: AgentLoopConfig,
    stream_fn: StreamFn,
    model: str,
    options: StreamOptions,
    cancel: asyncio.Event | None = None,
    tool_ctx: dict[str, Any] | None = None,
) -> AgentEventStream[AgentEvent]:
    """Continue an existing agent loop from accumulated context.

    The last message in context must convert to a ``user`` or ``toolResult``
    role via ``convertToLlm``.  If context is empty or ends with an
    ``assistant`` message, an error is raised.
    """
    if not context.messages:
        raise ValueError("Cannot continue: no messages in context")

    last = context.messages[-1]
    if isinstance(last, AssistantMessage):
        raise ValueError("Cannot continue from message role: assistant")

    stream: AgentEventStream[AgentEvent] = AgentEventStream()

    async def _emit_to_stream(event: AgentEvent) -> None:
        await stream.send(event)

    async def _run_and_close() -> None:
        try:
            messages = await _run_agent_loop_continue_impl(
                context, config, _emit_to_stream, stream_fn, model, options, cancel,
            )
            stream.end(messages)
        except Exception:
            stream.end([])
            raise

    asyncio.ensure_future(_run_and_close())
    return stream


# ---------------------------------------------------------------------------
# Public API — Imperative
# ---------------------------------------------------------------------------

async def run_agent_loop(
    prompts: list[AgentMessage],
    context: AgentContext,
    config: AgentLoopConfig,
    emit: AgentEventSink,
    stream_fn: StreamFn,
    model: str,
    options: StreamOptions,
    cancel: asyncio.Event | None = None,
    tool_ctx: dict[str, Any] | None = None,
) -> list[AgentMessage]:
    """Run the agent loop, emitting events via *emit*, and return new messages.

    Parameters
    ----------
    prompts:
        Messages to add to context at the start.
    context:
        System prompt, message history, and available tools.
    config:
        Callbacks and streaming options.
    emit:
        Callback that receives each ``AgentEvent``.  May be sync or async.
    cancel:
        Optional cancellation event.
    stream_fn:
        Custom stream function.
    """
    return await _run_agent_loop_impl(prompts, context, config, emit, stream_fn, model, options, cancel, tool_ctx)


async def run_agent_loop_continue(
    context: AgentContext,
    config: AgentLoopConfig,
    emit: AgentEventSink,
    stream_fn: StreamFn,
    model: str,
    options: StreamOptions,
    cancel: asyncio.Event | None = None,
    tool_ctx: dict[str, Any] | None = None,
) -> list[AgentMessage]:
    """Continue the agent loop imperatively.

    Raises ``ValueError`` if context is empty or ends with assistant.
    """
    if not context.messages:
        raise ValueError("Cannot continue: no messages in context")

    last = context.messages[-1]
    if isinstance(last, AssistantMessage):
        raise ValueError("Cannot continue from message role: assistant")

    return await _run_agent_loop_continue_impl(context, config, emit, stream_fn, model, options, cancel, tool_ctx)


# ---------------------------------------------------------------------------
# Internal: run_agent_loop_impl & run_agent_loop_continue_impl
# ---------------------------------------------------------------------------

async def _run_agent_loop_impl(
    prompts: list[AgentMessage],
    context: AgentContext,
    config: AgentLoopConfig,
    emit: AgentEventSink,
    stream_fn: StreamFn,
    model: str,
    options: StreamOptions,
    cancel: asyncio.Event | None,
    tool_ctx: dict[str, Any] | None = None,
) -> list[AgentMessage]:
    """Core implementation for ``agent_loop`` / ``run_agent_loop``."""
    new_messages: list[AgentMessage] = list(prompts)
    current_context = AgentContext(
        system_prompt=context.system_prompt,
        messages=list(context.messages) + list(prompts),
        tools=context.tools,
    )

    await _emit_event(emit, AgentStartEvent())
    await _emit_event(emit, TurnStartEvent())

    # Emit message_start / message_end for each prompt
    for prompt in prompts:
        await _emit_event(emit, MessageStartEvent(message=prompt))
        await _emit_event(emit, MessageEndEvent(message=prompt))

    await _run_loop(current_context, new_messages, config, emit, stream_fn, model, options, cancel, tool_ctx)
    return new_messages


async def _run_agent_loop_continue_impl(
    context: AgentContext,
    config: AgentLoopConfig,
    emit: AgentEventSink,
    stream_fn: StreamFn,
    model: str,
    options: StreamOptions,
    cancel: asyncio.Event | None,
    tool_ctx: dict[str, Any] | None = None,
) -> list[AgentMessage]:
    """Core implementation for ``agent_loop_continue`` / ``run_agent_loop_continue``."""
    new_messages: list[AgentMessage] = []
    current_context = AgentContext(
        system_prompt=context.system_prompt,
        messages=list(context.messages),
        tools=context.tools,
    )

    await _emit_event(emit, AgentStartEvent())
    await _emit_event(emit, TurnStartEvent())

    await _run_loop(current_context, new_messages, config, emit, stream_fn, model, options, cancel, tool_ctx)
    return new_messages


# ---------------------------------------------------------------------------
# Main loop logic (shared by agentLoop and agentLoopContinue)
# ---------------------------------------------------------------------------

async def _run_loop(
    current_context: AgentContext,
    new_messages: list[AgentMessage],
    config: AgentLoopConfig,
    emit: AgentEventSink,
    stream_fn: StreamFn,
    model: str,
    options: StreamOptions,
    cancel: asyncio.Event | None,
    tool_ctx: dict[str, Any] | None = None,
) -> None:
    """Two-loop structure matching the TS ``runLoop()``.

    Outer loop: continues when queued follow-up messages arrive after
    agent would stop.

    Inner loop: processes tool calls and steering messages.
    """
    first_turn = True
    # Check for steering messages at start (user may have typed while waiting)
    pending_messages: list[AgentMessage] = []
    if config.get_steering_messages is not None:
        pending_messages = await config.get_steering_messages()

    try:
        # Outer loop: continues when follow-up messages arrive
        while True:
            has_more_tool_calls = True

            # Inner loop: process tool calls and steering messages
            while has_more_tool_calls or pending_messages:
                if _is_cancelled(cancel):
                    break

                if not first_turn:
                    await _emit_event(emit, TurnStartEvent())
                else:
                    first_turn = False

                # Process pending messages (inject before next assistant response)
                if pending_messages:
                    for msg in pending_messages:
                        await _emit_event(emit, MessageStartEvent(message=msg))
                        await _emit_event(emit, MessageEndEvent(message=msg))
                        current_context.messages.append(msg)
                        new_messages.append(msg)
                    pending_messages = []

                # Stream assistant response
                message = await _stream_assistant_response(
                    current_context, config, emit, stream_fn, model, options, cancel,
                )
                if config.prepare_request is not None:
                    # A compacted prefix must not remain pinned by the run-output list.
                    retained = {id(item) for item in current_context.messages}
                    new_messages[:] = [item for item in new_messages if id(item) in retained]
                new_messages.append(message)

                if message.stop_reason in ("error", "aborted"):
                    await _emit_event(emit, TurnEndEvent(message=message, tool_results=()))
                    await _emit_event(emit, AgentEndEvent(messages=tuple(new_messages)))
                    return

                # Check for tool calls
                tool_calls = _extract_tool_calls(message)
                has_more_tool_calls = bool(tool_calls)

                tool_results: list[ToolResultMessage] = []
                if has_more_tool_calls:
                    tool_results = await _execute_tool_calls(
                        current_context, message, config, cancel, emit, tool_ctx,
                    )
                    for tr in tool_results:
                        current_context.messages.append(tr)
                        new_messages.append(tr)

                await _emit_event(emit, TurnEndEvent(
                    message=message,
                    tool_results=tuple(tool_results),
                ))

                if _is_cancelled(cancel):
                    break

                # Check for steering messages after tool execution
                pending_messages = []
                if config.get_steering_messages is not None:
                    pending_messages = await config.get_steering_messages()

            if _is_cancelled(cancel):
                break

            # Agent would stop here.  Check for follow-up messages.
            follow_ups: list[AgentMessage] = []
            if config.get_follow_up_messages is not None:
                follow_ups = await config.get_follow_up_messages()
            if follow_ups:
                pending_messages = follow_ups
                continue

            # No more messages — exit
            break

        await _emit_event(emit, AgentEndEvent(messages=tuple(new_messages)))

    except Exception:
        logger.exception("Agent loop failed")
        raise


# ---------------------------------------------------------------------------
# Stream assistant response (iterates LLM EventStream directly)
# ---------------------------------------------------------------------------

async def _stream_assistant_response(
    context: AgentContext,
    config: AgentLoopConfig,
    emit: AgentEventSink,
    stream_fn: StreamFn,
    model: str,
    options: StreamOptions,
    cancel: asyncio.Event | None,
) -> AssistantMessage:
    """Stream assistant response, handling LLM events inline.

    Matches the TS ``streamAssistantResponse()`` — iterates the
    ``EventStream[AssistantMessage]`` returned by the stream function,
    managing a ``partialMessage`` in context during streaming.
    """
    # Apply context transform if configured (AgentMessage[] -> AgentMessage[])
    messages = list(context.messages)
    if config.transform_context is not None:
        messages = await config.transform_context(messages)

    # Convert to LLM-compatible messages (AgentMessage[] -> Message[])
    llm_messages = await _convert_messages(config, messages)

    # Snapshot the actual current inventory after owned context hooks; one request
    # and its tool batch share it. Stale executor identities still fail at admission.
    if config.get_tools is not None:
        context.tools = list(config.get_tools())

    # Build LLM context
    llm_context = Context(
        system_prompt=context.system_prompt or None,
        messages=tuple(llm_messages),
        tools=_tools_to_ai_tools(context.tools) if context.tools else None,
    )

    # Validate exactly what will reach this adapter, including retained/tool/hook
    # images. Do not silently prune data or infer another provider's capabilities.
    from xdog.agent.image_context import validate_image_context
    await validate_image_context(stream_fn, model, llm_context)
    if config.prepare_request is not None:
        original_request = llm_context
        llm_context = await config.prepare_request(context, llm_context)
        if llm_context is not original_request:
            await validate_image_context(stream_fn, model, llm_context)
    if _is_cancelled(cancel):
        raise asyncio.CancelledError

    # Observe actual adapter entry, not merely context preparation or rejected input.
    if config.on_model_request is not None:
        config.on_model_request()
    # Call stream_fn
    response = stream_fn(model, llm_context, options)

    # Handle the case where stream_fn returns a coroutine (async fn returning EventStream)
    if inspect.isawaitable(response):
        response = await response

    iterator = response.__aiter__()
    try:
        partial_message: AssistantMessage | None = None
        added_partial = False

        async for event in iterator:
            if _is_cancelled(cancel):
                raise asyncio.CancelledError

            event_type = event.type

            if event_type == "start":
                partial_message = event.partial  # type: ignore[union-attr]
                if partial_message is not None:
                    context.messages.append(partial_message)
                    added_partial = True
                await _emit_event(emit, MessageStartEvent(message=partial_message))

            elif event_type in (
                "text_start", "text_delta", "text_end",
                "thinking_start", "thinking_delta", "thinking_end",
                "tool_call_start", "tool_call_delta", "tool_call_done",
            ):
                event_partial = getattr(event, "partial", None)
                if partial_message is not None and event_partial is not None:
                    partial_message = event_partial
                    context.messages[-1] = partial_message
                    await _emit_event(emit, MessageUpdateEvent(
                        message=partial_message,
                        assistant_message_event=event,
                    ))

            elif event_type in ("done", "error"):
                final_message = await response.result()
                if added_partial:
                    context.messages[-1] = final_message
                else:
                    context.messages.append(final_message)
                if not added_partial:
                    await _emit_event(emit, MessageStartEvent(message=final_message))
                await _emit_event(emit, MessageEndEvent(message=final_message))
                return final_message

        # Fallback: stream ended without done/error event
        final_message = await response.result()
        if added_partial:
            context.messages[-1] = final_message
        else:
            context.messages.append(final_message)
            await _emit_event(emit, MessageStartEvent(message=final_message))
        await _emit_event(emit, MessageEndEvent(message=final_message))
        return final_message
    finally:
        try:
            try:
                close_iterator = getattr(iterator, "aclose", None)
                if callable(close_iterator):
                    await close_iterator()
            finally:
                close_response = getattr(response, "aclose", None)
                if id(response) != id(iterator) and callable(close_response):
                    await close_response()
        except Exception as exc:
            if config.on_stream_close_error is not None:
                config.on_stream_close_error(exc)
            raise


# ---------------------------------------------------------------------------
# Tool execution dispatch
# ---------------------------------------------------------------------------

async def _execute_tool_calls(
    current_context: AgentContext,
    assistant_message: AssistantMessage,
    config: AgentLoopConfig,
    cancel: asyncio.Event | None,
    emit: AgentEventSink,
    tool_ctx: dict[str, Any] | None = None,
) -> list[ToolResultMessage]:
    """Execute tool calls from an assistant message."""
    tool_calls = _extract_tool_calls(assistant_message)
    if config.tool_execution == "sequential":
        return await _execute_tool_calls_sequential(
            current_context, assistant_message, tool_calls, config, cancel, emit, tool_ctx,
        )
    return await _execute_tool_calls_parallel(
        current_context, assistant_message, tool_calls, config, cancel, emit, tool_ctx,
    )


# ---------------------------------------------------------------------------
# Sequential tool execution
# ---------------------------------------------------------------------------

async def _execute_tool_calls_sequential(
    current_context: AgentContext,
    assistant_message: AssistantMessage,
    tool_calls: list[ToolCall],
    config: AgentLoopConfig,
    cancel: asyncio.Event | None,
    emit: AgentEventSink,
    tool_ctx: dict[str, Any] | None = None,
) -> list[ToolResultMessage]:
    """Execute tool calls one at a time: prepare -> execute -> finalize each."""
    results: list[ToolResultMessage] = []
    tools = list(current_context.tools) if current_context.tools else []

    for tc in tool_calls:
        await _emit_event(emit, ToolExecutionStartEvent(
            tool_call_id=tc.id,
            tool_name=tc.name,
            args=dict(tc.arguments),
        ))

        if _is_cancelled(cancel):
            result_msg = await _emit_tool_call_outcome(
                tc,
                _create_error_tool_result("Tool execution cancelled"),
                True,
                emit,
            )
            results.append(result_msg)
            continue

        preparation = await _prepare_tool_call(
            current_context, assistant_message, tc, tools, config, cancel,
        )

        if preparation["kind"] == "immediate":
            result_msg = await _emit_tool_call_outcome(
                tc, preparation["result"], preparation["is_error"], emit,
            )
            results.append(result_msg)
        else:
            executed = await _execute_prepared_tool_call(preparation, cancel, emit, tool_ctx)
            result_msg = await _finalize_executed_tool_call(
                current_context, assistant_message, preparation, executed, config, cancel, emit,
            )
            results.append(result_msg)

    return results


# ---------------------------------------------------------------------------
# Parallel tool execution
# ---------------------------------------------------------------------------

async def _execute_tool_calls_parallel(
    current_context: AgentContext,
    assistant_message: AssistantMessage,
    tool_calls: list[ToolCall],
    config: AgentLoopConfig,
    cancel: asyncio.Event | None,
    emit: AgentEventSink,
    tool_ctx: dict[str, Any] | None = None,
) -> list[ToolResultMessage]:
    """Execute tool calls in parallel.

    1. Prepare all tool calls sequentially (before_tool_call hook is serial).
    2. Execute all prepared calls concurrently.
    3. Finalize all results in order (after_tool_call hook is serial).
    """
    tools = list(current_context.tools) if current_context.tools else []
    immediate_outcomes: dict[int, tuple[ToolCall, AgentToolResult, bool]] = {}
    runnable_calls: list[tuple[int, dict[str, Any]]] = []

    for index, tc in enumerate(tool_calls):
        await _emit_event(emit, ToolExecutionStartEvent(
            tool_call_id=tc.id,
            tool_name=tc.name,
            args=dict(tc.arguments),
        ))

        if _is_cancelled(cancel):
            immediate_outcomes[index] = (
                tc,
                _create_error_tool_result("Tool execution cancelled"),
                True,
            )
            continue

        preparation = await _prepare_tool_call(
            current_context, assistant_message, tc, tools, config, cancel,
        )

        if preparation["kind"] == "immediate":
            immediate_outcomes[index] = (
                tc,
                preparation["result"],
                preparation["is_error"],
            )
        else:
            runnable_calls.append((index, preparation))

    # Execute concurrently — create tasks so all start in parallel.
    task_by_index: dict[int, tuple[dict[str, Any], asyncio.Task[dict[str, Any]]]] = {
        index: (
            preparation,
            asyncio.ensure_future(
                _execute_prepared_tool_call(preparation, cancel, emit, tool_ctx)
            ),
        )
        for index, preparation in runnable_calls
    }

    try:
        # Emit and return every result in assistant source order.
        results: list[ToolResultMessage] = []
        for index, tc in enumerate(tool_calls):
            immediate = immediate_outcomes.get(index)
            if immediate is not None:
                immediate_tc, result, is_error = immediate
                results.append(await _emit_tool_call_outcome(
                    immediate_tc, result, is_error, emit,
                ))
                continue

            prepared, task = task_by_index[index]
            executed = await asyncio.shield(task)
            results.append(await _finalize_executed_tool_call(
                current_context, assistant_message, prepared,
                executed, config, cancel, emit,
            ))

        return results
    finally:
        # The batch owns every started tool, not just the source-order task being
        # awaited. Producer cancellation/finalizer failure must settle siblings
        # before publishing idle or releasing session/workspace ownership.
        tasks = [task for _prepared, task in task_by_index.values()]
        for task in tasks:
            if not task.done() and not task.cancelling():
                task.cancel()
        settlement = asyncio.gather(*tasks, return_exceptions=True)
        while not settlement.done():
            try:
                await asyncio.shield(settlement)
            except asyncio.CancelledError:
                continue
        settlement.result()


# ---------------------------------------------------------------------------
# Tool call lifecycle: prepare -> execute -> finalize
# ---------------------------------------------------------------------------

async def _prepare_tool_call(
    current_context: AgentContext,
    assistant_message: AssistantMessage,
    tc: ToolCall,
    tools: list[AgentTool],
    config: AgentLoopConfig,
    cancel: asyncio.Event | None,
) -> dict[str, Any]:
    """Prepare a tool call for execution.

    Returns a dict with ``kind`` == ``"immediate"`` or ``"prepared"``.
    """
    tool = _find_tool(tools, tc.name)

    if tool is None or tool.execute is None:
        return {
            "kind": "immediate",
            "result": _create_error_tool_result(f"Tool {tc.name} not found"),
            "is_error": True,
        }

    try:
        # Validate tool arguments
        args = dict(tc.arguments)
        if tool.validate_arguments is not None:
            checked = tool.validate_arguments(args)
            if inspect.isawaitable(checked):
                validation_task = asyncio.ensure_future(checked)
                watcher = asyncio.create_task(cancel.wait()) if cancel is not None else None
                try:
                    if watcher is not None:
                        done, _pending = await asyncio.wait({validation_task, watcher},
                                                           return_when=asyncio.FIRST_COMPLETED)
                        if watcher in done:
                            validation_task.cancel()
                            await asyncio.gather(validation_task, return_exceptions=True)
                            return {"kind": "immediate",
                                    "result": _create_error_tool_result("Tool execution cancelled"), "is_error": True}
                    await validation_task
                finally:
                    if not validation_task.done() and not validation_task.cancelling():
                        validation_task.cancel()
                    if watcher is not None:
                        watcher.cancel()
                    await asyncio.gather(validation_task, *([watcher] if watcher is not None else []),
                                         return_exceptions=True)
        elif tool.parameters:
            validation = validate_tool_arguments(args, tool.parameters)
            if not validation.valid:
                error_msg = "; ".join(validation.errors)
                return {
                    "kind": "immediate",
                    "result": _create_error_tool_result(error_msg),
                    "is_error": True,
                }

        # before_tool_call hook
        if config.before_tool_call is not None:
            before_ctx = BeforeToolCallContext(
                assistant_message=assistant_message,
                tool_call=tc,
                args=args,
                context=current_context,
            )
            try:
                before_result = await config.before_tool_call(before_ctx, cancel)
            except asyncio.CancelledError:
                return {
                    "kind": "immediate",
                    "result": _create_error_tool_result("Tool execution cancelled"),
                    "is_error": True,
                }
            if before_result is not None and before_result.block:
                reason = before_result.reason or "Tool execution was blocked"
                return {
                    "kind": "immediate",
                    "result": _create_error_tool_result(reason),
                    "is_error": True,
                }

        return {
            "kind": "prepared",
            "tool_call": tc,
            "tool": tool,
            "args": args,
        }
    except Exception as exc:
        return {
            "kind": "immediate",
            "result": _create_error_tool_result(str(exc)),
            "is_error": True,
        }


async def _execute_prepared_tool_call(
    prepared: dict[str, Any],
    cancel: asyncio.Event | None,
    emit: AgentEventSink,
    tool_ctx: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute a prepared tool call."""
    tool: AgentTool = prepared["tool"]
    tc: ToolCall = prepared["tool_call"]
    args: dict[str, Any] = prepared["args"]

    async def on_update(partial_result: AgentToolResult) -> None:
        await _emit_event(emit, ToolExecutionUpdateEvent(
            tool_call_id=tc.id,
            tool_name=tc.name,
            args=args,
            partial_result=partial_result,
        ))

    try:
        assert tool.execute is not None
        result = await tool.execute(tc.id, args, cancel, on_update, ctx=tool_ctx)
        if type(result.is_error) is not bool:
            raise ValueError("Tool result error flag must be a boolean")
        return {"result": result, "is_error": result.is_error}
    except asyncio.CancelledError:
        return {
            "result": _create_error_tool_result("Tool execution cancelled"),
            "is_error": True,
            "interrupted": True,
        }
    except Exception as exc:
        logger.exception("Tool %s execution failed", tool.name)
        return {
            "result": _create_error_tool_result(f"Tool error: {exc}"),
            "is_error": True,
        }


async def _finalize_executed_tool_call(
    current_context: AgentContext,
    assistant_message: AssistantMessage,
    prepared: dict[str, Any],
    executed: dict[str, Any],
    config: AgentLoopConfig,
    cancel: asyncio.Event | None,
    emit: AgentEventSink,
) -> ToolResultMessage:
    """Apply the after_tool_call hook and emit final events."""
    result: AgentToolResult = executed["result"]
    is_error: bool = executed["is_error"]
    tc: ToolCall = prepared["tool_call"]

    if config.after_tool_call is not None:
        after_ctx = AfterToolCallContext(
            assistant_message=assistant_message,
            tool_call=tc,
            args=prepared["args"],
            result=result,
            is_error=is_error,
            context=current_context,
        )
        try:
            after_result = await config.after_tool_call(after_ctx, cancel)
        except asyncio.CancelledError:
            result = _create_error_tool_result("Tool execution cancelled")
            is_error = True
            executed["interrupted"] = True
            after_result = None

        if after_result is not None:
            if after_result.content is not None:
                result = AgentToolResult(
                    content=after_result.content,
                    details=after_result.details if after_result.details is not None else result.details,
                )
            elif after_result.details is not None:
                result = AgentToolResult(
                    content=result.content,
                    details=after_result.details,
                )
            if after_result.is_error is not None:
                is_error = after_result.is_error

    return await _emit_tool_call_outcome(tc, result, is_error, emit, interrupted=executed.get("interrupted", False))


async def _emit_tool_call_outcome(
    tc: ToolCall,
    result: AgentToolResult,
    is_error: bool,
    emit: AgentEventSink,
    *, interrupted: bool = False,
) -> ToolResultMessage:
    """Emit tool_execution_end and message lifecycle events, return ToolResultMessage."""
    result = replace(result, is_error=is_error)  # Align rich result and lifecycle/message flags after hooks.
    await _emit_event(emit, ToolExecutionEndEvent(
        tool_call_id=tc.id,
        tool_name=tc.name,
        result=result,
        is_error=is_error,
        interrupted=interrupted,
    ))

    tool_result_msg = ToolResultMessage(
        tool_call_id=tc.id,
        tool_name=tc.name,
        content=result.content if result else (),
        details=result.details if result else None,
        is_error=is_error,
    )

    await _emit_event(emit, MessageStartEvent(message=tool_result_msg))
    await _emit_event(emit, MessageEndEvent(message=tool_result_msg))
    return tool_result_msg


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_cancelled(cancel: asyncio.Event | None) -> bool:
    """Check whether the cancellation event has been set."""
    return cancel is not None and cancel.is_set()


async def _emit_event(emit: AgentEventSink, event: AgentEvent) -> None:
    """Call the emit sink, awaiting if it returns a coroutine."""
    result = emit(event)
    if inspect.isawaitable(result):
        await result


async def _convert_messages(
    config: AgentLoopConfig,
    messages: list[AgentMessage],
) -> list[Message]:
    """Convert agent messages to LLM messages via the config callback."""
    if config.convert_to_llm is None:
        return _default_convert(messages)

    result = config.convert_to_llm(messages)
    if inspect.isawaitable(result):
        return await result
    return result


def _default_convert(messages: list[AgentMessage]) -> list[Message]:
    """Project named canonical context content; keep unknown custom metadata out of the LLM."""
    from xdog.agent.extensions.context_message import llm_message
    from xdog.ai.types import AssistantMessage, ToolResultMessage, UserMessage

    result: list[Message] = []
    for message in messages:
        if isinstance(message, (UserMessage, AssistantMessage, ToolResultMessage)):
            result.append(message)
        elif (projected := llm_message(message)) is not None:
            result.append(projected)
    return result


def _extract_tool_calls(message: AgentMessage) -> list[ToolCall]:
    """Extract ToolCall content parts from an AssistantMessage."""
    if not isinstance(message, AssistantMessage):
        return []
    return [part for part in message.content if isinstance(part, ToolCall)]


def _find_tool(tools: list[AgentTool], name: str) -> AgentTool | None:
    """Look up a tool by name."""
    for tool in tools:
        if tool.name == name:
            return tool
    return None


def _tools_to_ai_tools(tools: list[AgentTool] | None) -> tuple[Tool, ...]:
    """Convert AgentTool list to ai.types.Tool tuple for the LLM context."""
    if not tools:
        return ()
    return tuple(
        Tool(name=t.name, description=t.description, parameters=t.parameters)
        for t in tools
    )


def _create_error_tool_result(message: str) -> AgentToolResult:
    """Create an error tool result with a text content block."""
    return AgentToolResult(
        content=(TextContent(text=message),),
        details=None,
        is_error=True,
    )
