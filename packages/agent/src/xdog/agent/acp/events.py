"""Incremental runtime-to-ACP projection without replaying streamed text twice."""

from __future__ import annotations

try:
    from acp.schema import AgentMessageChunk, AgentThoughtChunk, TextContentBlock, ToolCallProgress, ToolCallStart
except ImportError as exc:
    raise ImportError("ACP requires Python 3.12–3.14 and agent-client-protocol>=0.12.1,<0.13") from exc
from xdog.agent.acp.contracts import AcpUpdate
from xdog.agent.events import (
    AgentEvent,
    MessageEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
)
from xdog.ai.types import AssistantMessage, TextContent


class EventProjector:
    def __init__(self) -> None:
        self.text = ""

    def project(self, event: AgentEvent) -> tuple[AcpUpdate, ...]:
        if isinstance(event, MessageStartEvent):
            self.text = ""
        if isinstance(event, MessageUpdateEvent):
            delta = event.assistant_message_event
            if delta is not None and delta.type == "text_delta":
                self.text += delta.delta
                return (
                    AgentMessageChunk.model_validate(
                        {
                            "sessionUpdate": "agent_message_chunk",
                            "content": TextContentBlock(type="text", text=delta.delta),
                        }
                    ),
                )
            if delta is not None and delta.type == "thinking_delta":
                return (
                    AgentThoughtChunk.model_validate(
                        {
                            "sessionUpdate": "agent_thought_chunk",
                            "content": TextContentBlock(type="text", text=delta.delta),
                        }
                    ),
                )
        if isinstance(event, MessageEndEvent) and isinstance(event.message, AssistantMessage):
            complete = "".join(part.text for part in event.message.content if isinstance(part, TextContent))
            tail = complete[len(self.text) :] if complete.startswith(self.text) else ""
            self.text = complete
            if tail:
                return (
                    AgentMessageChunk.model_validate(
                        {"sessionUpdate": "agent_message_chunk", "content": TextContentBlock(type="text", text=tail)}
                    ),
                )
        if isinstance(event, ToolExecutionStartEvent):
            return (
                ToolCallStart.model_validate(
                    {
                        "sessionUpdate": "tool_call",
                        "toolCallId": event.tool_call_id,
                        "title": event.tool_name,
                        "status": "in_progress",
                        "rawInput": event.args,
                    }
                ),
            )
        if isinstance(event, ToolExecutionEndEvent):
            return (
                ToolCallProgress.model_validate(
                    {
                        "sessionUpdate": "tool_call_update",
                        "toolCallId": event.tool_call_id,
                        "status": "failed" if event.is_error else "completed",
                        "rawOutput": str(event.result),
                    }
                ),
            )
        return ()
