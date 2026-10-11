"""Stable history checkpoints shared by interactive session/branch navigation."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from xdog.agent.core import AgentMessage
from xdog.ai.types import AssistantMessage, TextContent, ToolCall, ToolResultMessage, UserMessage


@dataclass(frozen=True, slots=True)
class HistoryPoint:
    index: int
    role: str
    preview: str


def history_points(messages: Sequence[AgentMessage]) -> tuple[HistoryPoint, ...]:
    """Offer only boundaries with complete tool-call/result pairs.

    Truncating after a tool-call message or a partial parallel result batch leaves
    invalid provider context. Such intermediate positions are not navigable.
    """
    pending: set[str] = set()
    result: list[HistoryPoint] = []
    for index, message in enumerate(messages):
        if isinstance(message, AssistantMessage):
            pending.update(part.id for part in message.content if isinstance(part, ToolCall))
        elif isinstance(message, ToolResultMessage):
            pending.discard(message.tool_call_id)
        if pending:
            continue
        content = getattr(message, "content", "")
        preview = content if isinstance(content, str) else " ".join(
            part.text for part in content if isinstance(part, TextContent)
        )
        if isinstance(message, UserMessage) and not preview:
            preview = "[attachment]"
        result.append(HistoryPoint(index, str(getattr(message, "role", "message")), preview[:240]))
    return tuple(result)


def validate_checkpoint(messages: Sequence[AgentMessage], index: int) -> None:
    if index == -1 and not messages:
        return
    if type(index) is not int or index not in {point.index for point in history_points(messages)}:
        raise ValueError("Checkpoint is unavailable or splits a tool-call/result batch")
