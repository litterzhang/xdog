"""Transcript ↔ Message conversion, plus usage estimation.

The conversion is `xdog.agent.messages`. claw used to have its own, which
flattened message content to a string — dropping every image, every thinking
block including its signature, and all but the first part of a tool result. A
restored session was quietly a different conversation from the saved one, and
for extended reasoning the lost signature broke the chain outright.

So a transcript entry is now the agent's own lossless dict with claw's per-entry
metadata alongside it: `timestamp` on every entry and `usage` on assistant turns.
The parser tolerates the extra keys, so the two sit together without a wrapper.

Reading an entry's text or its tool calls means looking inside `content`, which
is a list of typed parts rather than a string — `entry_text` and
`entry_tool_calls` do that, and are the only things that should.
"""
from __future__ import annotations

import time
from typing import Any

from xdog.agent import AgentMessage
from xdog.agent.messages import dicts_to_messages, messages_to_dicts
from xdog.ai.types import (
    AssistantMessage,
    TextContent,
    ToolResultMessage,
    UserMessage,
)

#: The role a tool result carries in the lossless format. claw's old format
#: said "tool"; anything still comparing against that silently never matches.
TOOL_RESULT_ROLE = "toolResult"


def extract_final_text(
    messages: tuple[AgentMessage, ...],
    previous_count: int = 0,
    *, empty: str = "(no response)",
) -> str:
    """Extract text from all new assistant messages in this turn."""
    new_msgs = messages[previous_count:]
    text_parts: list[str] = []
    for msg in new_msgs:
        if isinstance(msg, AssistantMessage):
            for part in msg.content:
                if isinstance(part, TextContent) and part.text:
                    text_parts.append(part.text)
    return "\n\n".join(text_parts) if text_parts else empty


def estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token."""
    return max(1, len(text) // 4) if text else 0


def estimate_turn_usage(
    messages: tuple[AgentMessage, ...],
    previous_count: int,
    system_prompt: str,
) -> dict[str, int]:
    """Estimate usage when no real usage data is available."""
    total = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}

    for msg in messages[previous_count:]:
        if isinstance(msg, AssistantMessage):
            text = ""
            for part in msg.content:
                if isinstance(part, TextContent):
                    text += part.text
            total["output"] += estimate_tokens(text)

    input_chars = len(system_prompt)
    for msg in messages[:previous_count + 1]:
        if isinstance(msg, UserMessage):
            input_chars += len(msg.content) if isinstance(msg.content, str) else 0
        elif isinstance(msg, AssistantMessage):
            for part in msg.content:
                if isinstance(part, TextContent):
                    input_chars += len(part.text)
        elif isinstance(msg, ToolResultMessage):
            for result_part in msg.content:
                if isinstance(result_part, TextContent):
                    input_chars += len(result_part.text)
    total["input"] = max(1, input_chars // 4) if input_chars else 0

    return total


def messages_to_transcript(
    messages: tuple[AgentMessage, ...] | list[AgentMessage],
) -> list[dict[str, Any]]:
    """Lossless message dicts, with claw's per-entry metadata alongside."""
    entries = messages_to_dicts(list(messages))
    for message, entry in zip(messages, entries, strict=True):
        entry["timestamp"] = time.time()
        usage = getattr(message, "usage", None)
        if isinstance(message, AssistantMessage) and usage and usage.total_tokens > 0:
            entry["usage"] = {
                "input": usage.input,
                "output": usage.output,
                "cache_read": usage.cache_read,
                "cache_write": usage.cache_write,
            }
    return entries


def live_transcript_snapshot(messages: tuple[AgentMessage, ...], stored: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Capture ALL current messages, retaining disk-only metadata for unchanged rows.

    A failed/delayed turn save cannot make a compaction checkpoint omit the live
    tail. This pure materialization runs off-loop on an admitted immutable state.
    """
    current = messages_to_transcript(messages)
    old_wire = messages_to_dicts(transcript_to_messages(stored))
    new_wire = messages_to_dicts(list(messages))
    for index in range(min(len(stored), len(current))):
        before = {key: value for key, value in old_wire[index].items() if key not in {'usage', 'timestamp'}}
        after = {key: value for key, value in new_wire[index].items() if key not in {'usage', 'timestamp'}}
        if before == after:
            current[index] = dict(stored[index])
    return current


def transcript_to_messages(transcript: list[dict[str, Any]]) -> list[AgentMessage]:
    """Rebuild messages from a transcript. claw's own keys are ignored."""
    normalized = []
    for entry in transcript:
        value = dict(entry)
        # Owned summaries are conversation context, not a fabricated system
        # message or Python repr of the transcript metadata envelope.
        if (value.get("role") == "system" and value.get("is_compaction") is True
                and isinstance(value.get("content"), str)):
            value["role"] = "user"
        if value.get("role") == "assistant" and isinstance(value.get("content"), str):
            value["content"] = [{"type": "text", "text": value["content"]}]
        normalized.append(value)
    return dicts_to_messages(normalized)


def entry_text(entry: dict[str, Any]) -> str:
    """The displayable text of an entry, joined across its text parts."""
    if entry.get('role') == 'custom' and entry.get('type') == 'xdog.claw.handoff.v1':
        from xdog.claw.core.persistence.handoff import text
        return text(entry.get('data', {}))
    if entry.get('role') == 'custom' and entry.get('type') == 'xdog.extension_context.v1':
        from xdog.agent.extensions.context_message import display_text
        return display_text(entry.get('data', {}))
    if entry.get("role") == "custom" and entry.get("type") == "xdog.session_input.v1":
        from xdog.agent.session_input import render_input_data
        return render_input_data(entry.get("data"))
    if entry.get('role') == 'custom' and entry.get('type') == 'xdog.claw.actor.v1':
        from xdog.agent.core import CustomAgentMessage
        from xdog.claw.core.identity.context import project
        value = project(CustomAgentMessage(entry['type'], entry.get('data', {})))
        return value.content if isinstance(value, UserMessage) and isinstance(value.content, str) else ''
    content = entry.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "".join(
        part.get("text", "")
        for part in content
        if isinstance(part, dict) and part.get("type") == "text"
    )


def entry_tool_calls(entry: dict[str, Any]) -> list[dict[str, Any]]:
    """The tool calls an assistant entry made."""
    content = entry.get("content")
    if not isinstance(content, list):
        return []
    return [
        part for part in content
        if isinstance(part, dict) and part.get("type") == "toolCall"
    ]
