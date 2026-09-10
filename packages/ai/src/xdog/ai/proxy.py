"""API proxy for Anthropic Messages and OpenAI generation endpoints.

Native-capable models receive lossless Messages, Responses, or Chat Completions
requests. Responses falls back to a strict stateless adapter when its native
protocol is unavailable; Chat Completions requires native model support.

Usage::

    python -m ai.proxy --port 8082
    python -m ai.proxy --port 8082 --provider copilot

Then point any Anthropic SDK client at ``http://localhost:8082``::

    from anthropic import Anthropic
    client = Anthropic(api_key="dummy", base_url="http://localhost:8082")
    msg = client.messages.create(
        model="claude-sonnet-4-20250514",
        messages=[{"role": "user", "content": "Hello"}],
        max_tokens=100,
    )

Zero external dependencies beyond what the ai package already uses.
Uses raw asyncio TCP server with manual HTTP/1.1 parsing.
"""
from __future__ import annotations

import argparse
import asyncio
import errno
import json
import logging
import ssl
import uuid
from collections.abc import AsyncIterator, Iterable, Iterator, Mapping
from typing import Any, Literal

import httpx
from xdog.ai import proxy_anthropic, proxy_openai, proxy_responses
from xdog.ai.native import NativeHTTPError, NativeResponse, NativeSSEEvent
from xdog.ai.proxy_anthropic_diagnostics import target_projection_issues
from xdog.ai.types import (
    AssistantMessage,
    AuthExpiredError,
    Context,
    DoneEvent,
    ErrorEvent,
    ImageContent,
    StreamOptions,
    TextContent,
    TextDoneEvent,
    ThinkingBudgets,
    ThinkingContent,
    ThinkingDoneEvent,
    Tool,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)

logger = logging.getLogger(__name__)

_INITIAL_STREAM_RETRY_DELAYS = (0.05, 0.15)
_UPSTREAM_TRANSPORT_ERROR = "Upstream connection failed; retry later"
ProxyFormat = Literal["anthropic-messages", "openai-responses", "openai-completions"]

_RETRYABLE_NETWORK_ERRNOS = {
    errno.ECONNABORTED,
    errno.ECONNREFUSED,
    errno.ECONNRESET,
    errno.EHOSTUNREACH,
    errno.ENETUNREACH,
    errno.ETIMEDOUT,
    errno.EPIPE,
}


# ---------------------------------------------------------------------------
# Thinking budget → level mapping (derived from ThinkingBudgets)
# ---------------------------------------------------------------------------

def _budget_to_thinking_level(budget: int) -> str:
    """Map a budget_tokens value to a ThinkingLevel string.

    Uses the thresholds defined in :class:`ThinkingBudgets` so they stay
    in sync automatically.  Picks the highest level whose budget is ≤ the
    requested budget.
    """
    budgets = ThinkingBudgets()
    # Ordered from highest to lowest so we return the best match
    levels = [
        ("xhigh", budgets.xhigh),
        ("high", budgets.high),
        ("medium", budgets.medium),
        ("low", budgets.low),
        ("minimal", budgets.minimal),
    ]
    for level, threshold in levels:
        if budget >= threshold:
            return level
    return "minimal"


# ---------------------------------------------------------------------------
# Request parsing: Anthropic JSON → ai types
# ---------------------------------------------------------------------------

def parse_request(body: dict[str, Any]) -> tuple[str, Context, StreamOptions, bool]:
    """Convert an Anthropic Messages API request body to ai types.

    Returns (model_id, context, options, is_stream).
    """
    model_id = body.get("model", "")
    is_stream = body.get("stream", False)

    # System prompt
    system_raw = body.get("system")
    if isinstance(system_raw, str):
        system_prompt = system_raw
    elif isinstance(system_raw, list):
        # List of content blocks — concatenate text
        system_prompt = "\n\n".join(
            b.get("text", "") for b in system_raw if b.get("type") == "text"
        )
    else:
        system_prompt = None

    # Messages
    messages = tuple(
        parsed
        for m in body.get("messages", [])
        for parsed in _parse_message(m)
    )

    # Tools
    tools = None
    raw_tools = body.get("tools")
    if raw_tools:
        tools = tuple(_parse_tool(t) for t in raw_tools)

    context = Context(
        system_prompt=system_prompt,
        messages=messages,
        tools=tools,
    )

    # Stream options
    thinking_raw = body.get("thinking")
    thinking_level = None
    if isinstance(thinking_raw, dict):
        thinking_type = thinking_raw.get("type", "")
        if thinking_type == "enabled":
            budget = thinking_raw.get("budget_tokens", 0)
            thinking_level = _budget_to_thinking_level(budget)
        elif thinking_type == "adaptive":
            # Adaptive thinking: effort level comes from output_config
            output_config = body.get("output_config", {})
            effort = output_config.get("effort", "medium") if isinstance(output_config, dict) else "medium"
            # Map Anthropic effort to our thinking level
            effort_to_level = {"low": "low", "medium": "medium", "high": "high"}
            thinking_level = effort_to_level.get(effort, "medium")

    options = StreamOptions(
        max_tokens=body.get("max_tokens"),
        temperature=body.get("temperature"),
        thinking=thinking_level,  # type: ignore[arg-type]  # the proxy validates this upstream
    )

    return model_id, context, options, is_stream


def _parse_message(msg: dict[str, Any]) -> list[UserMessage | AssistantMessage | ToolResultMessage]:
    """Parse a single Anthropic message dict into a list of ai Messages.

    A single Anthropic ``user`` message may bundle multiple ``tool_result``
    blocks (parallel tool calls) alongside text/image parts. Each
    ``tool_result`` becomes its own :class:`ToolResultMessage`; any remaining
    text/image parts become a single :class:`UserMessage`. Returning a list
    ensures every ``tool_use`` id issued by the assistant is answered, which
    the upstream Anthropic API strictly requires.
    """
    role = msg.get("role", "")
    content = msg.get("content", "")

    if role == "user":
        if isinstance(content, str):
            return [UserMessage(content=content)]
        # Content blocks
        parts: list[Any] = []
        tool_results: list[ToolResultMessage] = []
        for block in content:
            btype = block.get("type", "")
            if btype == "text":
                parts.append(TextContent(text=block.get("text", "")))
            elif btype == "image":
                source = block.get("source", {})
                parts.append(ImageContent(
                    data=source.get("data", ""),
                    mime_type=source.get("media_type", "image/png"),
                ))
            elif btype == "tool_result":
                # Tool results are wrapped as ToolResultMessage
                result_content = block.get("content", "")
                if isinstance(result_content, str):
                    tc: tuple[TextContent, ...] = (TextContent(text=result_content),)
                elif isinstance(result_content, list):
                    tc = tuple(
                        TextContent(text=b.get("text", ""))
                        for b in result_content if b.get("type") == "text"
                    )
                else:
                    tc = (TextContent(text=str(result_content)),)
                tool_results.append(ToolResultMessage(
                    tool_call_id=block.get("tool_use_id", ""),
                    tool_name="",
                    content=tc,
                    is_error=block.get("is_error", False),
                ))
        # Emit every tool_result (parallel tool calls), then any text/image parts.
        out: list[UserMessage | AssistantMessage | ToolResultMessage] = list(tool_results)
        if parts:
            out.append(UserMessage(content=tuple(parts)))
        if not out:
            out.append(UserMessage(content=""))
        return out

    if role == "assistant":
        if isinstance(content, str):
            return [AssistantMessage(content=(TextContent(text=content),))]
        parts_out: list[Any] = []
        for block in content:
            btype = block.get("type", "")
            if btype == "text":
                parts_out.append(TextContent(text=block.get("text", "")))
            elif btype == "thinking":
                parts_out.append(ThinkingContent(
                    thinking=block.get("thinking", ""),
                    thinking_signature=block.get("signature"),
                ))
            elif btype == "tool_use":
                parts_out.append(ToolCall(
                    id=block.get("id", ""),
                    name=block.get("name", ""),
                    arguments=block.get("input", {}),
                ))
        return [AssistantMessage(content=tuple(parts_out))]

    return [UserMessage(content=str(content))]


def _parse_tool(tool: dict[str, Any]) -> Tool:
    """Parse an Anthropic tool definition."""
    return Tool(
        name=tool.get("name", ""),
        description=tool.get("description", ""),
        parameters=tool.get("input_schema", {}),
    )


# ---------------------------------------------------------------------------
# Response formatting: ai events → Anthropic SSE
# ---------------------------------------------------------------------------

def _sse_line(event: str, data: dict[str, Any]) -> bytes:
    """Format a single SSE event."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()


def _parse_upstream_error(error_message: str) -> tuple[int, dict[str, Any]]:
    """Recover an upstream Anthropic error envelope from an internal error."""
    status = 500
    payload_text = error_message
    if error_message.startswith("HTTP "):
        status_text, separator, payload_text = error_message[5:].partition(": ")
        if separator:
            try:
                status = int(status_text)
            except ValueError:
                status = 500

    try:
        payload = json.loads(payload_text)
    except (json.JSONDecodeError, TypeError):
        payload = None

    if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
        return status, payload

    return status, {
        "type": "error",
        "error": {"type": "api_error", "message": error_message},
    }


def _map_stop_reason_to_anthropic(reason: str) -> str:
    """Map internal stop reasons to Anthropic API format."""
    return {
        "stop": "end_turn",
        "toolUse": "tool_use",
        "length": "max_tokens",
        "error": "end_turn",
        "aborted": "end_turn",
    }.get(reason, reason)


def _event_usage(event: Any) -> Any:
    """Return the Usage carried by a streaming event, if any."""
    message = getattr(event, "partial", None) or getattr(event, "message", None)
    return getattr(message, "usage", None)


def _message_start_line(msg_id: str, model_id: str, usage: Any) -> bytes:
    """Build the Anthropic ``message_start`` SSE line with real token usage."""
    return _sse_line("message_start", {
        "type": "message_start",
        "message": {
            "id": msg_id,
            "type": "message",
            "role": "assistant",
            "content": [],
            "model": model_id,
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {
                "input_tokens": usage.input if usage else 0,
                "output_tokens": usage.output if usage else 0,
                "cache_read_input_tokens": usage.cache_read if usage else 0,
                "cache_creation_input_tokens": usage.cache_write if usage else 0,
            },
        },
    })


async def stream_to_sse(
    event_stream: Any,
    model_id: str,
) -> AsyncIterator[bytes]:
    """Convert an ai EventStream to Anthropic SSE bytes.

    Yields SSE-formatted byte chunks matching the Anthropic Messages API
    streaming protocol.
    """
    msg_id = f"msg_{uuid.uuid4().hex[:24]}"
    block_index = -1
    pending_message_start = False

    async for event in event_stream:
        etype = event.type

        if etype == "start":
            # Defer message_start until upstream usage is known — clients rely
            # on its input/cache token counts to track context growth.
            pending_message_start = True
            continue

        if pending_message_start:
            pending_message_start = False
            yield _message_start_line(msg_id, model_id, _event_usage(event))

        if etype == "text_start":
            block_index = event.index
            yield _sse_line("content_block_start", {
                "type": "content_block_start",
                "index": block_index,
                "content_block": {"type": "text", "text": ""},
            })

        elif etype == "text_delta":
            yield _sse_line("content_block_delta", {
                "type": "content_block_delta",
                "index": event.index,
                "delta": {"type": "text_delta", "text": event.delta},
            })

        elif etype == "text_done":
            # Emit signature delta if present (before content_block_stop)
            assert isinstance(event, TextDoneEvent)
            if event.text_signature:
                yield _sse_line("content_block_delta", {
                    "type": "content_block_delta",
                    "index": event.index,
                    "delta": {
                        "type": "signature_delta",
                        "signature": event.text_signature,
                    },
                })
            yield _sse_line("content_block_stop", {
                "type": "content_block_stop",
                "index": event.index,
            })

        elif etype == "thinking_start":
            block_index = event.index
            yield _sse_line("content_block_start", {
                "type": "content_block_start",
                "index": block_index,
                "content_block": {"type": "thinking", "thinking": ""},
            })

        elif etype == "thinking_delta":
            yield _sse_line("content_block_delta", {
                "type": "content_block_delta",
                "index": event.index,
                "delta": {"type": "thinking_delta", "thinking": event.delta},
            })

        elif etype == "thinking_done":
            # Emit signature delta if present (before content_block_stop)
            assert isinstance(event, ThinkingDoneEvent)
            if event.thinking_signature:
                yield _sse_line("content_block_delta", {
                    "type": "content_block_delta",
                    "index": event.index,
                    "delta": {
                        "type": "signature_delta",
                        "signature": event.thinking_signature,
                    },
                })
            yield _sse_line("content_block_stop", {
                "type": "content_block_stop",
                "index": event.index,
            })

        elif etype == "tool_call_start":
            block_index = event.index
            yield _sse_line("content_block_start", {
                "type": "content_block_start",
                "index": block_index,
                "content_block": {
                    "type": "tool_use",
                    "id": event.id,
                    "name": event.name,
                    "input": {},
                },
            })

        elif etype == "tool_call_delta":
            yield _sse_line("content_block_delta", {
                "type": "content_block_delta",
                "index": event.index,
                "delta": {
                    "type": "input_json_delta",
                    "partial_json": event.delta,
                },
            })

        elif etype == "tool_call_done":
            yield _sse_line("content_block_stop", {
                "type": "content_block_stop",
                "index": event.index,
            })

        elif etype == "usage":
            # Cumulative usage is forwarded in message_delta below, which the
            # OpenAI-backed protocols only know once the stream completes.
            pass

        elif etype == "done":
            assert isinstance(event, DoneEvent)
            msg = event.message
            stop_reason = _map_stop_reason_to_anthropic(
                msg.stop_reason if msg else "end_turn"
            )
            usage = msg.usage if msg else None

            yield _sse_line("message_delta", {
                "type": "message_delta",
                "delta": {
                    "stop_reason": stop_reason,
                    "stop_sequence": None,
                },
                "usage": {
                    "input_tokens": usage.input if usage else 0,
                    "output_tokens": usage.output if usage else 0,
                    "cache_read_input_tokens": usage.cache_read if usage else 0,
                    "cache_creation_input_tokens": usage.cache_write if usage else 0,
                },
            })
            yield _sse_line("message_stop", {"type": "message_stop"})

        elif etype == "error":
            assert isinstance(event, ErrorEvent)
            _, error = _parse_upstream_error(event.error)
            yield _sse_line("error", error)
            return


def format_non_streaming_response(
    msg: AssistantMessage,
    model_id: str,
) -> dict[str, Any]:
    """Format an AssistantMessage as an Anthropic non-streaming JSON response."""
    content: list[dict[str, Any]] = []
    for part in msg.content:
        if isinstance(part, TextContent):
            block: dict[str, Any] = {"type": "text", "text": part.text}
            if part.text_signature:
                block["signature"] = part.text_signature
            content.append(block)
        elif isinstance(part, ThinkingContent):
            if part.redacted:
                content.append({"type": "redacted_thinking", "data": part.thinking or ""})
            else:
                tblock: dict[str, Any] = {"type": "thinking", "thinking": part.thinking or ""}
                if part.thinking_signature:
                    tblock["signature"] = part.thinking_signature
                content.append(tblock)
        elif isinstance(part, ToolCall):
            content.append({
                "type": "tool_use",
                "id": part.id,
                "name": part.name,
                "input": part.arguments or {},
            })

    stop_reason = _map_stop_reason_to_anthropic(msg.stop_reason or "end_turn")

    usage = msg.usage
    return {
        "id": f"msg_{uuid.uuid4().hex[:24]}",
        "type": "message",
        "role": "assistant",
        "content": content,
        "model": model_id,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage.input if usage else 0,
            "output_tokens": usage.output if usage else 0,
            "cache_creation_input_tokens": usage.cache_write if usage else 0,
            "cache_read_input_tokens": usage.cache_read if usage else 0,
        },
    }


# ---------------------------------------------------------------------------
# HTTP server (raw asyncio, zero dependencies)
# ---------------------------------------------------------------------------

async def _read_http_request(
    reader: asyncio.StreamReader,
) -> tuple[str, str, dict[str, str], bytes] | None:
    """Read and parse an HTTP/1.1 request. Returns (method, path, headers, body)."""
    try:
        request_line = await asyncio.wait_for(reader.readline(), timeout=30)
    except (asyncio.TimeoutError, ConnectionError):
        return None

    if not request_line:
        return None

    parts = request_line.decode("utf-8", errors="replace").strip().split(" ")
    if len(parts) < 2:
        return None

    method = parts[0]
    path = parts[1]

    headers: dict[str, str] = {}
    while True:
        line = await reader.readline()
        if not line or line == b"\r\n":
            break
        decoded = line.decode("utf-8", errors="replace").strip()
        if ":" in decoded:
            key, value = decoded.split(":", 1)
            name = key.strip().lower()
            normalized_value = value.strip()
            if name == "anthropic-beta" and name in headers:
                headers[name] = f"{headers[name]},{normalized_value}"
            else:
                headers[name] = normalized_value

    body = b""
    if "chunked" not in headers.get("transfer-encoding", "").lower():
        content_length = int(headers.get("content-length", "0"))
        if content_length > 0:
            body = await reader.readexactly(content_length)

    return method, path, headers, body


def _http_response(
    status: int,
    status_text: str,
    body: bytes,
    content_type: str = "application/json",
    extra_headers: Mapping[str, str] | Iterable[tuple[str, str]] | None = None,
) -> bytes:
    """Build a complete HTTP/1.1 response."""
    extra_items = extra_headers.items() if isinstance(extra_headers, Mapping) else extra_headers or ()
    filtered_extra = tuple(
        (name, value)
        for name, value in extra_items
        if name.lower() not in {"content-type", "content-length", "connection", "transfer-encoding"}
    )
    headers = [
        f"HTTP/1.1 {status} {status_text}",
        f"Content-Type: {content_type}",
        f"Content-Length: {len(body)}",
        "Access-Control-Allow-Origin: *",
        "Access-Control-Allow-Headers: *",
        "Access-Control-Allow-Methods: POST, OPTIONS",
        "Connection: close",
        *(f"{name}: {value}" for name, value in filtered_extra),
    ]
    header_block = "\r\n".join(headers) + "\r\n\r\n"
    return header_block.encode() + body


def _sse_response_headers(
    extra_headers: dict[str, str] | None = None,
    *,
    content_type: str = "text/event-stream",
    status: int = 200,
    status_text: str = "OK",
) -> bytes:
    """Build HTTP headers for an SSE streaming response closed at EOF."""
    headers = [
        f"HTTP/1.1 {status} {status_text}",
        f"Content-Type: {content_type}",
        "Cache-Control: no-cache",
        "Connection: close",
        "Access-Control-Allow-Origin: *",
        "Access-Control-Allow-Headers: *",
        "Access-Control-Allow-Methods: POST, OPTIONS",
    ]
    if extra_headers:
        headers.extend(f"{name}: {value}" for name, value in extra_headers.items())
    return ("\r\n".join(headers) + "\r\n\r\n").encode()


def _exception_chain(exc: BaseException) -> Iterator[BaseException]:
    """Yield an exception and its explicit or implicit causes once."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _is_retryable_transport_error(exc: BaseException) -> bool:
    """Return whether an initial upstream connection failure can be retried."""
    for current in _exception_chain(exc):
        if isinstance(current, (httpx.TransportError, ssl.SSLError)):
            return True
        if isinstance(current, OSError) and current.errno in _RETRYABLE_NETWORK_ERRNOS:
            return True
    return False


async def _prepend_events(
    first_events: tuple[Any, ...],
    remainder: AsyncIterator[Any],
) -> AsyncIterator[Any]:
    """Replay primed events before the rest of their stream."""
    for event in first_events:
        yield event
    async for event in remainder:
        yield event


def _openai_format(format: ProxyFormat) -> bool:
    return format in ("openai-responses", "openai-completions")


def _error_payload(
    format: ProxyFormat,
    message: str,
    *,
    kind: str,
    param: str | None = None,
) -> dict[str, Any]:
    if _openai_format(format):
        return proxy_openai.error_body(message, kind=kind, param=param)
    return {"type": "error", "error": {"type": kind, "message": message}}


def _error_response(
    status: int,
    status_text: str,
    message: str,
    *,
    format: ProxyFormat = "anthropic-messages",
) -> bytes:
    kind = "server_error" if _openai_format(format) else "api_error"
    return _http_response(
        status,
        status_text,
        json.dumps(_error_payload(format, message, kind=kind)).encode(),
    )


async def _close_writer(writer: asyncio.StreamWriter) -> None:
    """Close a response writer and wait for socket cleanup."""
    writer.close()
    try:
        await writer.wait_closed()
    except (BrokenPipeError, ConnectionResetError):
        pass


async def _handle_connection(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    provider: Any,
    api_key: str = "",
) -> None:
    """Handle a single HTTP connection."""
    format: ProxyFormat = "anthropic-messages"
    try:
        req = await _read_http_request(reader)
        if req is None:
            writer.close()
            return

        method, path, headers, body = req

        # Strip query string for routing
        route_path = path.split("?")[0]
        if route_path == "/v1/responses":
            format = "openai-responses"
        elif route_path == "/v1/chat/completions":
            format = "openai-completions"

        logger.debug("Request: %s %s", method, path)

        # CORS preflight
        if method == "OPTIONS":
            writer.write(_http_response(204, "No Content", b""))
            await writer.drain()
            writer.close()
            return

        # Health check (no auth required)
        if route_path in ("/", "/health"):
            resp = json.dumps({"status": "ok"}).encode()
            writer.write(_http_response(200, "OK", resp))
            await writer.drain()
            writer.close()
            return

        # API key authentication
        if api_key:
            auth_header = headers.get("authorization", "")
            x_api_key = headers.get("x-api-key", "")
            provided = ""
            if auth_header.startswith("Bearer "):
                provided = auth_header[7:]
            elif x_api_key:
                provided = x_api_key
            if provided != api_key:
                resp = json.dumps(_error_payload(
                    format,
                    "Invalid API key",
                    kind="authentication_error",
                )).encode()
                writer.write(_http_response(401, "Unauthorized", resp))
                await writer.drain()
                writer.close()
                return

        if (
            method == "POST"
            and route_path in (
                "/v1/messages",
                "/v1/messages/count_tokens",
                "/v1/responses",
                "/v1/chat/completions",
            )
            and "chunked" in headers.get("transfer-encoding", "").lower()
        ):
            resp = json.dumps(_error_payload(
                format,
                "Chunked request bodies are unsupported",
                kind="invalid_request_error",
            )).encode()
            writer.write(_http_response(400, "Bad Request", resp))
            await writer.drain()
            await _close_writer(writer)
            return

        # GET /v1/models — list available models
        if method == "GET" and route_path == "/v1/models":
            models = _list_models(provider)
            resp = json.dumps(models).encode()
            writer.write(_http_response(200, "OK", resp))
            await writer.drain()
            writer.close()
            return

        # GET /v1/models/{model_id} — single model lookup (Claude Code validates on startup)
        if method == "GET" and route_path.startswith("/v1/models/"):
            model_id = route_path[len("/v1/models/"):]
            model_obj = _get_model(provider, model_id)
            if model_obj is not None:
                resp = json.dumps(model_obj).encode()
                writer.write(_http_response(200, "OK", resp))
            else:
                resp = json.dumps({
                    "type": "error",
                    "error": {"type": "not_found_error", "message": f"Model not found: {model_id}"},
                }).encode()
                writer.write(_http_response(404, "Not Found", resp))
            await writer.drain()
            writer.close()
            return

        # Anthropic token counting never enters a generation path.
        if method == "POST" and route_path == "/v1/messages/count_tokens":
            await _handle_count_tokens(
                writer,
                provider,
                body,
                request_headers=headers,
            )
            return

        # Generation endpoints
        if method == "POST" and route_path == "/v1/messages":
            await _handle_messages(
                writer,
                provider,
                body,
                format="anthropic-messages",
                request_headers=headers,
            )
            return

        if method == "POST" and route_path in ("/v1/responses", "/v1/chat/completions"):
            await _handle_openai(
                writer,
                provider,
                body,
                format=format,
                request_headers=headers,
            )
            return

        # Stub: /v1/organizations — Claude Code checks this at startup
        if route_path == "/v1/organizations":
            resp = json.dumps({"data": []}).encode()
            writer.write(_http_response(200, "OK", resp))
            await writer.drain()
            writer.close()
            return

        # Stub: /v1/dashboard — Claude Code billing/usage check
        if route_path.startswith("/v1/dashboard"):
            resp = json.dumps({}).encode()
            writer.write(_http_response(200, "OK", resp))
            await writer.drain()
            writer.close()
            return

        # Not found — respond immediately so clients don't hang
        logger.debug("Unhandled route: %s %s", method, path)
        resp = json.dumps(_error_payload(
            format,
            f"Not found: {method} {path}",
            kind="not_found_error" if _openai_format(format) else "not_found",
        )).encode()
        writer.write(_http_response(404, "Not Found", resp))
        await writer.drain()
        writer.close()

    except ConnectionResetError:
        # Client closed connection before we finished writing — harmless
        logger.debug("Client disconnected (connection reset)")
        try:
            writer.close()
        except Exception:
            pass

    except AuthExpiredError as exc:
        # Not an internal error, and not the proxy's to fix: report it as the
        # 401 it is, with the command that resolves it. Rendered as a 500 the
        # client shows "API error" and the user has no way to know that one
        # login would fix it.
        logger.error("%s", exc)
        try:
            resp = json.dumps(_error_payload(
                format,
                str(exc),
                kind="authentication_error",
            )).encode()
            writer.write(_http_response(401, "Unauthorized", resp))
            await writer.drain()
        except Exception:
            pass
        finally:
            writer.close()

    except Exception:
        logger.exception("Proxy request failed")
        try:
            resp = json.dumps(_error_payload(
                format,
                "Proxy request failed",
                kind="server_error" if _openai_format(format) else "api_error",
            )).encode()
            writer.write(_http_response(500, "Internal Server Error", resp))
            await writer.drain()
        except Exception:
            pass
        finally:
            writer.close()


def _list_models(provider: Any) -> dict[str, Any]:
    """Build a model list response matching the Anthropic /v1/models format."""
    models = provider.models()
    data = []
    for m in models:
        data.append({
            "id": m.id,
            "type": "model",
            "display_name": m.name or m.id,
            "created_at": "2025-01-01T00:00:00Z",
        })
    return {
        "data": data,
        "has_more": False,
        "first_id": data[0]["id"] if data else None,
        "last_id": data[-1]["id"] if data else None,
    }


def _get_model(provider: Any, model_id: str) -> dict[str, Any] | None:
    """Look up a single model by ID, matching the Anthropic GET /v1/models/{id} format."""
    m = provider.model(model_id)
    if m is None:
        return None
    return {
        "id": m.id,
        "type": "model",
        "display_name": m.name or m.id,
        "created_at": "2025-01-01T00:00:00Z",
    }


def _model_protocol(provider: Any, model_id: str) -> str | None:
    model_lookup = getattr(provider, "model", None)
    if not callable(model_lookup):
        return None
    model = model_lookup(model_id)
    if model is None:
        return None
    preferred = getattr(model, "preferred_protocol", None)
    api = getattr(model, "api", None)
    return preferred if isinstance(preferred, str) else api if isinstance(api, str) else None


def _native_anthropic_capable(provider: Any, model_id: str) -> bool:
    complete = getattr(provider, "request_complete", None)
    stream = getattr(provider, "request_stream", None)
    if not callable(complete) or not callable(stream):
        return False
    model_lookup = getattr(provider, "model", None)
    if not callable(model_lookup):
        return True
    model = model_lookup(model_id)
    if model is None:
        return False
    supported = getattr(model, "supported_protocols", None)
    if supported is None:
        return True
    return "anthropic-messages" in supported


def _native_openai_capable(
    provider: Any,
    model_id: str,
    protocol: proxy_openai.OpenAIProtocol,
) -> bool:
    """Return whether model metadata advertises exact native generation."""
    if not callable(getattr(provider, "request_complete", None)) or not callable(
        getattr(provider, "request_stream", None),
    ):
        return False
    model_lookup = getattr(provider, "model", None)
    if not callable(model_lookup):
        return False
    model = model_lookup(model_id)
    if model is None:
        return False
    supported = getattr(model, "supported_generation_protocols", None)
    if supported is None:
        if getattr(model, "model_type", "chat") == "embeddings":
            supported = ()
        else:
            supported = getattr(model, "supported_protocols", None)
            if supported is None:
                api = getattr(model, "api", None)
                supported = (api,) if isinstance(api, str) else ()
    return protocol in supported


def _native_headers(response: NativeResponse) -> tuple[tuple[str, str], ...]:
    return tuple(
        (name, value)
        for name, value in response.headers
        if name.lower() not in {
            "content-type",
            "content-length",
            "connection",
            "transfer-encoding",
            "set-cookie",
            "authorization",
            "x-api-key",
        }
    )


def _native_content_type(response: NativeResponse, default: str) -> str:
    return response.header("content-type") or default


def _native_sse_is_error(event: Any) -> bool:
    if event.event == "error":
        return True
    try:
        payload = event.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return False
    return isinstance(payload, dict) and payload.get("type") == "error"


async def _write_native_error(
    writer: asyncio.StreamWriter,
    error: NativeHTTPError,
) -> None:
    response = error.response
    writer.write(_http_response(
        response.status,
        "Upstream Error",
        response.body,
        content_type=_native_content_type(response, "application/json"),
        extra_headers=_native_headers(response),
    ))
    await writer.drain()
    await _close_writer(writer)


async def _write_native_transport_error(
    writer: asyncio.StreamWriter,
    *,
    format: ProxyFormat = "anthropic-messages",
) -> None:
    writer.write(_error_response(
        502,
        "Bad Gateway",
        _UPSTREAM_TRANSPORT_ERROR,
        format=format,
    ))
    await writer.drain()
    await _close_writer(writer)


async def _handle_count_tokens(
    writer: asyncio.StreamWriter,
    provider: Any,
    body: bytes,
    *,
    request_headers: dict[str, str],
) -> None:
    try:
        request_body = json.loads(body)
        parsed = proxy_anthropic.parse_count_tokens_request(request_body, request_headers)
    except (
        json.JSONDecodeError,
        UnicodeDecodeError,
        proxy_anthropic.InvalidRequest,
        ValueError,
    ) as exc:
        response = json.dumps(_error_payload(
            "anthropic-messages",
            str(exc),
            kind="invalid_request_error",
            param=getattr(exc, "param", None),
        )).encode()
        writer.write(_http_response(400, "Bad Request", response))
        await writer.drain()
        await _close_writer(writer)
        return

    model_lookup = getattr(provider, "model", None)
    if not callable(model_lookup) or model_lookup(parsed.model) is None:
        response = json.dumps(_error_payload(
            "anthropic-messages",
            f"Model not found: {parsed.model}",
            kind="not_found_error",
            param="model",
        )).encode()
        writer.write(_http_response(404, "Not Found", response))
        await writer.drain()
        await _close_writer(writer)
        return

    preflight = getattr(provider, "supports_native_request", None)
    native = callable(preflight) and preflight(parsed.model, parsed.native)
    if not native:
        estimate = proxy_anthropic._estimate_count_tokens(parsed.native.json())
        response = json.dumps({"input_tokens": estimate}, separators=(",", ":")).encode()
        writer.write(_http_response(
            200,
            "OK",
            response,
            extra_headers=(("x-xdog-upstream-protocol", "best-effort"),),
        ))
        await writer.drain()
        await _close_writer(writer)
        return

    try:
        response = await provider.request_complete(parsed.model, parsed.native)
    except NativeHTTPError as exc:
        await _write_native_error(writer, exc)
        return
    except Exception as exc:
        if not _is_retryable_transport_error(exc):
            raise
        logger.error("Native Anthropic count request failed before response")
        await _write_native_transport_error(writer)
        return

    writer.write(_http_response(
        response.status,
        "OK",
        response.body,
        content_type=_native_content_type(response, "application/json"),
        extra_headers=_native_headers(response),
    ))
    await writer.drain()
    await _close_writer(writer)


async def _handle_native_anthropic(
    writer: asyncio.StreamWriter,
    provider: Any,
    request: proxy_anthropic.ParsedRequest,
) -> bool:
    if not _native_anthropic_capable(provider, request.model):
        return False

    if not request.stream:
        try:
            response = await provider.request_complete(request.model, request.native)
        except NotImplementedError:
            return False
        except NativeHTTPError as exc:
            await _write_native_error(writer, exc)
            return True
        except Exception as exc:
            if not _is_retryable_transport_error(exc):
                raise
            logger.error("Native Anthropic request failed before response")
            await _write_native_transport_error(writer)
            return True
        writer.write(_http_response(
            response.status,
            "OK",
            response.body,
            content_type=_native_content_type(response, "application/json"),
            extra_headers=_native_headers(response),
        ))
        await writer.drain()
        await _close_writer(writer)
        return True

    try:
        stream = await provider.request_stream(request.model, request.native)
    except NotImplementedError:
        return False
    except NativeHTTPError as exc:
        await _write_native_error(writer, exc)
        return True
    except Exception as exc:
        if not _is_retryable_transport_error(exc):
            raise
        logger.error("Native Anthropic stream failed before response")
        await _write_native_transport_error(writer)
        return True

    stream_headers = {
        name: value
        for name, value in stream.start.headers
        if name.lower() != "content-type"
    }
    content_type = stream.start.header("content-type") or "text/event-stream"
    writer.write(_sse_response_headers(stream_headers, content_type=content_type))
    await writer.drain()
    try:
        async for event in stream:
            writer.write(event.encode())
            await writer.drain()
            if _native_sse_is_error(event):
                break
    except (BrokenPipeError, ConnectionResetError):
        logger.debug("Client disconnected during native Anthropic stream")
    except Exception:
        logger.exception("Native Anthropic stream failed")
        try:
            writer.write(_sse_line("error", {
                "type": "error",
                "error": {
                    "type": "api_error",
                    "message": _UPSTREAM_TRANSPORT_ERROR,
                },
            }))
            await writer.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
    finally:
        await stream.aclose()
        await _close_writer(writer)
    return True


def _native_openai_sse_is_terminal(event: Any, format: ProxyFormat) -> bool:
    if event.data == b"[DONE]":
        return True
    try:
        payload = event.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return False
    if not isinstance(payload, dict):
        return False
    if format == "openai-responses":
        return event.event == "error" or payload.get("type") in {
            "error",
            "response.completed",
            "response.failed",
            "response.incomplete",
        }
    return "error" in payload or payload.get("type") == "error"


def _native_openai_stream_error(format: ProxyFormat) -> bytes:
    payload = proxy_openai.error_body(
        _UPSTREAM_TRANSPORT_ERROR,
        kind="server_error",
    )
    if format == "openai-responses":
        return _sse_line("error", {
            "type": "error",
            "code": "server_error",
            "message": _UPSTREAM_TRANSPORT_ERROR,
            "param": None,
        })
    return NativeSSEEvent(None, json.dumps(payload, separators=(",", ":")).encode()).encode()


async def _handle_native_openai(
    writer: asyncio.StreamWriter,
    provider: Any,
    request: proxy_openai.ParsedRequest,
    format: proxy_openai.OpenAIProtocol,
) -> None:
    if not request.stream:
        try:
            response = await provider.request_complete(request.model, request.native)
        except NativeHTTPError as exc:
            await _write_native_error(writer, exc)
            return
        except Exception as exc:
            if not _is_retryable_transport_error(exc):
                raise
            logger.error("Native OpenAI request failed before response")
            await _write_native_transport_error(writer, format=format)
            return
        writer.write(_http_response(
            response.status,
            "OK",
            response.body,
            content_type=_native_content_type(response, "application/json"),
            extra_headers=_native_headers(response),
        ))
        await writer.drain()
        await _close_writer(writer)
        return

    try:
        stream = await provider.request_stream(request.model, request.native)
    except NativeHTTPError as exc:
        await _write_native_error(writer, exc)
        return
    except Exception as exc:
        if not _is_retryable_transport_error(exc):
            raise
        logger.error("Native OpenAI stream failed before response")
        await _write_native_transport_error(writer, format=format)
        return

    stream_headers = {
        name: value
        for name, value in stream.start.headers
        if name.lower() != "content-type"
    }
    content_type = stream.start.header("content-type") or "text/event-stream"
    writer.write(_sse_response_headers(
        stream_headers,
        content_type=content_type,
        status=stream.start.status,
    ))
    await writer.drain()
    terminal = False
    disconnected = False
    try:
        iterator = stream.__aiter__()
        while True:
            try:
                event = await anext(iterator)
            except StopAsyncIteration:
                break
            except Exception:
                logger.exception("Native OpenAI stream failed")
                try:
                    writer.write(_native_openai_stream_error(format))
                    await writer.drain()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                terminal = True
                break
            try:
                writer.write(event.encode())
                await writer.drain()
            except (BrokenPipeError, ConnectionResetError):
                logger.debug("Client disconnected during native OpenAI stream")
                disconnected = True
                break
            if _native_openai_sse_is_terminal(event, format):
                terminal = True
                break
        if not terminal and not disconnected:
            try:
                writer.write(_native_openai_stream_error(format))
                await writer.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass
    finally:
        await stream.aclose()
        await _close_writer(writer)


async def _handle_openai(
    writer: asyncio.StreamWriter,
    provider: Any,
    body: bytes,
    *,
    format: ProxyFormat,
    request_headers: dict[str, str],
) -> None:
    assert format in ("openai-responses", "openai-completions")
    try:
        request_body = json.loads(body)
        parsed = proxy_openai.parse_request(request_body, format, request_headers)
    except (
        json.JSONDecodeError,
        UnicodeDecodeError,
        proxy_openai.InvalidRequest,
        ValueError,
    ) as exc:
        response = json.dumps(proxy_openai.error_body(
            str(exc),
            param=getattr(exc, "param", None),
        )).encode()
        writer.write(_http_response(400, "Bad Request", response))
        await writer.drain()
        await _close_writer(writer)
        return

    if _native_openai_capable(provider, parsed.model, format):
        await _handle_native_openai(writer, provider, parsed, format)
        return

    if format == "openai-completions":
        response = json.dumps(proxy_openai.error_body(
            f"Model {parsed.model!r} does not support native Chat Completions",
            param="model",
        )).encode()
        writer.write(_http_response(400, "Bad Request", response))
        await writer.drain()
        await _close_writer(writer)
        return

    await _handle_messages(
        writer,
        provider,
        body,
        format="openai-responses",
    )


async def _handle_messages(
    writer: asyncio.StreamWriter,
    provider: Any,
    body: bytes,
    *,
    format: Literal["anthropic-messages", "openai-responses"] = "anthropic-messages",
    request_headers: dict[str, str] | None = None,
) -> None:
    """Handle normalized generation for Anthropic or Responses fallback."""
    responses = format == "openai-responses"
    parsed_anthropic: proxy_anthropic.ParsedRequest | None = None
    try:
        request_body = json.loads(body)
        if responses:
            model_id, context, options, is_stream = proxy_responses.parse_request(request_body)
        else:
            parsed_anthropic = proxy_anthropic.parse_request(request_body, request_headers)
            model_id = parsed_anthropic.model
            context = parsed_anthropic.context
            options = parsed_anthropic.options
            is_stream = parsed_anthropic.stream
    except (
        json.JSONDecodeError,
        UnicodeDecodeError,
        proxy_anthropic.InvalidRequest,
        proxy_responses.InvalidRequest,
    ) as exc:
        resp = json.dumps(proxy_responses.error_body(
            str(exc), param=getattr(exc, "param", None),
        ) if responses else {
            "type": "error",
            "error": {"type": "invalid_request_error", "message": str(exc)},
        }).encode()
        writer.write(_http_response(400, "Bad Request", resp))
        await writer.drain()
        writer.close()
        return

    logger.info("Proxy request: model=%s stream=%s", model_id, is_stream)

    if parsed_anthropic is not None:
        handled = await _handle_native_anthropic(
            writer,
            provider,
            parsed_anthropic,
        )
        if handled:
            return

    fallback_headers: dict[str, str] = {}
    if responses or parsed_anthropic is not None:
        fallback_headers["x-xdog-upstream-protocol"] = "best-effort"
    if parsed_anthropic is not None:
        ignored_parameters = tuple(sorted({
            *parsed_anthropic.ignored_parameters,
            *target_projection_issues(
                _model_protocol(provider, model_id),
                request_body,
            ),
        }))
        if ignored_parameters:
            fallback_headers["x-xdog-ignored-parameters"] = ",".join(
                ignored_parameters,
            )
            logger.info(
                "Best-effort Anthropic translation ignored parameters: %s",
                ", ".join(ignored_parameters),
            )

    if is_stream:
        primed_events: tuple[Any, ...] = ()
        iterator: AsyncIterator[Any] | None = None

        for attempt in range(len(_INITIAL_STREAM_RETRY_DELAYS) + 1):
            try:
                event_stream = provider.stream(model_id, context, options)
                iterator = event_stream.__aiter__()
                pending: list[Any] = []
                while True:
                    event = await anext(iterator)
                    pending.append(event)
                    if event.type != "start":
                        break
                primed_events = tuple(pending)
                break
            except StopAsyncIteration:
                writer.write(_error_response(
                    502,
                    "Bad Gateway",
                    "Upstream stream ended before producing a response",
                    format=format,
                ))
                await writer.drain()
                await _close_writer(writer)
                return
            except AuthExpiredError:
                raise
            except Exception as exc:
                if not _is_retryable_transport_error(exc):
                    raise
                if attempt >= len(_INITIAL_STREAM_RETRY_DELAYS):
                    logger.error("Initial upstream stream failed after retries: %s", exc)
                    writer.write(_error_response(
                        502,
                        "Bad Gateway",
                        _UPSTREAM_TRANSPORT_ERROR,
                        format=format,
                    ))
                    await writer.drain()
                    await _close_writer(writer)
                    return
                delay = _INITIAL_STREAM_RETRY_DELAYS[attempt]
                logger.warning(
                    "Initial upstream stream failed; retrying in %.2fs: %s",
                    delay,
                    exc,
                )
                await asyncio.sleep(delay)

        assert primed_events
        assert iterator is not None

        admitted_event = primed_events[-1]
        if isinstance(admitted_event, ErrorEvent):
            status, error = _parse_upstream_error(admitted_event.error)
            if responses:
                error = proxy_responses.error_body(
                    error["error"]["message"], kind=error["error"].get("type", "server_error"),
                )
            response = json.dumps(error).encode()
            writer.write(_http_response(status, "Upstream Error", response))
            await writer.drain()
            await _close_writer(writer)
            return

        writer.write(_sse_response_headers(fallback_headers))
        await writer.drain()

        try:
            replay = _prepend_events(primed_events, iterator)
            sse = proxy_responses.stream_to_sse(
                replay, model_id, request_body,
            ) if responses else stream_to_sse(replay, model_id)
            async for chunk in sse:
                writer.write(chunk)
                await writer.drain()
        except (BrokenPipeError, ConnectionResetError):
            logger.debug("Client disconnected during stream")
        except Exception as exc:
            logger.error("Stream error: %s", exc)
            try:
                writer.write(_sse_line("error", {
                    "type": "error", "code": "server_error", "message": _UPSTREAM_TRANSPORT_ERROR,
                    "param": None,
                } if responses else {
                    "type": "error",
                    "error": {
                        "type": "api_error",
                        "message": _UPSTREAM_TRANSPORT_ERROR,
                    },
                }))
                await writer.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass
        finally:
            await _close_writer(writer)

    else:
        msg = await provider.complete(model_id, context, options)
        if msg.stop_reason == "error" and msg.error_message:
            status, error = _parse_upstream_error(msg.error_message)
            if responses:
                error = proxy_responses.error_body(
                    error["error"]["message"], kind=error["error"].get("type", "server_error"),
                )
            resp = json.dumps(error).encode()
            writer.write(_http_response(
                status,
                "Bad Request" if status == 400 else "Upstream Error",
                resp,
                extra_headers=fallback_headers,
            ))
        else:
            result = (
                proxy_responses.format_response(msg, model_id, request_body)
                if responses else format_non_streaming_response(msg, model_id)
            )
            resp = json.dumps(result).encode()
            writer.write(_http_response(200, "OK", resp, extra_headers=fallback_headers))
        await writer.drain()
        writer.close()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def start_proxy(
    host: str = "127.0.0.1",
    port: int = 8082,
    api_key: str = "",
) -> None:
    """Start the Anthropic Messages, token count, and OpenAI API proxy server.

    Uses the ai Runtime, which routes model names to the correct provider
    automatically. Model names can be ``"provider/model"`` (explicit) or
    just ``"model"`` (if only one provider is active).

    Parameters
    ----------
    api_key:
        If set, all requests must include this key via
        ``Authorization: Bearer <key>`` or ``x-api-key: <key>``.
        If empty, no authentication is required.
    """
    import xdog.ai as ai
    runtime = ai.load()
    active = runtime.active_providers()
    if not active:
        raise RuntimeError("No active providers. Run 'xdog-ai login copilot' first.")

    logger.info("Active providers: %s", ", ".join(active))

    # Sync models from upstream APIs (refreshes cache if stale)
    synced = await runtime.sync_models()
    logger.info("Synced %d models from %d provider(s)", len(synced), len(active))

    async def on_connect(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await _handle_connection(reader, writer, runtime, api_key=api_key)

    server = await asyncio.start_server(on_connect, host, port)
    addr = server.sockets[0].getsockname() if server.sockets else (host, port)
    logger.info("AI API proxy listening on http://%s:%d", addr[0], addr[1])
    print(f"AI API proxy listening on http://{addr[0]}:{addr[1]}")
    print(f"Providers: {', '.join(active)}")
    print(f"Auth: {'API key required' if api_key else 'none (open)'}")
    print("Endpoints:")
    print(f"  POST http://{addr[0]}:{addr[1]}/v1/messages")
    print(f"  POST http://{addr[0]}:{addr[1]}/v1/messages/count_tokens")
    print(f"  POST http://{addr[0]}:{addr[1]}/v1/responses")
    print(f"  POST http://{addr[0]}:{addr[1]}/v1/chat/completions")
    print(f"  GET  http://{addr[0]}:{addr[1]}/v1/models")

    async with server:
        await server.serve_forever()


def run_proxy(
    host: str = "127.0.0.1",
    port: int = 8082,
    api_key: str = "",
) -> None:
    """Run the proxy server (blocking)."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    try:
        asyncio.run(start_proxy(host, port, api_key))
    except KeyboardInterrupt:
        print("\nProxy stopped.")


# ---------------------------------------------------------------------------
# __main__ support: python -m ai.proxy
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Anthropic Messages, token count, and OpenAI proxy backed by the ai package",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8082, help="Port (default: 8082)")
    parser.add_argument("--api-key", default="", help="API key for authentication (default: none)")
    args = parser.parse_args()
    run_proxy(args.host, args.port, args.api_key)
