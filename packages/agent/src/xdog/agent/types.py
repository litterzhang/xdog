"""Hook types, callback aliases, and loop configuration.

These are the types used by the agent loop internals and hook system.
Core types (StreamFn, AgentTool, AgentConfig, AgentState) live in ``core.py``.
Event types live in ``events.py``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Union

from xdog.agent.core import (
    AgentContext,
    AgentMessage,
    AgentTool,
    AgentToolResult,
    ToolExecutionMode,
)
from xdog.agent.events import AgentEvent
from xdog.ai.types import AssistantMessage, Message, ToolCall, ToolResultContentPart, UserMessage

# ---------------------------------------------------------------------------
# Before/after tool call hooks
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BeforeToolCallResult:
    """Return value from a ``before_tool_call`` hook.

    Parameters
    ----------
    block:
        If ``True``, the tool call is not executed and an error result
        with *reason* is returned to the LLM instead.
    reason:
        Human-readable explanation when *block* is ``True``.
    """

    block: bool = False
    reason: str = ""


@dataclass(frozen=True)
class AfterToolCallResult:
    """Return value from an ``after_tool_call`` hook.

    Allows the hook to override the content / details / error flag of the
    tool result that will be forwarded to the LLM.  ``None`` fields mean
    "keep the original value".
    """

    content: tuple[ToolResultContentPart, ...] | None = None
    details: Any = None
    is_error: bool | None = None


@dataclass(frozen=True)
class BeforeToolCallContext:
    """Context passed to a ``before_tool_call`` hook."""

    assistant_message: AssistantMessage
    tool_call: ToolCall
    args: Any
    context: AgentContext


@dataclass(frozen=True)
class AfterToolCallContext:
    """Context passed to an ``after_tool_call`` hook."""

    assistant_message: AssistantMessage
    tool_call: ToolCall
    args: Any
    result: AgentToolResult
    is_error: bool
    context: AgentContext


# ---------------------------------------------------------------------------
# Callback type aliases
# ---------------------------------------------------------------------------

AgentEventSink = Callable[[AgentEvent], Union[Awaitable[None], None]]
"""Callback that receives agent lifecycle events."""

ConvertToLlmFn = Callable[
    [list[AgentMessage]],
    Union[list[Message], Awaitable[list[Message]]],
]
"""Converts agent-level messages to plain LLM messages."""

TransformContextFn = Callable[
    [list[AgentMessage]],
    Awaitable[list[AgentMessage]],
]
"""Optional async hook to transform the context before each LLM call."""

@dataclass(frozen=True, slots=True)
class HandledInput:
    """Explicit handler feedback; not a model response or canonical user-message admission."""
    feedback: str


InputTransformFn = Callable[[UserMessage, str, asyncio.Event | None], Awaitable[UserMessage | HandledInput]]
"""Optional canonical pre-admission input transform; failures must not fall back to unfiltered input."""

GetMessagesFn = Callable[
    [],
    Awaitable[list[AgentMessage]],
]
"""Retrieves dynamically injected messages (steering or follow-up)."""

BeforeToolCallFn = Callable[
    [BeforeToolCallContext, asyncio.Event | None],
    Awaitable[BeforeToolCallResult | None],
]
"""Async hook called before each tool execution."""

AfterToolCallFn = Callable[
    [AfterToolCallContext, asyncio.Event | None],
    Awaitable[AfterToolCallResult | None],
]
"""Async hook called after each tool execution."""


# ---------------------------------------------------------------------------
# Agent loop configuration (internal — callbacks only)
# ---------------------------------------------------------------------------

@dataclass
class AgentLoopConfig:
    """Callback configuration for the agent loop.

    The loop receives model name, StreamOptions, and stream_fn separately.
    This config holds only the behavioral callbacks.
    """

    convert_to_llm: ConvertToLlmFn | None = None
    transform_context: TransformContextFn | None = None
    get_tools: Callable[[], tuple[AgentTool, ...]] | None = None
    get_steering_messages: GetMessagesFn | None = None
    get_follow_up_messages: GetMessagesFn | None = None
    tool_execution: ToolExecutionMode = "parallel"
    before_tool_call: BeforeToolCallFn | None = None
    after_tool_call: AfterToolCallFn | None = None
