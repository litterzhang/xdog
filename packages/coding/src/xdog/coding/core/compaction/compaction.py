"""Compaction logic: summarize conversation history to free context space."""

from __future__ import annotations

from typing import Any

from xdog.agent import AgentMessage
from xdog.agent.agent import Agent
from xdog.agent.compaction import (
    SUMMARIZE_SYSTEM_PROMPT as SUMMARIZE_SYSTEM_PROMPT,
)
from xdog.agent.compaction import (
    conversation_text,
    extract_text,
    summary_context,
)
from xdog.ai.types import UserMessage
from xdog.coding.core.compaction.utils import split_messages
from xdog.coding.core.defaults import COMPACTION_TARGET_RATIO
from xdog.coding.core.prompt_templates import COMPACTION_SUMMARY_TEMPLATE


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

    text = conversation_text(old_messages)

    # Use the agent's stream_fn to summarize
    summary = (summary_override if summary_override is not None
               else await _summarize(text, agent, instructions))

    # Build the compacted history
    summary_text = COMPACTION_SUMMARY_TEMPLATE.format(summary=summary)
    summary_msg = UserMessage(content=summary_text)

    return [summary_msg] + recent_messages


def _extract_text(msg: Any) -> str:
    """Compatibility wrapper for the shared text projection."""
    return extract_text(msg)


async def _summarize(conversation_text: str, agent: Agent, instructions: str = "") -> str:
    """Use the selected session adapter, including extension/custom providers."""
    from xdog.agent.summarization import stream_summary
    return await stream_summary(agent.stream_fn, agent.state.model,
                                summary_context(conversation_text, instructions), agent.options)
