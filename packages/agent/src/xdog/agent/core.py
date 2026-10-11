"""Core agent types — StreamFn, tools, config, state.

These are the foundational types that define what an agent is and how it
interacts with the LLM. Higher-level types (events, callbacks, loop config)
live in ``events.py`` and ``types.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import (
    Any,
    Awaitable,
    Callable,
    Literal,
    Protocol,
    Union,
    runtime_checkable,
)

from xdog.ai.types import (
    AssistantMessage,
    Context,
    Message,
    Model,
    StreamOptions,
    SystemPrompt,
    ToolResultContentPart,
)
from xdog.ai.utils.event_stream import EventStream as AiEventStream

# ---------------------------------------------------------------------------
# Agent messages
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CustomAgentMessage:
    """Base for application-specific agent messages.

    Subclass this to add domain-specific message types to the agent
    conversation history.  They will be converted to standard LLM messages
    via the ``convert_to_llm`` callback in :class:`AgentLoopConfig`.
    """

    type: str = "custom"
    data: dict[str, Any] = field(default_factory=dict)


AgentMessage = Union[Message, CustomAgentMessage]
"""Agent messages can be standard LLM messages or custom agent-level messages."""


# ---------------------------------------------------------------------------
# Stream / Embed / WebSearch function protocols
# ---------------------------------------------------------------------------

@runtime_checkable
class StreamFn(Protocol):
    """Stream function: ``(model, context, options) -> EventStream[AssistantMessage]``.

    The Agent calls this to make LLM requests. Build one from an ai
    Provider via ``stream_fn_from_provider(provider)``.
    """

    def __call__(
        self,
        model: str,
        context: Context,
        options: StreamOptions,
    ) -> AiEventStream[AssistantMessage]: ...


@runtime_checkable
class ModelStreamFn(StreamFn, Protocol):
    """Optional cached metadata on the SAME adapter/provider that executes requests."""

    def resolve_model(self, name: str) -> Model | None: ...


@runtime_checkable
class CatalogStreamFn(ModelStreamFn, Protocol):
    """Optional read-only cached catalog from the adapter that executes requests."""

    def models(self) -> tuple[Model, ...]: ...


@runtime_checkable
class EmbedFn(Protocol):
    """Embed function: ``(text) -> list[float]``.

    If provided to Agent, a built-in ``embed`` tool is automatically registered.
    """

    async def __call__(self, text: str) -> list[float]: ...


@runtime_checkable
class WebSearchFn(Protocol):
    """Web search function: ``(query) -> str``.

    If provided to Agent, a built-in ``web_search`` tool is automatically registered.
    """

    async def __call__(self, query: str) -> str: ...


# ---------------------------------------------------------------------------
# Tool execution mode
# ---------------------------------------------------------------------------

ToolExecutionMode = Literal["sequential", "parallel"]
"""How tool calls within a single turn are executed."""


# ---------------------------------------------------------------------------
# Tool result & callback
# ---------------------------------------------------------------------------

@dataclass
class AgentToolResult:
    """Result of executing an agent tool."""

    content: tuple[ToolResultContentPart, ...] = ()
    details: Any = None
    is_error: bool = False


AgentToolUpdateCallback = Callable[[AgentToolResult], Awaitable[None]]
"""Awaited by tool implementations to report incremental progress."""


# ---------------------------------------------------------------------------
# Agent tool
# ---------------------------------------------------------------------------

@dataclass
class AgentTool:
    """An executable tool available to the agent.

    Parameters
    ----------
    name:
        Unique tool identifier (passed to the LLM).
    description:
        Natural-language description shown to the LLM.
    parameters:
        JSON Schema describing the tool's parameters.
    label:
        Short human-readable label (for UI display).
    execute:
        Async callable that performs the tool action.

        Signature::

            async def execute(
                tool_call_id: str,
                params: dict[str, Any],
                cancel: asyncio.Event | None = None,
                on_update: AgentToolUpdateCallback | None = None,
                ctx: dict[str, Any] | None = None,
            ) -> AgentToolResult
    """

    name: str
    description: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)
    label: str = ""
    execute: Callable[..., Awaitable[AgentToolResult]] | None = field(
        default=None,
        repr=False,
    )
    validate_arguments: Callable[[dict[str, Any]], None | Awaitable[None]] | None = field(default=None, repr=False)
    """Optional pure pre-permission argument check; execute must recheck transformed arguments."""


# ---------------------------------------------------------------------------
# Queue mode
# ---------------------------------------------------------------------------

class QueueMode(str, Enum):
    """Controls how queued messages (steering / follow-up) are consumed."""

    ALL = "all"
    ONE_AT_A_TIME = "one-at-a-time"


# ---------------------------------------------------------------------------
# Agent context
# ---------------------------------------------------------------------------

@dataclass
class AgentContext:
    """The conversation context passed into the agent loop."""

    system_prompt: SystemPrompt = ""
    messages: list[AgentMessage] = field(default_factory=list)
    tools: list[AgentTool] | None = None


# ---------------------------------------------------------------------------
# Agent config
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AgentConfig:
    """Configuration for an Agent instance.

    Holds agent identity (model, system_prompt), model limits for
    compaction (context_window, max_prompt_tokens), model capabilities
    (supports_vision, supports_tool_calls), streaming options, and
    behavioral settings.
    """

    # Identity
    model: str = ""
    system_prompt: SystemPrompt = ""

    # Model limits (for compaction / overflow decisions)
    context_window: int = 200_000
    max_prompt_tokens: int = 0

    # Model capabilities
    supports_vision: bool = False
    # Tri-state on purpose. True and False are claims about the model; None is
    # "nobody looked", which must not be confused with either — assuming True
    # sends definitions a model may reject, and assuming False silently removes
    # every tool. None behaves like True and is the default, so a caller that
    # says nothing gets exactly what it got before.
    supports_tool_calls: bool | None = None
    supports_streaming: bool = True

    # Streaming options (temperature, thinking, max_tokens, etc.)
    options: StreamOptions = field(default_factory=StreamOptions)

    # Behavioral settings
    tool_execution: ToolExecutionMode = "parallel"
    steering_mode: QueueMode = QueueMode.ONE_AT_A_TIME
    follow_up_mode: QueueMode = QueueMode.ONE_AT_A_TIME


# ---------------------------------------------------------------------------
# Agent state
# ---------------------------------------------------------------------------

@dataclass
class AgentState:
    """Mutable runtime state for a running agent.

    The :class:`Agent` class owns this and applies immutable-style updates
    by replacing the entire state object on each mutation via
    ``dataclasses.replace``.
    """

    system_prompt: SystemPrompt = ""
    model: str = ""
    tools: tuple[AgentTool, ...] = ()
    messages: tuple[AgentMessage, ...] = ()
    is_streaming: bool = False
    stream_message: AgentMessage | None = None
    pending_tool_calls: frozenset[str] = field(default_factory=frozenset)
    error: str | None = None
