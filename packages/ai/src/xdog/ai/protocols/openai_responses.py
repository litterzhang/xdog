"""OpenAI Responses API protocol.

Implements streaming via the ``/v1/responses`` endpoint. This protocol uses
``input`` instead of ``messages``, returns ``output`` items, and supports
reasoning summaries natively.

Key differences from openai-completions:
- Endpoint: ``/v1/responses`` (not ``/v1/chat/completions``)
- Input: ``input`` array with ``input_text`` / ``input_image`` items
- System role: ``developer`` for reasoning models, ``system`` otherwise
- Tool calls: ``function_call`` items with ``call_id`` / ``id``
- Reasoning: ``reasoning`` items with summary text
- SSE events: ``response.output_text.delta``, ``response.output_item.added``, etc.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx
from xdog.ai.core import AuthResult, BaseProtocol
from xdog.ai.native import NativeEventStream, NativeResponse, ProtocolRequest
from xdog.ai.protocols._message_builder import MessageBuilder
from xdog.ai.types import (
    AssistantMessage,
    AssistantMessageEvent,
    Context,
    DoneEvent,
    ErrorEvent,
    ImageContent,
    Message,
    Model,
    StartEvent,
    StatusEvent,
    StopReason,
    StreamOptions,
    TextContent,
    TextDeltaEvent,
    TextDoneEvent,
    TextStartEvent,
    ThinkingContent,
    ThinkingDeltaEvent,
    ThinkingDoneEvent,
    ThinkingStartEvent,
    Tool,
    ToolCall,
    ToolCallDeltaEvent,
    ToolCallDoneEvent,
    ToolCallStartEvent,
    ToolChoice,
    ToolResultMessage,
    Usage,
    UsageEvent,
    UserMessage,
)
from xdog.ai.utils.cost import usage_with_cost
from xdog.ai.utils.event_stream import EventStream
from xdog.ai.utils.json_parse import parse_partial_json
from xdog.ai.utils.sanitize_unicode import sanitize_unicode

logger = logging.getLogger(__name__)

_RESPONSE_ITEM_ID_MAX_LENGTH = 64


# ---------------------------------------------------------------------------
# Stop reason mapping
# ---------------------------------------------------------------------------

_STOP_REASON_MAP: dict[str, StopReason] = {
    "completed": "stop",
    "incomplete": "length",
    "failed": "error",
    "cancelled": "error",
    "in_progress": "stop",
    "queued": "stop",
}


def _map_stop_reason(status: str | None) -> StopReason:
    if not status:
        return "stop"
    return _STOP_REASON_MAP.get(status, "error")


# ---------------------------------------------------------------------------
# Context -> Responses input conversion
# ---------------------------------------------------------------------------


def context_to_responses_input(
    context: Context,
    model: Model,
) -> list[dict[str, Any]]:
    """Convert a :class:`Context` to the OpenAI Responses ``input`` array."""
    items: list[dict[str, Any]] = []

    # System / developer prompt
    if context.system_prompt:
        from xdog.ai.types import system_prompt_text
        text = system_prompt_text(context.system_prompt)
        if text:
            role = "developer" if model.reasoning else "system"
            items.append({
                "role": role,
                "content": sanitize_unicode(text),
            })

    pending_tool_calls: list[str] = []
    resolved_tool_calls: set[str] = set()

    def _flush_unmatched_tool_calls() -> None:
        for call_id in pending_tool_calls:
            if call_id not in resolved_tool_calls:
                items.append({
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": "Tool execution cancelled",
                })
                resolved_tool_calls.add(call_id)
        pending_tool_calls.clear()

    for msg in (context.messages or ()):
        if isinstance(msg, AssistantMessage):
            _flush_unmatched_tool_calls()
            assistant_items = _convert_assistant_message(msg)
            if assistant_items is not None:
                items.extend(assistant_items)
            if assistant_items is not None:
                pending_tool_calls.extend(
                    _responses_call_id(block.id)
                    for block in msg.content
                    if isinstance(block, ToolCall)
                    and _responses_call_id(block.id) not in resolved_tool_calls
                )
            continue

        if isinstance(msg, ToolResultMessage):
            call_id = _responses_call_id(msg.tool_call_id)
            if call_id in pending_tool_calls and call_id not in resolved_tool_calls:
                pending_tool_calls.remove(call_id)
                resolved_tool_calls.add(call_id)
                items.append(_convert_tool_result(msg))
            continue

        _flush_unmatched_tool_calls()
        converted = _convert_message(msg, model)
        if converted is not None:
            if isinstance(converted, list):
                items.extend(converted)
            else:
                items.append(converted)

    _flush_unmatched_tool_calls()
    return items


def _responses_call_id(call_id: str) -> str:
    """Return the Responses API call id from the internal composite id."""
    return call_id.split("|", 1)[0]


def _convert_message(
    msg: Message,
    model: Model,
) -> dict[str, Any] | list[dict[str, Any]] | None:
    """Convert a single message to Responses API input format."""
    if isinstance(msg, UserMessage):
        return _convert_user_message(msg, model)
    if isinstance(msg, AssistantMessage):
        return _convert_assistant_message(msg)
    if isinstance(msg, ToolResultMessage):
        return _convert_tool_result(msg)
    return None


def _convert_user_message(
    msg: UserMessage,
    model: Model,
) -> dict[str, Any]:
    """Convert a UserMessage to Responses input."""
    content = msg.content
    if isinstance(content, str):
        return {
            "role": "user",
            "content": [{"type": "input_text", "text": sanitize_unicode(content)}],
        }

    parts: list[dict[str, Any]] = []
    for item in content:
        if isinstance(item, TextContent):
            parts.append({"type": "input_text", "text": sanitize_unicode(item.text)})
        elif isinstance(item, ImageContent) and "image" in model.input:
            parts.append({
                "type": "input_image",
                "detail": "auto",
                "image_url": f"data:{item.mime_type};base64,{item.data}",
            })

    if not parts:
        parts.append({"type": "input_text", "text": ""})

    return {"role": "user", "content": parts}


def _convert_assistant_message(
    msg: AssistantMessage,
) -> list[dict[str, Any]] | None:
    """Convert an AssistantMessage to Responses output items."""
    if msg.stop_reason in ("error", "aborted"):
        return None

    items: list[dict[str, Any]] = []
    for block in msg.content:
        if isinstance(block, ThinkingContent):
            if not block.thinking_signature:
                continue
            try:
                reasoning_item = json.loads(block.thinking_signature)
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(reasoning_item, dict) and reasoning_item.get("type") == "reasoning":
                item_id = reasoning_item.get("id")
                if (
                    not isinstance(item_id, str)
                    or not item_id
                    or len(item_id) > _RESPONSE_ITEM_ID_MAX_LENGTH
                ):
                    # Expected compatibility filtering, repeated for every history replay.
                    # A warning falls through to stderr and corrupts interactive terminal frames.
                    logger.debug("Skipping non-replayable reasoning item with invalid identity")
                    continue
                items.append(reasoning_item)
        elif isinstance(block, TextContent):
            items.append({
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": sanitize_unicode(block.text), "annotations": []}],
                "status": "completed",
            })
        elif isinstance(block, ToolCall):
            call_id = _responses_call_id(block.id)
            items.append({
                "type": "function_call",
                "call_id": call_id,
                "name": block.name,
                "arguments": json.dumps(block.arguments) if block.arguments else "{}",
            })

    return items if items else None


def _convert_tool_result(msg: ToolResultMessage) -> dict[str, Any]:
    """Convert a ToolResultMessage to function_call_output."""
    text_parts = [
        sanitize_unicode(p.text)
        for p in msg.content
        if isinstance(p, TextContent)
    ]
    text = "\n".join(text_parts) if text_parts else ""

    call_id = _responses_call_id(msg.tool_call_id)

    return {
        "type": "function_call_output",
        "call_id": call_id,
        "output": sanitize_unicode(text) if text else "",
    }


def _convert_tools(tools: tuple[Tool, ...]) -> list[dict[str, Any]]:
    """Convert tools to Responses API format."""
    return [
        {
            "type": "function",
            "name": tool.name,
            "description": tool.description or "",
            "parameters": tool.parameters if tool.parameters else {"type": "object", "properties": {}},
        }
        for tool in tools
    ]


def _tool_choice(choice: ToolChoice) -> str | dict[str, Any]:
    """Convert provider-neutral tool choice to Responses format."""
    if choice.type == "any":
        return "required"
    if choice.type in ("auto", "none"):
        return choice.type
    if not choice.name:
        raise ValueError("Named tool choice requires a tool name")
    return {"type": "function", "name": choice.name}


def _response_format(options: StreamOptions) -> dict[str, Any] | None:
    value = options.response_format
    if value is None:
        return None
    result: dict[str, Any] = {
        "type": "json_schema", "name": value.name, "schema": value.schema(),
    }
    if value.description is not None:
        result["description"] = value.description
    if value.strict is not None:
        result["strict"] = value.strict
    return result


def _reasoning_effort(model: Model, requested: str) -> str:
    supported = model.supported_efforts
    if not supported or requested in supported:
        return requested
    order = ("minimal", "low", "medium", "high", "xhigh")
    requested_index = order.index(requested)
    candidates = tuple(
        effort
        for effort in supported
        if effort in order
    )
    if not candidates:
        return requested
    return min(
        candidates,
        key=lambda effort: (abs(order.index(effort) - requested_index), -order.index(effort)),
    )


# ---------------------------------------------------------------------------
# SSE parsing
# ---------------------------------------------------------------------------


def _parse_sse_line(line: str) -> tuple[str | None, str | None]:
    """Parse a single SSE line into (field, value) or (None, None)."""
    if line.startswith("event: "):
        return ("event", line[7:].strip())
    if line.startswith("data: "):
        return ("data", line[6:])
    return (None, None)


async def _iter_sse_events(
    response: httpx.Response,
) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    """Yield ``(event_type, data_dict)`` from an OpenAI Responses SSE stream."""
    current_event: str = "message"
    buffer: list[str] = []

    async for raw_line in response.aiter_lines():
        line = raw_line.strip() if isinstance(raw_line, str) else raw_line.decode().strip()

        if not line:
            if buffer:
                data_str = "\n".join(buffer)
                try:
                    data = json.loads(data_str)
                except json.JSONDecodeError:
                    data = {"raw": data_str}
                yield (current_event, data)
                buffer.clear()
                current_event = "message"
            continue

        field, value = _parse_sse_line(line)
        if field == "event" and value:
            current_event = value
        elif field == "data" and value is not None:
            buffer.append(value)

    # Flush remaining
    if buffer:
        data_str = "\n".join(buffer)
        try:
            data = json.loads(data_str)
        except json.JSONDecodeError:
            data = {"raw": data_str}
        yield (current_event, data)


# ---------------------------------------------------------------------------
# Main stream implementation
# ---------------------------------------------------------------------------


async def _stream_impl(
    model: Model,
    context: Context,
    options: StreamOptions,
    auth: AuthResult,
) -> AsyncIterator[AssistantMessageEvent]:
    """Core streaming implementation for OpenAI Responses API."""
    output = MessageBuilder(model)

    input_items = context_to_responses_input(context, model)

    body: dict[str, Any] = {
        "model": model.id,
        "input": input_items,
        "stream": True,
        "store": False,
    }

    if options.max_tokens is not None:
        body["max_output_tokens"] = options.max_tokens

    if options.temperature is not None:
        body["temperature"] = options.temperature

    text_options: dict[str, Any] = {}
    if options.verbosity is not None:
        text_options["verbosity"] = options.verbosity
    response_format = _response_format(options)
    if response_format is not None:
        text_options["format"] = response_format
    if text_options:
        body["text"] = text_options

    if options.parallel_tool_calls is not None:
        body["parallel_tool_calls"] = options.parallel_tool_calls

    if options.prompt_cache_key is not None:
        body["prompt_cache_key"] = options.prompt_cache_key
    if options.top_p is not None:
        body["top_p"] = options.top_p
    if options.tool_choice is not None:
        body["tool_choice"] = _tool_choice(options.tool_choice)
    if options.metadata is not None:
        body["metadata"] = dict(options.metadata)
    if options.service_tier is not None:
        body["service_tier"] = options.service_tier

    if context.tools:
        body["tools"] = _convert_tools(context.tools)

    # Web search: inject the built-in web_search_preview tool if requested.
    if options.web_search:
        tools_list = body.get("tools", [])
        tools_list.append({"type": "web_search_preview"})
        body["tools"] = tools_list

    # Reasoning support
    if model.reasoning and options.thinking:
        body["reasoning"] = {
            "effort": _reasoning_effort(model, options.thinking),
            "summary": "auto",
        }
        body["include"] = ["reasoning.encrypted_content"]

    # Build URL and headers
    base_url = model.base_url or "https://api.githubcopilot.com"
    url = f"{base_url.rstrip('/')}/v1/responses"

    headers: dict[str, str] = {
        "Content-Type": "application/json",
        **(model.headers or {}),
        **auth.headers,
    }

    if "Authorization" not in headers and auth.api_key:
        headers["Authorization"] = f"Bearer {auth.api_key}"

    # Track current streaming block state
    current_block_type: str | None = None
    partial_args: str = ""
    started = False

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as client:
            async with client.stream("POST", url, json=body, headers=headers) as response:
                if response.status_code != 200:
                    error_body = await response.aread()
                    error_text = error_body.decode("utf-8", errors="replace")
                    output.stop_reason = "error"
                    output.error_message = f"HTTP {response.status_code}: {error_text[:500]}"
                    output.mark_dirty()
                    yield ErrorEvent(
                        error=output.error_message,
                        stop_reason="error",
                        message=output.snapshot(),
                    )
                    return

                async for event_type, data in _iter_sse_events(response):
                    # The event_type from SSE may differ from the JSON "type" field.
                    # OpenAI Responses API puts the actual type in the JSON data.
                    api_type = data.get("type", event_type)

                    if api_type not in ("error", "response.failed") and not started:
                        started = True
                        yield StartEvent(partial=output.snapshot())

                    if api_type == "response.created":
                        resp = data.get("response", {})
                        if resp.get("id"):
                            output.response_id = resp["id"]
                            output.mark_dirty()

                    elif api_type == "response.output_item.added":
                        item = data.get("item", {})
                        item_type = item.get("type", "")

                        if item_type == "reasoning":
                            current_block_type = "reasoning"
                            # Retain the upstream item identity from the first event.
                            # Encrypted reasoning delivered at item.done is bound to
                            # this ID; downstream facades must not invent a new one.
                            output.push_block({
                                "type": "thinking", "thinking": "",
                                "thinking_signature": json.dumps(item), "redacted": False,
                            })
                            yield ThinkingStartEvent(index=output.block_index, partial=output.snapshot())

                        elif item_type == "message":
                            current_block_type = "message"
                            output.push_block({"type": "text", "text": ""})
                            yield TextStartEvent(index=output.block_index, partial=output.snapshot())

                        elif item_type == "function_call":
                            current_block_type = "function_call"
                            partial_args = item.get("arguments", "")
                            call_id = item.get("call_id", "")
                            item_id = item.get("id", "")
                            tool_id = f"{call_id}|{item_id}" if item_id else call_id
                            tool_name = item.get("name", "")
                            output.push_block({
                                "type": "toolCall",
                                "id": tool_id,
                                "name": tool_name,
                                "arguments": {},
                                "partial_args": partial_args,
                            })
                            yield ToolCallStartEvent(
                                index=output.block_index,
                                id=tool_id,
                                name=tool_name,
                                partial=output.snapshot(),
                            )

                        elif item_type == "web_search_call":
                            current_block_type = "web_search_call"
                            yield StatusEvent(
                                status="web_search_started",
                                detail="Searching the web...",
                            )

                    elif api_type == "response.output_text.delta":
                        delta = data.get("delta", "")
                        if delta and current_block_type == "message":
                            block = output.current_block()
                            if block and block.get("type") == "text":
                                block["text"] = block.get("text", "") + delta
                                output.mark_dirty()
                                yield TextDeltaEvent(
                                    index=output.block_index,
                                    delta=delta,
                                    partial=output.snapshot(),
                                )

                    elif api_type == "response.refusal.delta":
                        delta = data.get("delta", "")
                        if delta and current_block_type == "message":
                            block = output.current_block()
                            if block and block.get("type") == "text":
                                block["text"] = block.get("text", "") + delta
                                output.mark_dirty()
                                yield TextDeltaEvent(
                                    index=output.block_index,
                                    delta=delta,
                                    partial=output.snapshot(),
                                )

                    elif api_type == "response.reasoning_summary_text.delta":
                        delta = data.get("delta", "")
                        if delta and current_block_type == "reasoning":
                            block = output.current_block()
                            if block and block.get("type") == "thinking":
                                block["thinking"] = block.get("thinking", "") + delta
                                output.mark_dirty()
                                yield ThinkingDeltaEvent(
                                    index=output.block_index,
                                    delta=delta,
                                    partial=output.snapshot(),
                                )

                    elif api_type == "response.reasoning_summary_part.done":
                        # Paragraph boundary within reasoning
                        if current_block_type == "reasoning":
                            block = output.current_block()
                            if block and block.get("type") == "thinking":
                                block["thinking"] = block.get("thinking", "") + "\n\n"
                                output.mark_dirty()
                                yield ThinkingDeltaEvent(
                                    index=output.block_index,
                                    delta="\n\n",
                                    partial=output.snapshot(),
                                )

                    elif api_type == "response.function_call_arguments.delta":
                        delta = data.get("delta", "")
                        if delta and current_block_type == "function_call":
                            block = output.current_block()
                            if block and block.get("type") == "toolCall":
                                partial_args += delta
                                block["partial_args"] = partial_args
                                block["arguments"] = parse_partial_json(partial_args)
                                output.mark_dirty()
                                yield ToolCallDeltaEvent(
                                    index=output.block_index,
                                    delta=delta,
                                    partial=output.snapshot(),
                                )

                    elif api_type == "response.function_call_arguments.done":
                        args_str = data.get("arguments", "")
                        if current_block_type == "function_call":
                            block = output.current_block()
                            if block and block.get("type") == "toolCall":
                                block["arguments"] = parse_partial_json(args_str) if args_str else {}
                                block["partial_args"] = args_str
                                output.mark_dirty()

                    elif api_type == "response.web_search_call.in_progress":
                        yield StatusEvent(
                            status="web_search_in_progress",
                            detail="Web search in progress...",
                        )

                    elif api_type == "response.web_search_call.searching":
                        yield StatusEvent(
                            status="web_search_searching",
                            detail="Searching...",
                        )

                    elif api_type == "response.web_search_call.completed":
                        yield StatusEvent(
                            status="web_search_completed",
                            detail="Web search complete.",
                        )

                    elif api_type == "response.output_item.done":
                        item = data.get("item", {})
                        item_type = item.get("type", "")

                        if item_type == "reasoning":
                            block = output.current_block()
                            if block and block.get("type") == "thinking":
                                # Store full reasoning item as signature for replay
                                block["thinking_signature"] = json.dumps(item)
                                output.mark_dirty()
                                yield ThinkingDoneEvent(
                                    index=output.block_index,
                                    thinking=block.get("thinking", ""),
                                    partial=output.snapshot(),
                                )
                            current_block_type = None

                        elif item_type == "message":
                            block = output.current_block()
                            if block and block.get("type") == "text":
                                output.mark_dirty()
                                yield TextDoneEvent(
                                    index=output.block_index,
                                    text=block.get("text", ""),
                                    partial=output.snapshot(),
                                )
                            current_block_type = None

                        elif item_type == "function_call":
                            block = output.current_block()
                            if block and block.get("type") == "toolCall":
                                args_str = block.get("partial_args", "")
                                block["arguments"] = parse_partial_json(args_str) if args_str else {}
                                output.mark_dirty()
                                yield ToolCallDoneEvent(
                                    index=output.block_index,
                                    id=block.get("id", ""),
                                    name=block.get("name", ""),
                                    arguments=block.get("arguments", {}),
                                    partial=output.snapshot(),
                                )
                            current_block_type = None
                            partial_args = ""

                        elif item_type == "web_search_call":
                            current_block_type = None

                    elif api_type == "response.completed":
                        resp = data.get("response", {})
                        if resp.get("id"):
                            output.response_id = resp["id"]

                        usage_data = resp.get("usage", {})
                        if usage_data:
                            cached = 0
                            input_details = usage_data.get("input_tokens_details", {})
                            if input_details:
                                cached = input_details.get("cached_tokens", 0)
                            output.usage = Usage(
                                input=(usage_data.get("input_tokens", 0) or 0) - cached,
                                output=usage_data.get("output_tokens", 0) or 0,
                                cache_read=cached,
                                total_tokens=usage_data.get("total_tokens", 0) or 0,
                            )
                            output.mark_dirty()

                        status = resp.get("status", "completed")
                        output.stop_reason = _map_stop_reason(status)

                        # If we have tool calls and stop is "stop", override to "toolUse"
                        has_tool_calls = any(b.get("type") == "toolCall" for b in output.content)
                        if has_tool_calls and output.stop_reason == "stop":
                            output.stop_reason = "toolUse"
                        output.mark_dirty()

                    elif api_type == "error":
                        code = data.get("code", "unknown")
                        message = data.get("message", "Unknown error")
                        error_text = f"Error {code}: {message}"
                        output.stop_reason = "error"
                        output.error_message = error_text
                        output.mark_dirty()
                        yield ErrorEvent(
                            error=error_text,
                            stop_reason="error",
                            message=output.snapshot(),
                        )
                        return

                    elif api_type == "response.failed":
                        resp = data.get("response", {})
                        error = resp.get("error", {})
                        details = resp.get("incomplete_details", {})
                        if error:
                            error_text = f"{error.get('code', 'unknown')}: {error.get('message', 'no message')}"
                        elif details:
                            error_text = f"incomplete: {details.get('reason', 'unknown')}"
                        else:
                            error_text = "Unknown error"
                        output.stop_reason = "error"
                        output.error_message = error_text
                        output.mark_dirty()
                        yield ErrorEvent(
                            error=error_text,
                            stop_reason="error",
                            message=output.snapshot(),
                        )
                        return

                if not started:
                    raise httpx.RemoteProtocolError(
                        "Upstream stream ended before producing an event",
                    )

    except httpx.HTTPError as exc:
        if not started:
            raise
        output.stop_reason = "error"
        output.error_message = f"HTTP error: {exc}"
        output.mark_dirty()
        yield ErrorEvent(
            error=str(exc),
            stop_reason="error",
            message=output.snapshot(),
        )
        return

    # Final usage + done events
    usage = usage_with_cost(model, output.usage)
    output.usage = usage
    output.mark_dirty()
    if usage.input > 0 or usage.output > 0:
        yield UsageEvent(usage=usage)

    yield DoneEvent(
        stop_reason=output.stop_reason,
        message=output.snapshot(),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def stream(
    model: Model,
    context: Context,
    options: StreamOptions,
    auth: AuthResult,
) -> EventStream[AssistantMessage]:
    """Stream a response using the OpenAI Responses API protocol."""
    result_future: asyncio.Future[AssistantMessage] = (
        asyncio.get_event_loop().create_future()
    )

    async def _generate() -> AsyncIterator[AssistantMessageEvent]:
        last_event: AssistantMessageEvent | None = None
        async for event in _stream_impl(model, context, options, auth):
            last_event = event
            yield event

        if last_event is not None:
            if isinstance(last_event, DoneEvent) and last_event.message:
                result_future.set_result(last_event.message)
            elif isinstance(last_event, ErrorEvent) and last_event.message:
                result_future.set_result(last_event.message)
            else:
                result_future.set_result(AssistantMessage(
                    content=(),
                    stop_reason="error",
                    error_message="Stream ended without done event",
                ))
        else:
            result_future.set_result(AssistantMessage(
                content=(),
                stop_reason="error",
                error_message="Empty stream",
            ))

    return EventStream.from_async_generator(_generate(), result_future)


# ---------------------------------------------------------------------------
# Protocol class
# ---------------------------------------------------------------------------

class OpenAIResponsesProtocol(BaseProtocol):
    """OpenAI Responses wire-format protocol."""

    @property
    def id(self) -> str:
        return "openai-responses"

    @property
    def name(self) -> str:
        return "OpenAI Responses"

    def stream(
        self,
        model: Model,
        context: Context,
        options: StreamOptions,
        auth: AuthResult,
    ) -> EventStream[AssistantMessage]:
        return stream(model, context, options, auth)

    def native_auth_context(self, request: ProtocolRequest) -> Context:
        from xdog.ai.protocols.native_auth import responses_native_auth_context

        return responses_native_auth_context(request.json())

    async def request_complete(
        self,
        model: Model,
        request: ProtocolRequest,
        auth: AuthResult,
    ) -> NativeResponse:
        from xdog.ai.protocols.openai_native import RESPONSES, request_complete

        return await request_complete(RESPONSES, model, request, auth)

    async def request_stream(
        self,
        model: Model,
        request: ProtocolRequest,
        auth: AuthResult,
    ) -> NativeEventStream:
        from xdog.ai.protocols.openai_native import RESPONSES, request_stream

        return await request_stream(RESPONSES, model, request, auth)
