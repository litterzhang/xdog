"""Anthropic Messages API protocol.

Implements streaming via the ``/v1/messages`` endpoint for Claude models.
Authentication is handled by the vendor layer — this module only deals
with request building and SSE response parsing.

Key features over the OpenAI completions path for Claude models:
- Native extended thinking (``thinking`` content blocks)
- Prompt caching (``cache_control``)
- Native tool use format
- Native vision format
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx
from xdog.ai.core import AuthResult, BaseProtocol
from xdog.ai.native import NativeEventStream, NativeOperation, NativeResponse, ProtocolRequest
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
    ToolResultMessage,
    Usage,
    UsageEvent,
    UserMessage,
)
from xdog.ai.utils.cost import usage_with_cost
from xdog.ai.utils.event_stream import EventStream
from xdog.ai.utils.sanitize_unicode import sanitize_unicode

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Stop reason mapping (Anthropic -> canonical)
# ---------------------------------------------------------------------------

_STOP_REASON_MAP: dict[str, tuple[StopReason, str | None]] = {
    "end_turn": ("stop", None),
    "stop_sequence": ("stop", None),
    "max_tokens": ("length", None),
    "model_context_window_exceeded": ("length", None),
    "tool_use": ("toolUse", None),
    "pause_turn": ("stop", None),
    "refusal": ("stop", None),
    "compaction": ("stop", None),
}


def _map_stop_reason(raw: str | None) -> tuple[StopReason, str | None]:
    if raw is None:
        return ("stop", None)
    result = _STOP_REASON_MAP.get(raw)
    if result is not None:
        return result
    return ("error", f"Anthropic stop_reason: {raw}")


# ---------------------------------------------------------------------------
# Message conversion: Context -> Anthropic format
# ---------------------------------------------------------------------------

def context_to_anthropic(
    context: Context,
    model: Model,
) -> tuple[Any, list[dict[str, Any]], list[dict[str, Any]] | None]:
    """Convert a Context to Anthropic Messages API format.

    Returns (system, messages, tools).

    ``system`` is either:
    - A plain string (no caching)
    - A list of ``{"type": "text", "text": ..., "cache_control": ...}``
      blocks when ``SystemPromptBlock`` tuples are used
    """

    system: Any = None
    if context.system_prompt:
        if isinstance(context.system_prompt, str):
            system = sanitize_unicode(context.system_prompt)
        elif isinstance(context.system_prompt, tuple):
            blocks = []
            for block in context.system_prompt:
                if not block.text:
                    continue
                entry: dict[str, Any] = {
                    "type": "text",
                    "text": sanitize_unicode(block.text),
                }
                if block.cache:
                    entry["cache_control"] = {"type": "ephemeral"}
                blocks.append(entry)
            system = blocks if blocks else None

    messages: list[dict[str, Any]] = []
    for msg in (context.messages or ()):
        converted = _convert_message(msg)
        if converted is not None:
            messages.append(converted)

    tools: list[dict[str, Any]] | None = None
    if context.tools:
        tools = [_convert_tool(t) for t in context.tools]

    return system, messages, tools


def _anthropic_tool_call_id(tool_call_id: str) -> str:
    """Return the provider-neutral portion of a composite tool call ID."""
    return tool_call_id.split("|", 1)[0]


def _convert_message(msg: Message) -> dict[str, Any] | None:
    """Convert a single Message to Anthropic format."""
    if isinstance(msg, AssistantMessage):
        if msg.stop_reason in ("error", "aborted"):
            return None
        content_parts: list[dict[str, Any]] = []
        for part in msg.content:
            if isinstance(part, TextContent):
                if part.text:
                    content_parts.append({"type": "text", "text": part.text})
            elif isinstance(part, ThinkingContent):
                if part.redacted:
                    content_parts.append({
                        "type": "redacted_thinking",
                        "data": part.thinking or "",
                    })
                elif part.thinking_signature:
                    content_parts.append({
                        "type": "thinking",
                        "thinking": part.thinking,
                        "signature": part.thinking_signature,
                    })
                elif part.thinking:
                    content_parts.append({"type": "text", "text": part.thinking})
            elif isinstance(part, ToolCall):
                content_parts.append({
                    "type": "tool_use",
                    "id": _anthropic_tool_call_id(part.id),
                    "name": part.name,
                    "input": part.arguments or {},
                })
        if not content_parts:
            content_parts.append({"type": "text", "text": ""})
        return {"role": "assistant", "content": content_parts}

    if isinstance(msg, ToolResultMessage):
        result_content: list[dict[str, Any]] = []
        for result_part in msg.content:
            if isinstance(result_part, TextContent):
                result_content.append({
                    "type": "text",
                    "text": sanitize_unicode(result_part.text),
                })
            elif isinstance(result_part, ImageContent):
                result_content.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": result_part.mime_type or "image/png",
                        "data": result_part.data or "",
                    },
                })
        block: dict[str, Any] = {
            "type": "tool_result",
            "tool_use_id": _anthropic_tool_call_id(msg.tool_call_id),
            "content": result_content,
        }
        if msg.is_error:
            block["is_error"] = True
        return {"role": "user", "content": [block]}

    if isinstance(msg, UserMessage):
        content = msg.content
        if isinstance(content, str):
            return {
                "role": "user",
                "content": [{"type": "text", "text": sanitize_unicode(content)}],
            }

        parts: list[dict[str, Any]] = []
        for item in content:
            if isinstance(item, ImageContent):
                parts.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": item.mime_type or "image/png",
                        "data": item.data or "",
                    },
                })
            elif isinstance(item, TextContent):
                parts.append({"type": "text", "text": sanitize_unicode(item.text)})
        return {"role": "user", "content": parts if parts else [{"type": "text", "text": ""}]}

    return None


def _convert_tool(tool: Tool) -> dict[str, Any]:
    """Convert a Tool to Anthropic tool format."""
    return {
        "name": tool.name,
        "description": tool.description or "",
        "input_schema": tool.parameters if tool.parameters else {"type": "object", "properties": {}},
    }


# ---------------------------------------------------------------------------
# SSE parsing helpers
# ---------------------------------------------------------------------------

def _parse_sse_line(line: str) -> tuple[str | None, str | None]:
    """Parse a single SSE line into (event_type, data) or (None, None)."""
    if line.startswith("event: "):
        return ("event", line[7:].strip())
    if line.startswith("data: "):
        return ("data", line[6:])
    return (None, None)


async def _iter_sse_events(
    response: httpx.Response,
) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    """Yield (event_type, data_dict) from an Anthropic SSE stream."""
    current_event: str = "message"
    buffer: list[str] = []

    async for line_bytes in response.aiter_lines():
        line = line_bytes.strip() if isinstance(line_bytes, str) else line_bytes.decode().strip()

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
# Main stream function
# ---------------------------------------------------------------------------

async def _stream_impl(
    model: Model,
    context: Context,
    options: StreamOptions,
    auth: AuthResult,
) -> AsyncIterator[AssistantMessageEvent]:
    """Core streaming implementation for Anthropic Messages API."""

    output = MessageBuilder(model)

    system, messages, tools = context_to_anthropic(context, model)

    body: dict[str, Any] = {
        "model": model.id,
        "messages": messages,
        "stream": True,
    }

    if system:
        body["system"] = system

    if tools:
        body["tools"] = tools
        if options.parallel_tool_calls is not None:
            body["tool_choice"] = {
                "type": "auto", "disable_parallel_tool_use": not options.parallel_tool_calls,
            }

    body["max_tokens"] = 4096 if options.max_tokens is None else options.max_tokens

    if options.temperature is not None:
        body["temperature"] = options.temperature

    # Extended thinking
    thinking_level = options.thinking
    if thinking_level:
        from xdog.ai.types import ThinkingBudgets
        budgets = ThinkingBudgets()
        budget = getattr(budgets, thinking_level, None)
        if budget:
            # Anthropic requires max_tokens > budget_tokens
            body["max_tokens"] = max(body["max_tokens"], budget + 4096)

            # Models with adaptive_thinking use the new API format:
            #   thinking: {type: adaptive}, output_config: {effort: "..."}
            # Legacy models use: thinking: {type: enabled, budget_tokens: N}
            if model.adaptive_thinking:
                effort_map = {"minimal": "low", "low": "low", "medium": "medium", "high": "high", "xhigh": "high"}
                desired = effort_map.get(thinking_level, "high")
                # Clamp to the model's supported efforts
                if model.supported_efforts and desired not in model.supported_efforts:
                    desired = model.supported_efforts[-1]  # highest supported
                body["thinking"] = {"type": "adaptive"}
                body["output_config"] = {"effort": desired}
            else:
                body["thinking"] = {
                    "type": "enabled",
                    "budget_tokens": budget,
                }
            body.pop("temperature", None)

    # Build URL and headers
    base_url = model.base_url or "https://api.githubcopilot.com"
    url = f"{base_url.rstrip('/')}/v1/messages"

    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01",
        **(model.headers or {}),
        **auth.headers,
    }

    # Set auth header if not already provided.
    # Direct Anthropic API uses x-api-key; Copilot and other proxies use
    # Authorization: Bearer.  Prefer Authorization when talking to a
    # non-Anthropic base URL.
    if "Authorization" not in headers and auth.api_key:
        if model.base_url and "anthropic.com" in model.base_url:
            headers["x-api-key"] = auth.api_key
        else:
            headers["Authorization"] = f"Bearer {auth.api_key}"

    active_block_index: int = -1
    active_block_type: str | None = None
    started = False
    message_stopped = False

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as client:
            async with client.stream("POST", url, json=body, headers=headers) as response:
                if response.status_code != 200:
                    error_body = await response.aread()
                    error_text = error_body.decode("utf-8", errors="replace")
                    logger.error(
                        "Anthropic API error: HTTP %d %s",
                        response.status_code, error_text[:1000],
                    )
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
                    if data.get("type", event_type) == "message_stop":
                        message_stopped = True
                    events = _handle_sse_event(
                        event_type, data, output,
                        active_block_index, active_block_type,
                    )
                    for evt_or_update in events:
                        if isinstance(evt_or_update, tuple):
                            active_block_index, active_block_type = evt_or_update
                            continue
                        if isinstance(evt_or_update, ErrorEvent):
                            yield evt_or_update
                            return
                        if not started:
                            started = True
                            yield StartEvent(partial=output.snapshot())
                        yield evt_or_update

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

    if not message_stopped:
        output.stop_reason = "error"
        output.error_message = "Upstream stream ended without message_stop"
        output.mark_dirty()
        yield ErrorEvent(
            error=output.error_message,
            stop_reason="error",
            message=output.snapshot(),
        )
        return

    # Final done event
    usage = usage_with_cost(model, output.usage)
    output.usage = usage
    output.mark_dirty()
    if usage.input > 0 or usage.output > 0:
        yield UsageEvent(usage=usage)

    yield DoneEvent(
        stop_reason=output.stop_reason,
        message=output.snapshot(),
    )


def _anthropic_usage(
    input_tokens: int,
    output_tokens: int,
    cache_read: int,
    cache_write: int,
) -> Usage:
    """Build a Usage with ``total_tokens`` populated.

    Anthropic reports cached tokens separately from ``input_tokens``, so the
    total is the sum of all four buckets.
    """
    return Usage(
        input=input_tokens,
        output=output_tokens,
        cache_read=cache_read,
        cache_write=cache_write,
        total_tokens=input_tokens + output_tokens + cache_read + cache_write,
    )


def _handle_sse_event(
    event_type: str,
    data: dict[str, Any],
    output: MessageBuilder,
    active_block_index: int,
    active_block_type: str | None,
) -> list[Any]:
    """Process a single SSE event. Returns list of events and/or state updates."""
    results: list[Any] = []
    anthropic_type = data.get("type", event_type)

    if anthropic_type == "message_start":
        msg = data.get("message", {})
        output.response_id = msg.get("id")
        usage_data = msg.get("usage", {})
        if usage_data:
            output.usage = _anthropic_usage(
                input_tokens=usage_data.get("input_tokens", 0),
                output_tokens=usage_data.get("output_tokens", 0),
                cache_read=usage_data.get("cache_read_input_tokens", 0),
                cache_write=usage_data.get("cache_creation_input_tokens", 0),
            )
            output.mark_dirty()

    elif anthropic_type == "content_block_start":
        index = data.get("index", active_block_index + 1)
        content_block = data.get("content_block", {})
        block_type = content_block.get("type", "text")

        if block_type == "text":
            output.push_block({"type": "text", "text": content_block.get("text", "")})
            normalized_index = output.block_index
            results.append(TextStartEvent(index=normalized_index, partial=output.snapshot()))
            results.append((normalized_index, "text"))

        elif block_type == "thinking":
            output.push_block({
                "type": "thinking",
                "thinking": content_block.get("thinking", ""),
                "thinking_signature": None,
                "redacted": False,
            })
            normalized_index = output.block_index
            results.append(ThinkingStartEvent(index=normalized_index, partial=output.snapshot()))
            results.append((normalized_index, "thinking"))

        elif block_type == "redacted_thinking":
            output.push_block({
                "type": "thinking",
                "thinking": content_block.get("data", ""),
                "thinking_signature": None,
                "redacted": True,
            })
            normalized_index = output.block_index
            results.append(ThinkingStartEvent(index=normalized_index, partial=output.snapshot()))
            results.append((normalized_index, "thinking"))

        elif block_type == "tool_use":
            tool_id = content_block.get("id", "")
            tool_name = content_block.get("name", "")
            output.push_block({
                "type": "toolCall",
                "id": tool_id,
                "name": tool_name,
                "arguments": {},
                "partial_args": "",
            })
            normalized_index = output.block_index
            results.append(ToolCallStartEvent(
                index=normalized_index, id=tool_id, name=tool_name,
                partial=output.snapshot(),
            ))
            results.append((normalized_index, "tool_use"))

    elif anthropic_type == "content_block_delta":
        delta = data.get("delta", {})
        delta_type = delta.get("type", "")

        if delta_type == "text_delta":
            text = delta.get("text", "")
            if text and output.content and active_block_type == "text":
                block = output.content[active_block_index]
                block["text"] = block.get("text", "") + text
                output.mark_dirty()
                results.append(TextDeltaEvent(
                    index=active_block_index, delta=text,
                    partial=output.snapshot(),
                ))

        elif delta_type == "thinking_delta":
            thinking = delta.get("thinking", "")
            if thinking and output.content and active_block_type == "thinking":
                block = output.content[active_block_index]
                block["thinking"] = block.get("thinking", "") + thinking
                output.mark_dirty()
                results.append(ThinkingDeltaEvent(
                    index=active_block_index, delta=thinking,
                    partial=output.snapshot(),
                ))

        elif delta_type == "signature_delta":
            signature = delta.get("signature", "")
            if signature and output.content and active_block_type == "thinking":
                block = output.content[active_block_index]
                existing = block.get("thinking_signature") or ""
                block["thinking_signature"] = existing + signature
                output.mark_dirty()

        elif delta_type == "input_json_delta":
            partial_json = delta.get("partial_json", "")
            if partial_json and output.content and active_block_type == "tool_use":
                block = output.content[active_block_index]
                block["partial_args"] = block.get("partial_args", "") + partial_json
                try:
                    block["arguments"] = json.loads(block["partial_args"])
                except json.JSONDecodeError:
                    pass
                output.mark_dirty()
                results.append(ToolCallDeltaEvent(
                    index=active_block_index, delta=partial_json,
                    partial=output.snapshot(),
                ))

    elif anthropic_type == "content_block_stop":
        index = active_block_index
        if output.content and 0 <= index < len(output.content):
            block = output.content[index]
            btype = block.get("type")
            if btype == "text":
                results.append(TextDoneEvent(
                    index=index, text=block.get("text", ""),
                    text_signature=block.get("text_signature"),
                    partial=output.snapshot(),
                ))
            elif btype == "thinking":
                results.append(ThinkingDoneEvent(
                    index=index, thinking=block.get("thinking", ""),
                    thinking_signature=block.get("thinking_signature"),
                    redacted=block.get("redacted", False),
                    partial=output.snapshot(),
                ))
            elif btype == "toolCall":
                partial_args = block.get("partial_args", "")
                if partial_args:
                    try:
                        block["arguments"] = json.loads(partial_args)
                    except json.JSONDecodeError:
                        pass
                    output.mark_dirty()
                results.append(ToolCallDoneEvent(
                    index=index, id=block.get("id", ""),
                    name=block.get("name", ""),
                    arguments=block.get("arguments", {}),
                    partial=output.snapshot(),
                ))
        results.append((-1, None))

    elif anthropic_type == "message_delta":
        delta = data.get("delta", {})
        stop_reason_raw = delta.get("stop_reason")
        if stop_reason_raw:
            reason, error_msg = _map_stop_reason(stop_reason_raw)
            output.stop_reason = reason
            if error_msg:
                output.error_message = error_msg
            output.mark_dirty()

        usage_data = data.get("usage", {})
        if usage_data:
            output_tokens = usage_data.get("output_tokens", 0)
            if output_tokens:
                output.usage = _anthropic_usage(
                    input_tokens=output.usage.input,
                    output_tokens=output_tokens,
                    cache_read=output.usage.cache_read,
                    cache_write=output.usage.cache_write,
                )
                output.mark_dirty()

    elif anthropic_type == "message_stop":
        pass

    elif anthropic_type == "error":
        error_data = data.get("error", {})
        error_msg = error_data.get("message", str(data))
        output.stop_reason = "error"
        output.error_message = error_msg
        output.mark_dirty()
        results.append(ErrorEvent(
            error=error_msg,
            stop_reason="error",
            message=output.snapshot(),
        ))

    return results


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def stream(
    model: Model,
    context: Context,
    options: StreamOptions,
    auth: AuthResult,
) -> EventStream[AssistantMessage]:
    """Stream a response using the Anthropic Messages API protocol."""
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

class AnthropicMessagesProtocol(BaseProtocol):
    """Anthropic Messages wire-format protocol."""

    @property
    def id(self) -> str:
        return "anthropic-messages"

    @property
    def name(self) -> str:
        return "Anthropic Messages"

    def stream(
        self,
        model: Model,
        context: Context,
        options: StreamOptions,
        auth: AuthResult,
    ) -> EventStream[AssistantMessage]:
        return stream(model, context, options, auth)

    def supports_native_operation(self, operation: NativeOperation) -> bool:
        return operation in (NativeOperation.GENERATE, NativeOperation.COUNT_TOKENS)

    def native_auth_context(self, request: ProtocolRequest) -> Context:
        from xdog.ai.protocols.native_auth import anthropic_native_auth_context

        return anthropic_native_auth_context(request.json())

    async def request_complete(
        self,
        model: Model,
        request: ProtocolRequest,
        auth: AuthResult,
    ) -> NativeResponse:
        from xdog.ai.protocols.anthropic_native import request_complete

        return await request_complete(model, request, auth)

    async def request_stream(
        self,
        model: Model,
        request: ProtocolRequest,
        auth: AuthResult,
    ) -> NativeEventStream:
        from xdog.ai.protocols.anthropic_native import request_stream

        return await request_stream(model, request, auth)
