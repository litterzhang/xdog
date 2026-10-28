"""Session-owned compaction operations; no independent execution owner."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from xdog.agent.async_work import run_owned
from xdog.claw.core.compaction import estimate_tokens, run_compaction, should_compact
from xdog.claw.core.persistence.transcript_convert import (
    transcript_to_messages,
)

if TYPE_CHECKING:
    from xdog.claw.core.runtime.session import AgentSession

logger = logging.getLogger(__name__)


async def compact_history(session: AgentSession, *, force: bool = False, instructions: str = "",
                          alive: Callable[[], bool] = lambda: True) -> dict[str, Any]:
    from xdog.agent.extensions.compaction import (
        CompactionVeto,
        chosen_boundary,
        record,
        retained_turn_start,
        saved_record,
    )
    from xdog.agent.extensions.runtime import silent_extension_ui
    from xdog.agent.messages import messages_to_dicts
    from xdog.agent.summarization import summary_instructions
    from xdog.claw.core.compaction.flush_runner import FlushRunner
    from xdog.claw.core.compaction.summarizer import Summarizer
    from xdog.claw.core.compaction.transcript import find_cut_point
    summary_instructions(instructions)
    if session._compacting:
        raise ValueError("Compaction already owns this session")
    session._compacting = True
    try:
        from xdog.claw.core.persistence.transcript_convert import live_transcript_snapshot
        original = session._agent.state.messages
        stored = await run_owned(session._store.load_transcript, session._meta.session_id)
        transcript = await run_owned(live_transcript_snapshot, original, stored)
        before = len(transcript)
        result: dict[str, Any] = {"session_id": session._meta.session_id, "before": before,
                                  "after": before, "changed": False}
        token_est = estimate_tokens(transcript)
        context_window = min(session._runtime.context_window,
                             session._agent.max_prompt_tokens or session._runtime.context_window)
        target = max(1, int(context_window * 0.5))
        if force:
            target = min(target, max(1, token_est // 2))
        elif not should_compact(session._meta.turn_count, token_est, context_window):
            return result
        cut = retained_turn_start(list(original), find_cut_point(transcript, target))
        if before < 4 or cut == 0:
            return result
        if not alive():
            raise ValueError("Compaction owner changed; original history retained")
        await session.initialize_extensions(require_model=True)
        previous = next((saved_record(entry.get('compaction')) for entry in reversed(transcript)
                         if entry.get('is_compaction') is True), None)
        outcome = await session.extensions.before_compaction({
            'messages': messages_to_dicts(list(original)), 'first_kept_index': cut,
            'original_count': len(original), 'tokens_before': token_est, 'context_limit': context_window,
            'automatic': not force, 'instructions': instructions, 'previous': previous,
        })
        if outcome.cancel:
            if not force:
                return result
            raise CompactionVeto('Compaction canceled by extension ' + outcome.source + ': ' + outcome.reason)
        cut = chosen_boundary(outcome, list(original), cut)
        metadata = record(outcome, cut, len(original))
        ws = session._runtime.workspace_dir
        from xdog.ai.types import system_prompt_text
        sp = system_prompt_text(session._agent.state.system_prompt) or ""
        if not sp:
            sp = system_prompt_text(await session._prompt_blocks()) or ""
        # Model selection/extension adapters are session-owned. Never use a
        # stale initial model or quietly send an extension model to ai.load().
        summarizer = session._runtime.summarizer
        if isinstance(summarizer, Summarizer):
            summarizer = Summarizer(session._agent.state.model, session._agent.stream_fn, session._agent.options)
        flush = session._runtime.flush_runner
        if isinstance(flush, FlushRunner):
            flush = FlushRunner(session._agent.state.model, session._agent.stream_fn)
        revision = session._runtime._source_revision
        compacted = await run_compaction(
            transcript=transcript, messages=list(original), system_prompt=sp,
            tools=session._compaction_tools(), context_window=context_window, group_id=session._group_id,
            flush_runner=flush, summarizer=summarizer,
            conversations_dir=ws / "conversations" if ws else None,
            reindex_fn=session._runtime.reindex_fn, instructions=instructions, target_tokens=target,
            summary_override=outcome.summary, first_kept_index=cut,
        )
        if not alive() or session._agent.state.messages != original or session._runtime._source_revision != revision:
            raise ValueError("Compaction owner changed; original history retained")
        if compacted == transcript:
            return result
        import uuid
        recovery_id = 'compaction-' + uuid.uuid4().hex
        number = 1 + sum(branch.get('recovery_kind') == 'compaction'
                         for branch in session._store.branch_metadata(session._meta.session_id))
        recovery = {'label': f"Before compaction #{number} ({'manual' if force else 'automatic'})",
                    'recovery_kind': 'compaction', 'branch_point': len(original) - 1,
                    'model': session._agent.state.model, 'access': session.access.state.to_dict(),
                    'extension_entries': session.extensions.entries.snapshot(),
                    'active_tools': (list(session._agent.active_tool_selection)
                                     if session._agent.active_tool_selection is not None else None)}
        metadata = {**metadata, 'recovery_branch_id': recovery_id}
        compacted[0]['compaction'] = metadata
        compacted_messages = transcript_to_messages(compacted)
        async def commit() -> None:
            # A failed checkpoint write cannot discard original context.
            # If replacement later fails, the durable extra branch is safe
            # and inspectable; it is not a false compaction-success receipt.
            await run_owned(session._store.save_compaction_checkpoint, session._meta.session_id,
                                    recovery_id, transcript, metadata=recovery)
            await run_owned(session._store.replace_transcript, session._meta.session_id, compacted)
            session._runtime._source_revision += 1
            session._agent.replace_messages(compacted_messages)
            session._source_channels = {index: str(entry.get("channel", ""))
                                     for index, entry in enumerate(compacted)}
            session._source_tools.clear()
            session._source_call_ids.clear()
            session._persisted_count = len(compacted_messages)
            session._compaction_metadata = metadata
            session._has_compaction_entry = True
            with silent_extension_ui():
                await session.extensions.event('session_compact', {'compaction': {
                    **metadata, 'automatic': not force, 'before': before, 'after': len(compacted)}})
        task = asyncio.create_task(commit())
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if not task.cancelled():
                task.exception()
            raise
        return {**result, "after": len(compacted), "changed": True, "recovery_branch_id": recovery_id}
    finally:
        session._compacting = False
