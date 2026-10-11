"""Compaction pipeline — orchestrates flush, summarize, archive, compact.

This is the conductor. The individual stages live in their own modules:
- ``flush_runner.py`` — silent agent turn to save facts to memory
- ``summarizer.py`` — direct LLM call for structured summary
- ``transcript.py`` — pure data manipulation (cut, archive, compact)
- ``prompts.py`` — trigger logic and prompt templates
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Awaitable, Callable

from xdog.agent.core import AgentMessage, AgentTool
from xdog.agent.extensions.runtime import _owned_disk
from xdog.agent.summarization import summary_instructions
from xdog.claw.core.compaction.transcript import (
    archive_transcript,
    compact_transcript,
    estimate_tokens,
    extract_previous_summary,
)

logger = logging.getLogger(__name__)


async def run_compaction(
    *,
    transcript: list[dict[str, Any]],
    messages: list[AgentMessage],
    system_prompt: str,
    tools: list[AgentTool],
    context_window: int,
    group_id: str,
    flush_runner: Any | None = None,
    summarizer: Any | None = None,
    conversations_dir: Path | None = None,
    reindex_fn: Callable[..., Awaitable[None]] | None = None,
    instructions: str = "", target_tokens: int | None = None,
    summary_override: str | None = None, first_kept_index: int | None = None,
) -> list[dict[str, Any]]:
    """Run the full compaction pipeline.

    Returns the compacted transcript (list of dicts). The caller is
    responsible for persisting it and converting to agent messages.

    Stages:
    1. Flush — silent agent turn saves durable facts to memory
    2. Summarize — direct LLM call produces structured summary
    3. Archive — save full transcript as markdown
    4. Compact — replace old turns with summary, keep recent
    """
    instructions = summary_instructions(instructions)
    logger.info(
        "Compaction triggered for group %s (tokens~%d, ctx=%d)",
        group_id, estimate_tokens(transcript), context_window,
    )

    # 1. Flush
    if flush_runner is not None:
        await flush_runner.run(messages, system_prompt, tools)

    # 2. Summarize
    previous_summary = extract_previous_summary(transcript)
    if summary_override is not None:
        summary = summary_override
    else:
        if summarizer is None:
            raise ValueError("Compaction summarizer unavailable; original history retained")
        summary = (await summarizer.summarize(messages, previous_summary, instructions=instructions)
                   if instructions else await summarizer.summarize(messages, previous_summary))
    if (not isinstance(summary, str) or not summary.strip()
            or summary.strip() in {"Conversation summary unavailable.", "(no summary)"}):
        raise ValueError("Compaction summary unavailable; original history retained")

    # 3. Archive
    if conversations_dir is not None:
        try:
            archive_path = await _owned_disk(archive_transcript, transcript, conversations_dir)
            if reindex_fn is not None:
                try:
                    await reindex_fn(archive_path, archive_path.name)
                except Exception:
                    logger.exception("Failed to reindex archived transcript")
        except Exception:
            logger.exception("Failed to archive transcript")

    # 4. Compact
    target_tokens = int(context_window * 0.5) if target_tokens is None else target_tokens
    return compact_transcript(transcript, summary=summary, target_tokens=target_tokens,
                              first_kept_index=first_kept_index)
