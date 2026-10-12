"""Agent lifecycle events — discriminated union of all events emitted by the agent loop."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal, Union

from xdog.agent.core import AgentMessage, AgentToolResult
from xdog.agent.input_queue import QueuedInput
from xdog.ai.types import AssistantMessageEvent, ToolResultMessage, Usage, UserMessage


@dataclass(frozen=True, kw_only=True)
class EventMetadata:
    """Observation identity; sequence orders delivery, not durable storage."""

    session_id: str = ""
    run_id: str = ""
    sequence: int = 0


@dataclass(frozen=True)
class AgentStartEvent(EventMetadata):
    """Emitted when the agent loop begins."""

    type: Literal["agent_start"] = field(default="agent_start", init=False)


@dataclass(frozen=True)
class AgentEndEvent(EventMetadata):
    """Emitted when the agent loop finishes."""

    type: Literal["agent_end"] = field(default="agent_end", init=False)
    messages: tuple[AgentMessage, ...] = ()


@dataclass(frozen=True)
class TurnStartEvent(EventMetadata):
    """Emitted at the start of each LLM turn."""

    type: Literal["turn_start"] = field(default="turn_start", init=False)


@dataclass(frozen=True)
class TurnEndEvent(EventMetadata):
    """Emitted at the end of each LLM turn."""

    type: Literal["turn_end"] = field(default="turn_end", init=False)
    message: AgentMessage | None = None
    tool_results: tuple[ToolResultMessage, ...] = ()


@dataclass(frozen=True)
class MessageStartEvent(EventMetadata):
    """Emitted when a new assistant message begins streaming."""

    type: Literal["message_start"] = field(default="message_start", init=False)
    message: AgentMessage | None = None


@dataclass(frozen=True)
class MessageUpdateEvent(EventMetadata):
    """Emitted as the assistant message is incrementally updated."""

    type: Literal["message_update"] = field(default="message_update", init=False)
    message: AgentMessage | None = None
    assistant_message_event: AssistantMessageEvent | None = None


@dataclass(frozen=True)
class MessageEndEvent(EventMetadata):
    """Emitted when the assistant message is complete."""

    type: Literal["message_end"] = field(default="message_end", init=False)
    message: AgentMessage | None = None


@dataclass(frozen=True)
class ToolExecutionStartEvent(EventMetadata):
    """Emitted when a tool begins executing."""

    type: Literal["tool_execution_start"] = field(default="tool_execution_start", init=False)
    tool_call_id: str = ""
    tool_name: str = ""
    args: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolExecutionUpdateEvent(EventMetadata):
    """Emitted with incremental tool execution progress."""

    type: Literal["tool_execution_update"] = field(default="tool_execution_update", init=False)
    tool_call_id: str = ""
    tool_name: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    partial_result: AgentToolResult | None = None


@dataclass(frozen=True)
class ToolExecutionEndEvent(EventMetadata):
    """Emitted when a tool finishes executing."""

    type: Literal["tool_execution_end"] = field(default="tool_execution_end", init=False)
    tool_call_id: str = ""
    tool_name: str = ""
    result: AgentToolResult | None = None
    is_error: bool = False
    interrupted: bool = False
    """Cancellation left external effects unconfirmed, even if an error result exists."""


@dataclass(frozen=True, slots=True)
class InputConsumedEvent(EventMetadata):
    """An identified input was admitted to agent history, not just queued."""

    input: QueuedInput
    type: Literal["input_consumed"] = field(default="input_consumed", init=False)


@dataclass(frozen=True, slots=True)
class InputTransformedEvent(EventMetadata):
    original: UserMessage
    message: UserMessage
    source: str
    input_id: str = ""
    type: Literal["input_transformed"] = field(default="input_transformed", init=False)


@dataclass(frozen=True, slots=True)
class InputRejectedEvent(EventMetadata):
    original: UserMessage
    source: str
    input_id: str = ""
    type: Literal["input_rejected"] = field(default="input_rejected", init=False)


@dataclass(frozen=True, slots=True)
class InputHandledEvent(EventMetadata):
    original: UserMessage
    source: str
    feedback: str
    input_id: str = ""
    type: Literal["input_handled"] = field(default="input_handled", init=False)


@dataclass(frozen=True)
class UsageEvent(EventMetadata):
    """Usage for one completed model or compaction request, not a run budget."""

    usage: Usage = field(default_factory=Usage)
    model: str = ""
    source: Literal["model", "compaction"] = "model"
    type: Literal["usage"] = field(default="usage", init=False)


@dataclass(frozen=True)
class CompactionStartEvent(EventMetadata):
    automatic: bool = False
    type: Literal["compaction_start"] = field(default="compaction_start", init=False)


@dataclass(frozen=True)
class CompactionEndEvent(EventMetadata):
    summary: str = ""
    removed_messages: tuple[AgentMessage, ...] = ()
    generation: int = 0
    automatic: bool = False
    type: Literal["compaction_end"] = field(default="compaction_end", init=False)


@dataclass(frozen=True)
class CompactionFailedEvent(EventMetadata):
    error: str = ""
    automatic: bool = False
    type: Literal["compaction_failed"] = field(default="compaction_failed", init=False)


@dataclass(frozen=True)
class SnapshotEvent(EventMetadata):
    """Frozen safe-boundary data. Delivery is not a save/transaction ACK.

    JSON text is immutable; each snapshot property access returns a fresh data
    copy, so an observer cannot mutate the Agent or another observer's snapshot.
    """

    snapshot_json: str = "{}"
    type: Literal["snapshot"] = field(default="snapshot", init=False)

    @property
    def snapshot(self) -> dict[str, Any]:
        return json.loads(self.snapshot_json)  # type: ignore[no-any-return]


AgentEvent = Union[
    SnapshotEvent,
    UsageEvent,
    CompactionStartEvent,
    CompactionEndEvent,
    CompactionFailedEvent,
    InputHandledEvent,
    InputTransformedEvent,
    InputRejectedEvent,
    InputConsumedEvent,
    AgentStartEvent,
    AgentEndEvent,
    TurnStartEvent,
    TurnEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    MessageEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
    ToolExecutionEndEvent,
]
"""Discriminated union of all agent lifecycle events."""
