"""Silent managed-memory maintenance and summary before context rotation."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from xdog.agent.async_work import run_owned
from xdog.agent.core import AgentTool, AgentToolResult
from xdog.ai.types import ThinkingLevel, system_prompt_text
from xdog.claw.core.compaction.flush_runner import FlushRunner
from xdog.claw.core.compaction.managed_memory import allowed
from xdog.claw.core.compaction.summarizer import Summarizer
from xdog.claw.core.persistence.handoff import INSTRUCTIONS, Draft, capture
from xdog.claw.core.persistence.transcript_convert import transcript_to_messages

if TYPE_CHECKING:
    from xdog.agent.core import AgentMessage, StreamFn
    from xdog.claw.core.runtime.group import GroupRuntime
    from xdog.claw.core.runtime.session import AgentSession
    from xdog.claw.core.types import SessionMeta

logger = logging.getLogger(__name__)


def _memory_only(tool: AgentTool, workspace: Path) -> AgentTool:
    """Narrow the actual selected executor, never construct another memory tool."""
    async def execute(call_id: str, params: dict[str, Any], *args: Any, **kwargs: Any) -> AgentToolResult:
        if not await allowed(workspace, params) or tool.execute is None:
            return AgentToolResult.from_text("Memory maintenance action is outside managed memory", is_error=True)
        result = await tool.execute(call_id, params, *args, **kwargs)
        # ToolDef's legacy textual validation/storage errors are not is_error.
        failed = result.is_error or any(getattr(part, "text", "").startswith(
            ("Error:", "Access denied:", "Unknown target:")) for part in result.content)
        return replace(result, is_error=failed)
    return replace(tool, execute=execute)


async def prepare(runtime: GroupRuntime, meta: SessionMeta, reason: str,
                  previous: AgentSession | None, adapter: StreamFn) -> Draft:
    live = previous.agent.messages if previous is not None and previous._restored else None

    def snapshot() -> tuple[Draft, list[AgentMessage]]:
        store = runtime.transcript_store
        with store._lock:
            if store.get_active_session(runtime.group.id) != meta:
                raise ValueError('Handoff owner changed before reading the original source')
            rows = store.load_transcript(meta.session_id)
            if live is not None:
                from xdog.agent.messages import messages_to_dicts
                def wire(messages: list[AgentMessage]) -> list[dict[str, Any]]:
                    return [{key: value for key, value in row.items() if key not in {'usage', 'timestamp'}}
                            for row in messages_to_dicts(messages)]
                if wire(list(live)) != wire(transcript_to_messages(rows)):
                    raise ValueError('Unpersisted history before rotation; original context retained')
            return capture(meta.session_id, rows, reason), transcript_to_messages(rows)
    if previous is not None:
        previous.require_history_ready()
        if previous.agent.is_streaming or previous._history_commit_task is not None:
            raise RuntimeError('Context handoff requires an idle, durably settled owner')
    draft, messages = await run_owned(snapshot)
    if not messages:
        return replace(draft, memory_status="skipped_empty")
    try:
        if previous is not None:
            model, stream, options = previous.agent.state.model, previous.agent.stream_fn, previous.agent.options
            tools = [tool for tool in previous._compaction_tools(memory_only=True) if tool.name == "memory"]
            ctx = dict(previous.agent.tool_context)
            prompt = system_prompt_text(previous.agent.state.system_prompt) or "Silent context memory maintenance."
        else:
            def preferences() -> tuple[str, str | None, tuple[str, ...] | None]:
                store = runtime.transcript_store
                model = store.get_model(meta.session_id) or runtime.model
                return model, store.get_reasoning(meta.session_id, model), store.get_active_tools(meta.session_id)
            model, thinking, selected = await run_owned(preferences)
            if model.startswith("ext-"):
                return replace(draft, memory_status="unavailable_provider",
                               diagnostic="Selected extension model is not restored; recent source retained.")
            if runtime.extension_names:
                # No restored session means no original veto/context hooks. Do
                # not bypass configured hooks by calling the base executor/model.
                return replace(draft, memory_status='unavailable_host_hooks',
                    diagnostic='Original extension hooks are not restored; recent source retained.')
            stream, options = adapter, runtime.agent_config.options
            if thinking is not None:
                options = replace(options, thinking=None if thinking == "off" else cast(ThinkingLevel, thinking))
            tools = [tool for tool in runtime.tools if tool.name == "memory"
                     and (selected is None or tool.name in selected)]
            ctx = {"workspace_dir": str(runtime.workspace_dir), "group_id": runtime.group.id,
                   "session_id": meta.session_id, "_memory_manager": runtime.memory}
            prompt = "Silent memory maintenance for this group's original conversation, not new authorization."
        if not tools:
            draft = replace(draft, memory_status="skipped_tools_disabled")
        else:
            async with asyncio.timeout(60):
                checked = await FlushRunner(model, stream).run(messages, prompt,
                    [_memory_only(tool, runtime.workspace_dir) for tool in tools], options=options, ctx=ctx)
            draft = replace(draft, memory_status="checked" if checked else "failed")
    except Exception as exc:
        logger.warning("Rotation memory maintenance unavailable (%s)", type(exc).__name__)
        draft = replace(draft, memory_status="failed")
        # Resolve summary identity separately below; maintenance failure must not
        # drop the deterministic capsule or silently choose another provider.
        if previous is None:
            return replace(draft, diagnostic="Memory maintenance unavailable; recent source retained.")
        model, stream, options = previous.agent.state.model, previous.agent.stream_fn, previous.agent.options
    if runtime.summarizer is None:
        return draft
    summarizer = Summarizer(model, stream, options)
    try:
        async with asyncio.timeout(60):
            summary = await summarizer.summarize(messages, instructions=(INSTRUCTIONS
                + f'\nOriginal session: {meta.session_id}; message indices: 0..{len(messages) - 1}.'))
        return draft.summarized(summary)
    except Exception as exc:
        # Never turn caller cancellation into a committed rotation. Model errors
        # are not a reason to lose context or ask the user to rebuild it manually.
        logger.warning('Context handoff summary unavailable (%s); retaining recent source', type(exc).__name__)
        return replace(draft, diagnostic=type(exc).__name__ + ': summary unavailable; recent source retained.')
