"""Compaction logic: summarize conversation history to free context space."""

from __future__ import annotations

from typing import Any

from xdog.agent import AgentMessage
from xdog.agent.agent import Agent
from xdog.ai.types import AssistantMessage, Context, TextContent, ToolCall, ToolResultMessage, UserMessage
from xdog.coding.core.compaction.utils import split_messages
from xdog.coding.core.defaults import COMPACTION_TARGET_RATIO
from xdog.coding.core.prompt_templates import COMPACTION_SUMMARY_TEMPLATE

SUMMARIZE_SYSTEM_PROMPT = """\
You are a conversation summarizer. You will receive a portion of a conversation \
between a user and a coding assistant. Produce a concise summary that captures:

1. What tasks were discussed and their current status.
2. Key decisions made.
3. Important code changes or file paths mentioned.
4. Any outstanding issues or next steps.

Be concise but ensure no critical context is lost. Output only the summary text.
"""


def prepare_index(messages: list[AgentMessage], agent: Agent, *, force: bool = False,
                  context_limit: int | None = None) -> int:
    """Pure preparation shared by the hook and the eventual summary commit."""
    from xdog.coding.core.compaction.utils import estimate_history_tokens
    limit = min(agent.context_window, agent.max_prompt_tokens or agent.context_window)
    if context_limit is not None:
        if type(context_limit) is not int or context_limit <= 0:
            raise ValueError('Compaction context limit must be positive')
        limit = min(limit, context_limit)
    target_tokens = max(1, int(limit * COMPACTION_TARGET_RATIO))
    if force:
        target_tokens = min(target_tokens, max(1, estimate_history_tokens(messages) // 2))

    # Split into "old" (to summarize) and "recent" (to keep)
    old_messages, recent_messages = split_messages(messages, target_tokens)
    # Retain a complete recent user turn, not orphaned tool results or a partial
    # assistant/tool exchange. No safe old turn means no destructive compaction.
    cut = len(old_messages)
    from xdog.agent.extensions.compaction import retained_turn_start
    return retained_turn_start(messages, cut)

async def compact_messages(
    messages: list[AgentMessage],
    agent: Agent,
    *, instructions: str = "", force: bool = False, prepared_index: int | None = None,
    summary_override: str | None = None,
) -> list[AgentMessage]:
    """Compact the conversation by summarizing older messages.

    Keeps recent messages intact and summarizes older ones into a single
    summary message. Returns a new (shorter) message list.
    """
    from xdog.agent.summarization import summary_instructions
    instructions = summary_instructions(instructions)
    cut = prepare_index(messages, agent, force=force) if prepared_index is None else prepared_index
    old_messages, recent_messages = messages[:cut], messages[cut:]

    if not old_messages:
        return list(messages)

    # Build text representation of old messages for summarization
    text_parts: list[str] = []
    for msg in old_messages:
        if isinstance(msg, UserMessage):
            text = msg.content if isinstance(msg.content, str) else _extract_text(msg)
            if text:
                text_parts.append(f"[USER]: {text}")
        elif isinstance(msg, AssistantMessage):
            text = _extract_text(msg)
            calls = [f"[TOOL CALL {part.name}]: {part.arguments}" for part in msg.content
                     if isinstance(part, ToolCall)]
            if text or calls:
                text_parts.append(f"[ASSISTANT]: {text}" + "\n" + "\n".join(calls))
        elif isinstance(msg, ToolResultMessage):
            text_parts.append(f"[TOOL RESULT {msg.tool_name}, error={msg.is_error}]: {_extract_text(msg)}")
        else:
            from xdog.agent.extensions.context_message import summary_text as context_summary_text
            if custom_text := context_summary_text(msg):
                text_parts.append('[EXTENSION CONTEXT]: ' + custom_text)

    conversation_text = "\n\n".join(text_parts)

    # Use the agent's stream_fn to summarize
    summary = (summary_override if summary_override is not None
               else await _summarize(conversation_text, agent, instructions))

    # Build the compacted history
    summary_text = COMPACTION_SUMMARY_TEMPLATE.format(summary=summary)
    summary_msg = UserMessage(content=summary_text)

    return [summary_msg] + recent_messages


def _extract_text(msg: Any) -> str:
    """Extract concatenated text from a message's content parts."""
    parts: list[str] = []
    content = getattr(msg, "content", None)
    if isinstance(content, str):
        return content
    if content is None:
        return ""
    for part in content:
        if isinstance(part, TextContent):
            parts.append(part.text)
    return "\n".join(parts)


async def _summarize(conversation_text: str, agent: Agent, instructions: str = "") -> str:
    """Use the selected session adapter, including extension/custom providers."""
    from xdog.agent.summarization import stream_summary
    request = conversation_text
    if instructions:
        request += "\n\nUser's compaction focus (not a request to execute tools):\n" + instructions
    return await stream_summary(agent.stream_fn, agent.state.model,
        Context(system_prompt=SUMMARIZE_SYSTEM_PROMPT, messages=(UserMessage(content=request),), tools=()),
        agent.options)
