"""Core types for the ai unified multi-provider LLM API.

Defines all message types, content types, model definitions, tool interfaces,
context, streaming events, and usage/cost tracking.  Uses frozen dataclasses
for immutability and ``Literal`` types for discriminated unions.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal, Union

# ---------------------------------------------------------------------------
# API & Provider literals
# ---------------------------------------------------------------------------

ProtocolId = str
"""Protocol identifier (e.g. ``"openai-completions"``, ``"anthropic-messages"``)."""

Provider = str
"""Provider identifier (e.g. ``"copilot"``)."""


class ProviderType(StrEnum):
    """Supported provider types."""
    COPILOT = "copilot"
    ANTIGRAVITY = "antigravity"


ThinkingLevel = Literal["minimal", "low", "medium", "high", "xhigh"]
TextVerbosity = Literal["low", "medium", "high"]
ToolChoiceType = Literal["auto", "any", "tool", "none"]


InputModality = Literal["text", "image"]

StopReason = Literal["stop", "length", "toolUse", "error", "aborted"]

# ---------------------------------------------------------------------------
# Content types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TextContent:
    """Plain text content block."""

    type: Literal["text"] = field(default="text", init=False)
    text: str = ""
    text_signature: str | None = None


@dataclass(frozen=True)
class ThinkingContent:
    """Model reasoning / chain-of-thought content block."""

    type: Literal["thinking"] = field(default="thinking", init=False)
    thinking: str = ""
    thinking_signature: str | None = None
    redacted: bool = False


@dataclass(frozen=True)
class ImageContent:
    """Base64-encoded image content block."""

    type: Literal["image"] = field(default="image", init=False)
    data: str = ""
    mime_type: str = "image/png"


@dataclass(frozen=True)
class ToolCall:
    """A tool invocation issued by the model."""

    type: Literal["toolCall"] = field(default="toolCall", init=False)
    id: str = ""
    name: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    thought_signature: str | None = None


# Union helpers
UserContentPart = Union[TextContent, ImageContent]
AssistantContentPart = Union[TextContent, ThinkingContent, ToolCall]
ToolResultContentPart = Union[TextContent, ImageContent]

# ---------------------------------------------------------------------------
# Cost & usage
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelCost:
    """Model pricing.

    For per-token providers: ``input``/``output``/``cache_read``/``cache_write``
    hold per-million-token dollar rates.

    For Copilot: ``input`` holds the premium request multiplier
    (0 = free, 0.33 = lightweight, 1 = standard, 3 = premium).
    """

    input: float = 0.0
    output: float = 0.0
    cache_read: float = 0.0
    cache_write: float = 0.0


@dataclass(frozen=True)
class CostBreakdown:
    """Dollar cost breakdown by token category."""

    input: float = 0.0
    output: float = 0.0
    cache_read: float = 0.0
    cache_write: float = 0.0
    total: float = 0.0


@dataclass(frozen=True)
class Usage:
    """Token usage counters and associated cost."""

    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    total_tokens: int = 0
    cost: CostBreakdown = field(default_factory=CostBreakdown)


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UserMessage:
    """A message from the user."""

    role: Literal["user"] = field(default="user", init=False)
    content: str | tuple[UserContentPart, ...] = ""
    timestamp: float = 0.0  # seconds since epoch (time.time())


@dataclass(frozen=True)
class AssistantMessage:
    """A message produced by the model."""

    role: Literal["assistant"] = field(default="assistant", init=False)
    content: tuple[AssistantContentPart, ...] = ()
    api: ProtocolId = ""
    provider: Provider = ""
    model: str = ""
    response_id: str | None = None
    usage: Usage = field(default_factory=Usage)
    stop_reason: StopReason = "stop"
    error_message: str | None = None
    timestamp: float = 0.0  # seconds since epoch (time.time())


@dataclass(frozen=True)
class ToolResultMessage:
    """Result of a tool invocation, returned to the model."""

    role: Literal["toolResult"] = field(default="toolResult", init=False)
    tool_call_id: str = ""
    tool_name: str = ""
    content: tuple[ToolResultContentPart, ...] = ()
    details: Any = None
    is_error: bool = False
    timestamp: float = 0.0  # seconds since epoch (time.time())


Message = Union[UserMessage, AssistantMessage, ToolResultMessage]

# ---------------------------------------------------------------------------
# Tool & Context
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Tool:
    """An LLM-callable tool definition."""

    name: str = ""
    description: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SystemPromptBlock:
    """A block of system prompt text, optionally cacheable.

    When ``cache=True``, providers that support prompt caching (Anthropic)
    will mark this block with ``cache_control: {"type": "ephemeral"}``.
    Providers without caching support ignore the flag and use the text as-is.
    """

    text: str = ""
    cache: bool = False


# System prompt can be a plain string or structured blocks for caching.
SystemPrompt = str | tuple[SystemPromptBlock, ...] | None


def system_prompt_text(prompt: SystemPrompt) -> str | None:
    """Extract plain text from a system prompt (string or blocks)."""
    if prompt is None:
        return None
    if isinstance(prompt, str):
        return prompt
    return "\n\n".join(b.text for b in prompt if b.text)


@dataclass(frozen=True)
class Context:
    """Full conversation context sent to the model."""

    messages: tuple[Message, ...] = ()
    system_prompt: SystemPrompt = None
    tools: tuple[Tool, ...] | None = None


# ---------------------------------------------------------------------------
# Compatibility helpers for OpenAI-like APIs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OpenAICompletionsCompat:
    """Compatibility settings for models accessed via OpenAI completions.

    Mirrors the TypeScript ``OpenAICompletionsCompat`` interface.  Each field
    controls a behavioural quirk of a specific provider/endpoint so that
    ``openai_completions.py`` and ``transform_messages.py`` can adapt without
    provider-specific ``if`` chains.
    """

    supports_store: bool = False
    supports_developer_role: bool = False
    supports_reasoning_effort: bool = False
    reasoning_effort_map: dict[str, str] | None = None
    supports_usage_in_streaming: bool = True
    max_tokens_field: str = "max_tokens"  # or "max_completion_tokens"
    requires_tool_result_name: bool = False
    requires_assistant_after_tool_result: bool = False
    requires_thinking_as_text: bool = False
    supports_strict_mode: bool = False


# ---------------------------------------------------------------------------
# Vision limits
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VisionLimits:
    """Image input constraints reported by the model API."""

    max_prompt_images: int = 0
    max_prompt_image_size: int = 0  # bytes
    supported_media_types: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Thinking budget range
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ThinkingBudgetRange:
    """Min/max thinking token budget reported by the model API.

    These come from ``supports.min_thinking_budget`` and
    ``supports.max_thinking_budget`` in the Copilot ``/models`` response.
    When available they override the hardcoded :class:`ThinkingBudgets`.
    """

    min_budget: int = 1024
    max_budget: int = 32768


# ---------------------------------------------------------------------------
# Model definition
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelEndpoint:
    """Provider transport route, distinct from the JSON wire protocol.

    Paths are relative to ``Model.base_url``. An endpoint is a transport
    capability, not evidence that the model accepts or produces images.
    """

    protocol: str
    operation: str  # generate | stream_generate | count_tokens | embed
    path: str
    method: str = "POST"


@dataclass(frozen=True)
class Model:
    """Full specification of an LLM model.

    Includes provider routing, pricing, capabilities, and context limits.
    """

    id: str = ""
    name: str = ""
    api: ProtocolId = ""
    provider: Provider = ""
    base_url: str = ""
    reasoning: bool = False
    input: tuple[InputModality, ...] | None = ("text",)
    cost: ModelCost = field(default_factory=ModelCost)
    context_window: int = 0
    max_prompt_tokens: int = 0
    max_tokens: int = 0
    headers: dict[str, str] = field(default_factory=dict)
    compat: OpenAICompletionsCompat | None = None
    supported_protocols: tuple[str, ...] | None = None
    # Exact native generation protocols advertised by provider endpoints.
    # None marks legacy/manual metadata; an empty tuple explicitly means none.
    supported_generation_protocols: tuple[str, ...] | None = None
    preferred_protocol: str | None = None
    # Capability flags from the provider API
    supports_tool_calls: bool = True
    supports_parallel_tool_calls: bool = False
    supports_streaming: bool = True
    supports_structured_outputs: bool = False
    # Thinking budget range (from API, not hardcoded)
    thinking_budget_range: ThinkingBudgetRange | None = None
    # Adaptive thinking: True = use type:adaptive + output_config.effort
    # None/False = use type:enabled + budget_tokens (legacy)
    adaptive_thinking: bool | None = None
    # Supported effort levels (e.g. ("low", "medium", "high"))
    supported_efforts: tuple[str, ...] | None = None
    # Vision constraints
    vision_limits: VisionLimits | None = None
    # Model metadata
    model_type: str = "chat"  # "chat" | "embeddings"
    vendor: str = ""
    version: str = ""
    preview: bool = False
    # Embedding-specific
    dimensions: int | None = None
    supports_dimensions: bool = False
    # None means unreported/unknown, not text-only. Image input and output are
    # independent: a vision model need not be an image generator.
    output: tuple[InputModality, ...] | None = None
    endpoints: tuple[ModelEndpoint, ...] | None = None
    # Native/provider-hosted search, not merely the ability to call a user tool.
    supports_web_search: bool | None = None

    @property
    def supports_image_input(self) -> bool | None:
        return None if self.input is None else "image" in self.input

    @property
    def supports_image_output(self) -> bool | None:
        return None if self.output is None else "image" in self.output


# ---------------------------------------------------------------------------
# Thinking budgets
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ThinkingBudgets:
    """Token budgets for each thinking level."""

    minimal: int = 1024
    low: int = 2048
    medium: int = 8192
    high: int = 16384
    xhigh: int = 32768


# ---------------------------------------------------------------------------
# Stream options
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolChoice:
    """Provider-neutral tool selection policy."""

    type: ToolChoiceType = "auto"
    name: str | None = None


@dataclass(frozen=True)
class JsonSchemaFormat:
    """Immutable structured-output schema for cross-provider requests."""

    schema_json: str
    name: str = "response"
    description: str | None = None
    strict: bool | None = None

    @classmethod
    def from_schema(
        cls,
        schema: dict[str, Any],
        *,
        name: str = "response",
        description: str | None = None,
        strict: bool | None = None,
    ) -> JsonSchemaFormat:
        import json

        return cls(
            schema_json=json.dumps(
                schema, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
            ),
            name=name,
            description=description,
            strict=strict,
        )

    def schema(self) -> dict[str, Any]:
        import json

        value = json.loads(self.schema_json)
        if not isinstance(value, dict):
            raise ValueError("JSON Schema must be an object")
        return value


@dataclass(frozen=True)
class StreamOptions:
    """User-facing options for stream/complete calls."""

    thinking: ThinkingLevel | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    cancel: asyncio.Event | None = None
    web_search: bool = False
    # Optional upstream Responses cache-routing hint; other protocols may ignore it.
    prompt_cache_key: str | None = None
    # None preserves the upstream default; False requests at most one tool call per turn.
    parallel_tool_calls: bool | None = None
    # OpenAI output-detail hint; ignored by protocols without a native equivalent.
    verbosity: TextVerbosity | None = None
    # Cross-provider generation controls used by best-effort protocol adapters.
    top_p: float | None = None
    stop_sequences: tuple[str, ...] | None = None
    tool_choice: ToolChoice | None = None
    response_format: JsonSchemaFormat | None = None
    metadata: tuple[tuple[str, str], ...] | None = None
    service_tier: str | None = None


# ---------------------------------------------------------------------------
# Streaming events  (AssistantMessageEvent discriminated union)
#
# Every event during content generation carries a ``partial`` field — an
# immutable snapshot of the AssistantMessage being built at the time the
# event was emitted.  Terminal events (``done`` / ``error``) carry the
# final ``message`` instead.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StartEvent:
    """Emitted when a stream begins."""

    type: Literal["start"] = field(default="start", init=False)
    partial: AssistantMessage | None = None


@dataclass(frozen=True)
class TextStartEvent:
    """Emitted when a new text content block begins."""

    type: Literal["text_start"] = field(default="text_start", init=False)
    index: int = 0
    partial: AssistantMessage | None = None


@dataclass(frozen=True)
class TextDeltaEvent:
    """Incremental text token."""

    type: Literal["text_delta"] = field(default="text_delta", init=False)
    index: int = 0
    delta: str = ""
    partial: AssistantMessage | None = None


@dataclass(frozen=True)
class TextDoneEvent:
    """Emitted when a text content block is complete."""

    type: Literal["text_done"] = field(default="text_done", init=False)
    index: int = 0
    text: str = ""
    text_signature: str | None = None
    partial: AssistantMessage | None = None


@dataclass(frozen=True)
class ThinkingStartEvent:
    """Emitted when a thinking content block begins."""

    type: Literal["thinking_start"] = field(default="thinking_start", init=False)
    index: int = 0
    partial: AssistantMessage | None = None


@dataclass(frozen=True)
class ThinkingDeltaEvent:
    """Incremental thinking token."""

    type: Literal["thinking_delta"] = field(default="thinking_delta", init=False)
    index: int = 0
    delta: str = ""
    partial: AssistantMessage | None = None


@dataclass(frozen=True)
class ThinkingDoneEvent:
    """Emitted when a thinking content block is complete."""

    type: Literal["thinking_done"] = field(default="thinking_done", init=False)
    index: int = 0
    thinking: str = ""
    thinking_signature: str | None = None
    redacted: bool = False
    partial: AssistantMessage | None = None


@dataclass(frozen=True)
class ToolCallStartEvent:
    """Emitted when a tool call begins."""

    type: Literal["tool_call_start"] = field(default="tool_call_start", init=False)
    index: int = 0
    id: str = ""
    name: str = ""
    partial: AssistantMessage | None = None


@dataclass(frozen=True)
class ToolCallDeltaEvent:
    """Incremental tool call argument JSON."""

    type: Literal["tool_call_delta"] = field(default="tool_call_delta", init=False)
    index: int = 0
    delta: str = ""
    partial: AssistantMessage | None = None


@dataclass(frozen=True)
class ToolCallDoneEvent:
    """Emitted when a tool call is fully received."""

    type: Literal["tool_call_done"] = field(default="tool_call_done", init=False)
    index: int = 0
    id: str = ""
    name: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    thought_signature: str | None = None
    partial: AssistantMessage | None = None


@dataclass(frozen=True)
class UsageEvent:
    """Token usage reported by the provider."""

    type: Literal["usage"] = field(default="usage", init=False)
    usage: Usage = field(default_factory=Usage)


@dataclass(frozen=True)
class DoneEvent:
    """Emitted once the stream has finished successfully.

    ``message`` carries the final assembled :class:`AssistantMessage`.
    """

    type: Literal["done"] = field(default="done", init=False)
    stop_reason: StopReason = "stop"
    message: AssistantMessage | None = None


@dataclass(frozen=True)
class StatusEvent:
    """Generic status update emitted during streaming.

    Used for non-content lifecycle events such as web search progress.
    Consumers can display these to the user or ignore them.
    """

    type: Literal["status"] = field(default="status", init=False)
    status: str = ""
    detail: str = ""


class AuthExpiredError(RuntimeError):
    """Stored credentials were rejected; a human has to log in again.

    Distinct from a transport failure or an upstream outage, because the
    remedy is different and only the user can apply it. Vendors raise this
    instead of letting the underlying 401 escape, so callers can say what to
    do rather than reporting an internal error for something that is neither
    internal nor a bug.
    """

    def __init__(self, vendor: str, command: str, detail: str = "") -> None:
        self.vendor = vendor
        self.command = command
        msg = f"{vendor} credentials are no longer valid. Run `{command}` to sign in again."
        if detail:
            msg = f"{msg} ({detail})"
        super().__init__(msg)


@dataclass(frozen=True)
class ErrorEvent:
    """Emitted when an error occurs during streaming.

    ``error`` is the human-readable error string.  ``message`` carries
    an :class:`AssistantMessage` with ``stop_reason`` set to ``"error"``
    or ``"aborted"`` and ``error_message`` populated.
    """

    type: Literal["error"] = field(default="error", init=False)
    error: str = ""
    stop_reason: StopReason = "error"
    message: AssistantMessage | None = None


AssistantMessageEvent = Union[
    StartEvent,
    TextStartEvent,
    TextDeltaEvent,
    TextDoneEvent,
    ThinkingStartEvent,
    ThinkingDeltaEvent,
    ThinkingDoneEvent,
    ToolCallStartEvent,
    ToolCallDeltaEvent,
    ToolCallDoneEvent,
    UsageEvent,
    StatusEvent,
    DoneEvent,
    ErrorEvent,
]


# ---------------------------------------------------------------------------
# Embedding types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EmbeddingRequest:
    """Request to generate embeddings for one or more inputs."""

    input: str | tuple[str, ...] = ""
    dimensions: int | None = None
    encoding_format: Literal["float", "base64"] = "float"


@dataclass(frozen=True)
class EmbeddingObject:
    """A single embedding vector from the response."""

    index: int = 0
    embedding: tuple[float, ...] = ()


@dataclass(frozen=True)
class EmbeddingResponse:
    """Response from an embedding API call."""

    data: tuple[EmbeddingObject, ...] = ()
    model: str = ""
    usage: Usage = field(default_factory=Usage)


# ---------------------------------------------------------------------------
# Image generation types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ImageGenerationRequest:
    """Provider-neutral image prompt and optional reference images."""

    prompt: str
    reference_images: tuple[ImageContent, ...] = ()
    aspect_ratio: str | None = None
    image_size: str | None = None


@dataclass(frozen=True)
class GeneratedImage:
    """Decoded image bytes; file storage is the caller's responsibility."""

    data: bytes = field(repr=False)
    mime_type: str = "image/png"


@dataclass(frozen=True)
class ImageGenerationResponse:
    images: tuple[GeneratedImage, ...] = ()
    text: str = ""
    model: str = ""
    provider: str = ""
    usage: Usage = field(default_factory=Usage)
