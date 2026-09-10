"""OpenAI Chat Completions protocol.

Implements streaming via the ``openai`` Python SDK.  Covers the standard
``/v1/chat/completions`` endpoint used by OpenAI, DeepSeek, Groq, xAI,
Fireworks, Together, OpenRouter, and other OpenAI-compatible APIs.

Ported from the TypeScript original at
``packages/ai/src/providers/openai-completions.ts``.

Key design choices matching the TS reference:
- **Mutable message builder**: a dict-based ``output`` accumulates content
  parts, usage, and metadata.  Each streaming event carries an immutable
  ``partial`` snapshot (frozen :class:`AssistantMessage`) of the message
  being built at that point in time.
- **responseId**: captured from the first chunk's ``id`` field.
- **Incremental JSON**: tool call arguments are accumulated and parsed via
  :func:`~ai.utils.json_parse.parse_partial_json` on every delta, so
  consumers can inspect partially-constructed arguments.
- **Reasoning content**: handles ``reasoning_content``, ``reasoning``, and
  ``reasoning_text`` fields from various OpenAI-compatible providers.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx
from xdog.ai.core import AuthResult, BaseProtocol
from xdog.ai.native import NativeEventStream, NativeResponse, ProtocolRequest
from xdog.ai.protocols._message_builder import MessageBuilder
from xdog.ai.protocols._transform_messages import context_to_openai
from xdog.ai.types import (
    AssistantMessage,
    AssistantMessageEvent,
    Context,
    DoneEvent,
    EmbeddingObject,
    EmbeddingRequest,
    EmbeddingResponse,
    ErrorEvent,
    Model,
    OpenAICompletionsCompat,
    StartEvent,
    StopReason,
    StreamOptions,
    TextDeltaEvent,
    TextDoneEvent,
    TextStartEvent,
    ThinkingDeltaEvent,
    ThinkingDoneEvent,
    ThinkingStartEvent,
    ToolCallDeltaEvent,
    ToolCallDoneEvent,
    ToolCallStartEvent,
    ToolChoice,
    Usage,
    UsageEvent,
)
from xdog.ai.utils.cost import usage_with_cost
from xdog.ai.utils.event_stream import EventStream
from xdog.ai.utils.json_parse import parse_partial_json

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Compat resolution
# ---------------------------------------------------------------------------

_DEFAULT_COMPAT = OpenAICompletionsCompat()


def _get_compat(model: Model) -> OpenAICompletionsCompat:
    """Return the resolved :class:`OpenAICompletionsCompat` for *model*."""
    if isinstance(model.compat, OpenAICompletionsCompat):
        return model.compat
    return _DEFAULT_COMPAT


# ---------------------------------------------------------------------------
# reasoning_effort mapping (matches TS ``mapReasoningEffort``)
# ---------------------------------------------------------------------------

_DEFAULT_REASONING_MAP: dict[str, str] = {
    "minimal": "low",
    "low": "low",
    "medium": "medium",
    "high": "high",
    "xhigh": "high",
}


def _map_reasoning_effort(
    level: str,
    effort_map: dict[str, str] | None,
) -> str:
    """Map a thinking level to a provider-specific reasoning_effort string."""
    mapping = effort_map or _DEFAULT_REASONING_MAP
    return mapping.get(level, level)


# ---------------------------------------------------------------------------
# Stop reason mapping (matches TS mapStopReason)
# ---------------------------------------------------------------------------

def _map_stop_reason(raw: str | None) -> tuple[StopReason, str | None]:
    """Map provider finish_reason to ``(StopReason, optional_error_message)``.

    Returns a tuple to match the TS pattern where some finish reasons
    produce error messages.
    """
    if raw is None:
        return ("stop", None)
    mapping: dict[str, tuple[StopReason, str | None]] = {
        "stop": ("stop", None),
        "end": ("stop", None),
        "length": ("length", None),
        "function_call": ("toolUse", None),
        "tool_calls": ("toolUse", None),
        "content_filter": ("error", "Provider finish_reason: content_filter"),
        "network_error": ("error", "Provider finish_reason: network_error"),
    }
    result = mapping.get(raw)
    if result is not None:
        return result
    return ("error", f"Provider finish_reason: {raw}")


def _tool_choice(choice: ToolChoice) -> str | dict[str, Any]:
    """Convert provider-neutral tool choice to Chat Completions format."""
    if choice.type == "any":
        return "required"
    if choice.type in ("auto", "none"):
        return choice.type
    if not choice.name:
        raise ValueError("Named tool choice requires a tool name")
    return {"type": "function", "function": {"name": choice.name}}


def _response_format(options: StreamOptions) -> dict[str, Any] | None:
    value = options.response_format
    if value is None:
        return None
    schema: dict[str, Any] = {"name": value.name, "schema": value.schema()}
    if value.description is not None:
        schema["description"] = value.description
    if value.strict is not None:
        schema["strict"] = value.strict
    return {"type": "json_schema", "json_schema": schema}


# ---------------------------------------------------------------------------
# Usage parsing (matches TS parseChunkUsage)
# ---------------------------------------------------------------------------

def _parse_chunk_usage(raw_usage: Any) -> Usage:
    """Parse a chunk's usage object into a :class:`Usage`."""
    if raw_usage is None:
        return Usage()

    prompt_tokens = getattr(raw_usage, "prompt_tokens", 0) or 0
    completion_tokens = getattr(raw_usage, "completion_tokens", 0) or 0

    # cached tokens
    cached_tokens = 0
    prompt_details = getattr(raw_usage, "prompt_tokens_details", None)
    if prompt_details is not None:
        cached_tokens = getattr(prompt_details, "cached_tokens", 0) or 0

    # OpenAI reports cached tokens inside prompt_tokens and reasoning tokens
    # inside completion_tokens, so neither is added on top again.
    input_tokens = prompt_tokens - cached_tokens
    output_tokens = completion_tokens
    total_tokens = (
        getattr(raw_usage, "total_tokens", 0) or (prompt_tokens + completion_tokens)
    )

    usage = Usage(
        input=input_tokens,
        output=output_tokens,
        cache_read=cached_tokens,
        cache_write=0,
        total_tokens=total_tokens,
    )
    return usage


# ---------------------------------------------------------------------------
# Block finalization helper
# ---------------------------------------------------------------------------

def _finish_block(
    block: dict[str, Any] | None,
    output: MessageBuilder,
) -> list[AssistantMessageEvent]:
    """Produce end events for the current block.

    Returns a list of events; the caller yields them into the stream.
    """
    if block is None:
        return []

    events: list[AssistantMessageEvent] = []
    btype = block.get("type")
    idx = output.block_index

    if btype == "text":
        events.append(TextDoneEvent(
            index=idx,
            text=block.get("text", ""),
            partial=output.snapshot(),
        ))
    elif btype == "thinking":
        events.append(ThinkingDoneEvent(
            index=idx,
            thinking=block.get("thinking", ""),
            thinking_signature=block.get("thinking_signature"),
            partial=output.snapshot(),
        ))
    elif btype == "toolCall":
        # Final parse of accumulated arguments
        partial_args = block.get("partial_args", "")
        parsed = parse_partial_json(partial_args)
        block["arguments"] = parsed if isinstance(parsed, dict) else {}
        events.append(ToolCallDoneEvent(
            index=idx,
            id=block.get("id", ""),
            name=block.get("name", ""),
            arguments=block["arguments"],
            partial=output.snapshot(),
        ))

    return events


# ---------------------------------------------------------------------------
# Public stream function
# ---------------------------------------------------------------------------


def stream(
    model: Model,
    context: Context,
    options: StreamOptions,
    auth: AuthResult,
) -> EventStream[AssistantMessage]:
    """Stream a response from an OpenAI-compatible Chat Completions API.

    Works with any provider that exposes the ``/v1/chat/completions``
    endpoint: OpenAI, DeepSeek, Groq, xAI, Fireworks, Together, etc.

    Matches the TypeScript ``streamOpenAICompletions`` implementation:
    - Every event carries a ``partial`` AssistantMessage snapshot.
    - Tool call arguments are incrementally parsed via ``parse_partial_json``.
    - ``response_id`` is extracted from the first chunk.
    - Reasoning/thinking content is extracted from multiple provider fields.
    """
    compat = _get_compat(model)
    reasoning_level = options.thinking
    temperature = options.temperature
    if reasoning_level and compat.supports_reasoning_effort:
        if temperature is not None and temperature != 1.0:
            temperature = None

    result_future: asyncio.Future[AssistantMessage] = asyncio.get_event_loop().create_future()

    async def _generate() -> AsyncIterator[AssistantMessageEvent]:
        try:
            import openai as openai_sdk
        except ImportError as exc:
            raise ImportError(
                "The 'openai' package is required for the OpenAI Completions provider. "
                "Install it with: pip install openai"
            ) from exc

        if not auth.api_key:
            raise ValueError(
                "API key is required. Set the appropriate env var or pass api_key in options."
            )

        base_url = model.base_url or None
        extra_headers: dict[str, str] = {**model.headers, **auth.headers}

        client = openai_sdk.AsyncOpenAI(
            api_key=auth.api_key,
            base_url=base_url,
            default_headers=extra_headers or None,
        )

        body = context_to_openai(context, model)
        body["stream"] = True

        if compat.supports_usage_in_streaming:
            body["stream_options"] = {"include_usage": True}

        if compat.supports_store:
            body["store"] = False

        if temperature is not None:
            body["temperature"] = temperature

        if options.max_tokens is not None:
            body[compat.max_tokens_field] = options.max_tokens

        if options.verbosity is not None:
            body["verbosity"] = options.verbosity

        if context.tools and options.parallel_tool_calls is not None:
            body["parallel_tool_calls"] = options.parallel_tool_calls

        if options.top_p is not None:
            body["top_p"] = options.top_p
        if options.stop_sequences is not None:
            body["stop"] = list(options.stop_sequences)
        if options.tool_choice is not None:
            body["tool_choice"] = _tool_choice(options.tool_choice)
        response_format = _response_format(options)
        if response_format is not None:
            body["response_format"] = response_format
        if options.metadata is not None:
            body["metadata"] = dict(options.metadata)
        if options.service_tier is not None:
            body["service_tier"] = options.service_tier

        # -- reasoning_effort ---------------------------------------------
        if compat.supports_reasoning_effort and reasoning_level:
            body["reasoning_effort"] = _map_reasoning_effort(
                reasoning_level, compat.reasoning_effort_map
            )

        # -- Message builder -----------------------------------------------
        output = MessageBuilder(model)

        # Current block being streamed (mutable dict in output.content)
        current_block: dict[str, Any] | None = None
        started = False

        try:
            response = await client.chat.completions.create(**body)
            iterator = response.__aiter__()
            try:
                first_chunk = await anext(iterator)
            except StopAsyncIteration as exc:
                raise httpx.RemoteProtocolError(
                    "Upstream stream ended before producing an event",
                ) from exc

            async def chunks() -> AsyncIterator[Any]:
                yield first_chunk
                async for item in iterator:
                    yield item

            started = True
            yield StartEvent(partial=output.snapshot())

            async for chunk in chunks():
                if not chunk or not isinstance(chunk, object):
                    continue

                # -- responseId (first chunk) ---------------------------------
                chunk_id = getattr(chunk, "id", None)
                if chunk_id and output.response_id is None:
                    output.response_id = chunk_id
                    output.mark_dirty()

                # -- Usage (chunk-level) --------------------------------------
                chunk_usage = getattr(chunk, "usage", None)
                if chunk_usage:
                    output.usage = _parse_chunk_usage(chunk_usage)
                    output.mark_dirty()

                choices = getattr(chunk, "choices", None)
                if not choices:
                    continue

                choice = choices[0]

                # Fallback: some providers return usage in choice.usage
                if not chunk_usage:
                    choice_usage = getattr(choice, "usage", None)
                    if choice_usage:
                        output.usage = _parse_chunk_usage(choice_usage)
                        output.mark_dirty()

                # -- finish_reason --------------------------------------------
                finish_reason = getattr(choice, "finish_reason", None)
                if finish_reason:
                    stop_reason, error_msg = _map_stop_reason(finish_reason)
                    output.stop_reason = stop_reason
                    if error_msg:
                        output.error_message = error_msg
                    output.mark_dirty()

                delta = getattr(choice, "delta", None)
                if delta is None:
                    continue

                # -- Text content ---------------------------------------------
                delta_content = getattr(delta, "content", None)
                if delta_content is not None and len(delta_content) > 0:
                    if current_block is None or current_block.get("type") != "text":
                        for ev in _finish_block(current_block, output):
                            yield ev
                        current_block = {"type": "text", "text": ""}
                        output.push_block(current_block)
                        yield TextStartEvent(
                            index=output.block_index,
                            partial=output.snapshot(),
                        )

                    current_block["text"] += delta_content
                    output.mark_dirty()
                    yield TextDeltaEvent(
                        index=output.block_index,
                        delta=delta_content,
                        partial=output.snapshot(),
                    )

                # -- Reasoning / thinking content -----------------------------
                # Probe multiple field names used by OpenAI-compatible
                # providers.  Copilot may surface reasoning via any of
                # these depending on the upstream model.
                reasoning_delta_text: str | None = None
                thinking_sig: str | None = None

                for rf in ("reasoning_content", "reasoning", "reasoning_text"):
                    val = getattr(delta, rf, None)
                    if val is not None and len(val) > 0:
                        reasoning_delta_text = val
                        thinking_sig = None
                        break

                if reasoning_delta_text is not None:
                    if current_block is None or current_block.get("type") != "thinking":
                        for ev in _finish_block(current_block, output):
                            yield ev
                        current_block = {
                            "type": "thinking",
                            "thinking": "",
                            "thinking_signature": thinking_sig,
                        }
                        output.push_block(current_block)
                        yield ThinkingStartEvent(
                            index=output.block_index,
                            partial=output.snapshot(),
                        )

                    current_block["thinking"] += reasoning_delta_text
                    output.mark_dirty()
                    yield ThinkingDeltaEvent(
                        index=output.block_index,
                        delta=reasoning_delta_text,
                        partial=output.snapshot(),
                    )

                # -- Tool calls -----------------------------------------------
                delta_tool_calls = getattr(delta, "tool_calls", None)
                if delta_tool_calls:
                    for tool_call in delta_tool_calls:
                        tc_id = getattr(tool_call, "id", None) or ""
                        tc_func = getattr(tool_call, "function", None)
                        tc_name = getattr(tc_func, "name", None) or "" if tc_func else ""
                        tc_args = getattr(tc_func, "arguments", None) or "" if tc_func else ""

                        # New tool call block?
                        if (
                            current_block is None
                            or current_block.get("type") != "toolCall"
                            or (tc_id and current_block.get("id") != tc_id)
                        ):
                            for ev in _finish_block(current_block, output):
                                yield ev
                            current_block = {
                                "type": "toolCall",
                                "id": tc_id,
                                "name": tc_name,
                                "arguments": {},
                                "partial_args": "",
                            }
                            output.push_block(current_block)
                            yield ToolCallStartEvent(
                                index=output.block_index,
                                id=tc_id,
                                name=tc_name,
                                partial=output.snapshot(),
                            )

                        # Accumulate deltas into current block
                        if tc_id:
                            current_block["id"] = tc_id
                        if tc_name:
                            current_block["name"] = tc_name

                        delta_str = ""
                        if tc_args:
                            delta_str = tc_args
                            current_block["partial_args"] += tc_args
                            # Incremental JSON parse on every delta
                            parsed = parse_partial_json(current_block["partial_args"])
                            if isinstance(parsed, dict):
                                current_block["arguments"] = parsed

                        output.mark_dirty()
                        yield ToolCallDeltaEvent(
                            index=output.block_index,
                            delta=delta_str,
                            partial=output.snapshot(),
                        )

            # -- End of stream: finish current block --------------------------
            for ev in _finish_block(current_block, output):
                yield ev

            # Check for abort/error post-stream
            if output.stop_reason == "aborted":
                raise Exception("Request was aborted")
            if output.stop_reason == "error":
                raise Exception(output.error_message or "Provider returned an error stop reason")

            # Yield usage event
            usage = usage_with_cost(model, output.usage)
            output.usage = usage
            output.mark_dirty()
            if usage.input > 0 or usage.output > 0:
                yield UsageEvent(usage=usage)

            # Build final message and emit done
            final_msg = output.snapshot()
            yield DoneEvent(stop_reason=output.stop_reason, message=final_msg)
            result_future.set_result(final_msg)

        except Exception as exc:
            if not started:
                raise
            output.stop_reason = "error"
            output.error_message = str(exc)
            error_snapshot = output.snapshot()

            yield ErrorEvent(
                error=str(exc),
                stop_reason=output.stop_reason,
                message=error_snapshot,
            )

            if not result_future.done():
                result_future.set_result(error_snapshot)

    return EventStream.from_async_generator(_generate(), result_future)


# ---------------------------------------------------------------------------
# Protocol class
# ---------------------------------------------------------------------------


class OpenAICompletionsProtocol(BaseProtocol):
    """OpenAI protocol — Chat Completions (streaming) + Embeddings.

    Handles ``/v1/chat/completions`` and ``/v1/embeddings``.
    """

    @property
    def id(self) -> str:
        return "openai-completions"

    @property
    def name(self) -> str:
        return "OpenAI Chat Completions"

    def stream(
        self,
        model: Model,
        context: Context,
        options: StreamOptions,
        auth: AuthResult,
    ) -> EventStream[AssistantMessage]:
        return stream(model, context, options, auth)

    def native_auth_context(self, request: ProtocolRequest) -> Context:
        from xdog.ai.protocols.native_auth import chat_native_auth_context

        return chat_native_auth_context(request.json())

    async def request_complete(
        self,
        model: Model,
        request: ProtocolRequest,
        auth: AuthResult,
    ) -> NativeResponse:
        from xdog.ai.protocols.openai_native import CHAT_COMPLETIONS, request_complete

        return await request_complete(CHAT_COMPLETIONS, model, request, auth)

    async def request_stream(
        self,
        model: Model,
        request: ProtocolRequest,
        auth: AuthResult,
    ) -> NativeEventStream:
        from xdog.ai.protocols.openai_native import CHAT_COMPLETIONS, request_stream

        return await request_stream(CHAT_COMPLETIONS, model, request, auth)

    async def embed(
        self,
        model: Model,
        request: EmbeddingRequest,
        auth: AuthResult,
    ) -> EmbeddingResponse:
        model_id = model.id
        if model_id.startswith(f"{model.provider}/"):
            model_id = model_id[len(model.provider) + 1:]

        input_val = [request.input] if isinstance(request.input, str) else list(request.input)

        body: dict[str, Any] = {"model": model_id, "input": input_val}
        if request.dimensions is not None:
            body["dimensions"] = request.dimensions
        if request.encoding_format != "float":
            body["encoding_format"] = request.encoding_format

        base_url = model.base_url or "https://api.githubcopilot.com"
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            **model.headers,
            **auth.headers,
        }
        if auth.api_key:
            headers["Authorization"] = f"Bearer {auth.api_key}"

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(f"{base_url}/embeddings", json=body, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        objects = tuple(
            EmbeddingObject(index=item.get("index", 0), embedding=tuple(item.get("embedding", [])))
            for item in data.get("data", [])
        )
        usage_raw = data.get("usage", {})
        return EmbeddingResponse(
            data=objects,
            model=data.get("model", ""),
            usage=usage_with_cost(model, Usage(
                input=usage_raw.get("prompt_tokens", 0),
                output=0,
                total_tokens=usage_raw.get("total_tokens", usage_raw.get("prompt_tokens", 0)),
            )),
        )
