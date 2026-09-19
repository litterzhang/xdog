"""Agent session — owns a long-lived Agent and manages one conversation.

Holds the Agent instance, runs turns, manages compaction, and
persists transcripts. Session lifecycle (creation, resets, caching)
is managed by ``GroupRuntime``.
"""
from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from xdog.agent import (
    AfterToolCallContext,
    AfterToolCallResult,
    Agent,
    AgentConfig,
    BeforeToolCallContext,
    BeforeToolCallResult,
    MessageEndEvent,
    MessageUpdateEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
)
from xdog.ai.types import (
    AssistantMessage,
    TextContent,
    TextDeltaEvent,
)
from xdog.claw.core.compaction import estimate_tokens, run_compaction, should_compact
from xdog.claw.core.persistence.transcript_convert import (
    estimate_turn_usage as _estimate_turn_usage,
)
from xdog.claw.core.persistence.transcript_convert import (
    extract_final_text,
    messages_to_transcript,
    transcript_to_messages,
)
from xdog.claw.core.prompt.workspace import run_bootstrap
from xdog.claw.core.runtime.display_events import (
    AssistantTextDelta,
    DisplayEvent,
    ToolFinished,
    ToolStarted,
    ToolUpdated,
)
from xdog.claw.core.types import GroupInput, SessionMeta, SystemInput, UserInput

if TYPE_CHECKING:
    from xdog.claw.core.runtime.group import GroupRuntime

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TurnResult:
    """Result of an agent turn execution."""

    response_text: str = ""
    tool_calls: tuple[dict[str, Any], ...] = ()
    error: str | None = None
    usage: dict[str, int] | None = None


@dataclass(frozen=True)
class _DrainResult:
    """Internal: data collected from draining the agent event stream."""

    tool_calls: tuple[dict[str, Any], ...] = ()
    usage: dict[str, int] = field(default_factory=lambda: {
        "input": 0, "output": 0, "cache_read": 0, "cache_write": 0,
    })


class AgentSession:
    """Holds a long-lived Agent and manages one conversation session.

    Responsibilities:
    - Owns the Agent instance (created once, reused across turns)
    - Rebuilds system prompt each turn
    - Runs turns: prompt -> event stream -> persist -> compaction check
    - Delegates steer/follow_up/abort to Agent
    """

    def __init__(self, *, runtime: GroupRuntime, session_meta: SessionMeta) -> None:
        self._runtime = runtime
        self._meta = session_meta
        self._group_id = runtime.group.id

        # Create the long-lived Agent
        effective_stream_fn = runtime.stream_fn
        if effective_stream_fn is None:
            import xdog.ai as ai
            from xdog.agent.helpers import stream_fn_from_provider
            effective_stream_fn = stream_fn_from_provider(ai.load())

        from xdog.agent.helpers import model_supports_tool_calls

        self._agent = Agent(
            effective_stream_fn,
            config=AgentConfig(
                model=runtime.model or "test/dummy",
                supports_tool_calls=model_supports_tool_calls(runtime.model or ""),
                system_prompt="",  # rebuilt each turn
                context_window=runtime.context_window,
                options=runtime.agent_config.options,
            ),
            tools=list(runtime.tools),
            # The index goes in the system prompt and a loaded body goes after
            # it; the Agent decides both, so the group's prompt builder no
            # longer carries a skills summary of its own.
            skills=getattr(runtime, "skill_manager", None),
            tool_ctx={
                "group_id": self._group_id,
                "workspace_dir": str(runtime.workspace_dir) if runtime.workspace_dir else "",
                # The key the filesystem tool actually reads. claw was setting
                # `workspace_dir`, which only its own memory tool consults, so a
                # relative path from the agent resolved against the process cwd.
                "fs_workspace": str(runtime.workspace_dir) if runtime.workspace_dir else "",
                "data_dir": str(runtime.data_dir),
                "_send_fn": runtime._send_fn,
                "_memory_search": runtime.memory.search if runtime.memory else None,
                "_memory_manager": runtime.memory,
                "_skill_manager": runtime.skill_manager,
                "_goal_manager": runtime.goal_manager,
            },
            before_tool_call=self._before_tool_call,
            after_tool_call=self._after_tool_call,
        )

        self._persisted_count = 0
        self._restored = False

    @property
    def agent(self) -> Agent:
        return self._agent

    @property
    def meta(self) -> SessionMeta:
        return self._meta

    # -- Helpers ---------------------------------------------------------------

    @property
    def _store(self) -> Any:
        """Shortcut to the transcript store."""
        return self._runtime.transcript_store

    # -- Message restore / clear -----------------------------------------------

    def _restore_messages(self) -> None:
        """Load existing session messages into the agent. Called once on first turn."""
        if self._restored:
            return
        self._restored = True
        transcript = self._store.load_transcript(self._meta.session_id)
        if not transcript:
            return
        messages = transcript_to_messages(transcript)
        if messages:
            self._agent.replace_messages(messages)
            self._persisted_count = len(messages)

    def clear_messages(self) -> None:
        self._agent.clear_messages()
        self._persisted_count = 0

    def dispose(self) -> None:
        pass

    def _persist(self) -> None:
        """Crash-safe: persist new messages without metadata."""
        messages = self._agent.state.messages
        if len(messages) <= self._persisted_count:
            return
        new_transcript = messages_to_transcript(messages[self._persisted_count:])
        for entry in new_transcript:
            self._store.append_turn(self._meta.session_id, entry)
        self._persisted_count = len(messages)

    # -- Hooks ----------------------------------------------------------------

    async def _before_tool_call(
        self, ctx: BeforeToolCallContext, cancel: asyncio.Event | None = None,
    ) -> BeforeToolCallResult | None:
        logger.info("Tool call: %s (group=%s)", ctx.tool_call.name, self._group_id)
        return None

    async def _after_tool_call(
        self, ctx: AfterToolCallContext, cancel: asyncio.Event | None = None,
    ) -> AfterToolCallResult | None:
        return None

    # -- Public API -----------------------------------------------------------

    def steer(self, content: str) -> None:
        self._agent.steer(content)

    def follow_up(self, content: str) -> None:
        self._agent.follow_up(content)

    def abort(self) -> None:
        self._agent.abort()

    # -- Branching ---------------------------------------------------------------

    def create_branch(self, *, at_index: int | None = None) -> str | None:
        import uuid
        messages = list(self._agent.state.messages)
        branch_point = at_index if at_index is not None else len(messages) - 1
        if branch_point < 0:
            return None
        branch_id = uuid.uuid4().hex[:8]
        snapshot = messages_to_transcript(messages[:branch_point + 1])
        self._store.save_branch(self._meta.session_id, branch_id, snapshot)
        return branch_id

    def restore_branch(self, branch_id: str) -> bool:
        transcript = self._store.load_branch(self._meta.session_id, branch_id)
        if transcript is None:
            return False
        messages = transcript_to_messages(transcript)
        self._agent.replace_messages(messages)
        self._persisted_count = len(messages)
        self._store.replace_transcript(self._meta.session_id, transcript)
        return True

    def list_branches(self) -> list[str]:
        branches: list[str] = self._store.list_branches(self._meta.session_id)
        return branches

    # -- Turn execution --------------------------------------------------------

    async def run_turn(
        self,
        input: GroupInput,
        on_text_delta: Any = None,
        *,
        on_display_event: Any = None,
        on_assistant_message: Callable[[str], Awaitable[None]] | None = None,
    ) -> TurnResult:
        """Execute a turn, optionally delivering each completed assistant message.

        Message delivery contains only public text, not reasoning or tool
        results. ``response_text`` still aggregates the turn for non-streaming
        callers; callers using message delivery must not send it again.
        """
        if on_display_event is None and on_text_delta is not None:
            def _compat_display_event(event: DisplayEvent) -> None:
                if isinstance(event, AssistantTextDelta):
                    on_text_delta(event.delta)
            on_display_event = _compat_display_event
        self._restore_messages()
        try:
            system_prompt = self._rebuild_system_prompt(input)
            await self._maybe_compact()

            previous_count = len(self._agent.state.messages)
            event_stream = await self._agent.prompt(input.content)
            turn = await self._drain_events(event_stream, on_display_event, on_assistant_message)

            if self._agent.cancellation_requested:
                self._persist_turn(input, turn.usage)
                return TurnResult(error="aborted")
            if self._agent.state.error:
                return TurnResult(error=self._agent.state.error)
            if len(self._agent.state.messages) <= previous_count + 1:
                return TurnResult(error="Agent produced no response")

            final_text = extract_final_text(
                self._agent.state.messages,
                previous_count=previous_count + 1,
            )

            usage = turn.usage
            if usage["input"] == 0 and usage["output"] == 0:
                usage = _estimate_turn_usage(
                    self._agent.state.messages, previous_count, system_prompt,
                )

            logger.info(
                "Turn usage for group %s: input=%d output=%d cache_read=%d cache_write=%d",
                self._group_id, usage.get("input", 0), usage.get("output", 0),
                usage.get("cache_read", 0), usage.get("cache_write", 0),
            )

            self._persist_turn(input, usage)

            return TurnResult(
                response_text=final_text,
                tool_calls=turn.tool_calls,
                usage=usage,
            )

        except asyncio.CancelledError:
            # The channel worker can be cancelled during gateway shutdown.
            # Cancelling the event consumer alone leaves the Agent's producer
            # running (and potentially executing tools) in the background.
            if self._agent.state.is_streaming:
                self._agent.abort()
                await self._agent.wait_for_idle()
            self._persist()
            raise
        except Exception as e:
            logger.error("Agent turn failed for group %s: %s", self._group_id, e)
            self._persist()
            return TurnResult(error=str(e))

    # -- Turn sub-steps -------------------------------------------------------

    def _rebuild_system_prompt(self, input: GroupInput) -> str:
        bootstrap_content = None
        ws = self._runtime.workspace_dir
        if ws:
            bootstrap_content = run_bootstrap(ws)

        goals_summary = self._runtime.goals_summary()
        blocks = self._runtime.build_system_prompt(
            goals_summary=goals_summary,
            bootstrap_content=bootstrap_content,
        )

        self._agent.set_system_prompt(blocks)
        # Return plain text for usage estimation
        from xdog.ai.types import system_prompt_text
        return system_prompt_text(blocks) or ""

    async def _drain_events(
        self, event_stream: Any, on_display_event: Any = None,
        on_assistant_message: Callable[[str], Awaitable[None]] | None = None,
    ) -> _DrainResult:
        tool_calls: list[dict[str, Any]] = []
        usage: dict[str, int] = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}

        async for event in event_stream:
            if isinstance(event, MessageUpdateEvent):
                inner = event.assistant_message_event
                if on_display_event and isinstance(inner, TextDeltaEvent):
                    on_display_event(AssistantTextDelta(inner.delta))

            elif isinstance(event, ToolExecutionStartEvent):
                tool_calls.append({
                    "id": event.tool_call_id,
                    "name": event.tool_name,
                    "arguments": event.args,
                })
                if on_display_event:
                    on_display_event(ToolStarted(
                        tool_call_id=event.tool_call_id,
                        name=event.tool_name,
                        arguments=dict(event.args),
                    ))

            elif isinstance(event, ToolExecutionUpdateEvent):
                if on_display_event and event.partial_result:
                    update_text = "\n".join(
                        part.text
                        for part in event.partial_result.content
                        if isinstance(part, TextContent)
                    )
                    on_display_event(ToolUpdated(
                        tool_call_id=event.tool_call_id,
                        name=event.tool_name,
                        result=update_text,
                    ))

            elif isinstance(event, ToolExecutionEndEvent):
                if on_display_event:
                    text_parts = [
                        part.text
                        for part in event.result.content
                        if isinstance(part, TextContent)
                    ] if event.result else []
                    on_display_event(ToolFinished(
                        tool_call_id=event.tool_call_id,
                        name=event.tool_name,
                        result="\n".join(text_parts),
                        is_error=event.is_error,
                    ))

            elif isinstance(event, MessageEndEvent):
                msg = event.message
                if not isinstance(msg, AssistantMessage):
                    continue
                if msg.usage and msg.usage.total_tokens > 0:
                    usage["input"] += msg.usage.input
                    usage["output"] += msg.usage.output
                    usage["cache_read"] += msg.usage.cache_read
                    usage["cache_write"] += msg.usage.cache_write
                if on_assistant_message and msg.stop_reason not in ("error", "aborted"):
                    text = "\n\n".join(
                        part.text for part in msg.content
                        if isinstance(part, TextContent) and part.text
                    )
                    if text.strip():
                        await on_assistant_message(text)

        return _DrainResult(tool_calls=tuple(tool_calls), usage=usage)

    def _persist_turn(self, input: GroupInput, usage: dict[str, int]) -> None:
        new_messages = self._agent.state.messages[self._persisted_count:]
        if not new_messages:
            return

        new_entries = messages_to_transcript(new_messages)

        # Tag the user message with its source channel so the TUI can
        # filter internal messages (goal_runner, scheduler) from chat
        # history and editor up-arrow.
        if isinstance(input, UserInput):
            channel = input.channel
        elif isinstance(input, SystemInput):
            channel = input.kind  # "goal_runner", "scheduler", etc.
        else:
            channel = ""

        if channel and new_entries:
            for entry in new_entries:
                if entry.get("role") == "user":
                    entry["channel"] = channel
                    break

        if usage and new_entries:
            for entry in reversed(new_entries):
                if entry.get("role") == "assistant":
                    entry["usage"] = dict(usage)
                    break

        for entry in new_entries:
            self._store.append_turn(self._meta.session_id, entry)
        self._persisted_count = len(self._agent.state.messages)
        self._meta = self._store.increment_turn(self._meta.session_id)

    async def _maybe_compact(self) -> None:
        """Check if compaction is needed and run the pipeline if so."""
        transcript = self._store.load_transcript(self._meta.session_id)
        token_est = estimate_tokens(transcript)
        context_window = self._runtime.context_window

        if not should_compact(self._meta.turn_count, token_est, context_window):
            return

        ws = self._runtime.workspace_dir

        from xdog.ai.types import system_prompt_text
        sp = system_prompt_text(self._agent.state.system_prompt) or ""
        if not sp:
            sp = system_prompt_text(self._runtime.build_system_prompt()) or ""

        compacted = await run_compaction(
            transcript=transcript,
            messages=list(self._agent.state.messages),
            system_prompt=sp,
            tools=list(self._runtime.tools),
            context_window=context_window,
            group_id=self._group_id,
            flush_runner=self._runtime.flush_runner,
            summarizer=self._runtime.summarizer,
            conversations_dir=ws / "conversations" if ws else None,
            reindex_fn=self._runtime.reindex_fn,
        )

        self._store.replace_transcript(self._meta.session_id, compacted)
        compacted_messages = transcript_to_messages(compacted)
        self._agent.replace_messages(compacted_messages)
        self._persisted_count = len(compacted_messages)


# ---------------------------------------------------------------------------
# Tool display helpers (module-level, called from _drain_events)
# ---------------------------------------------------------------------------

# Args that are internal metadata — not useful to show in TUI
_HIDDEN_ARGS = frozenset({"description", "timeout_ms", "dangerouslyDisableSandbox"})

# Per-arg truncation (longer for command, shorter for rest)
_ARG_MAX_LEN: dict[str, int] = {"command": 80}
_ARG_DEFAULT_MAX = 40

# Max chars for tool result display
_MAX_RESULT_DISPLAY = 200

# Lines that are just progress noise (pytest dots, etc.)
_NOISE_LINE_RE = re.compile(r'^[\s.Fsx\-=\[\]%\d/]+$')


def _emit_tool_start(on_text_delta: Any, tool_name: str, args: dict[str, Any]) -> None:
    """Emit a tool call marker into the text stream."""
    arg_parts: list[str] = []
    for k, v in args.items():
        if k == "action" or k in _HIDDEN_ARGS:
            continue
        val = str(v)
        max_len = _ARG_MAX_LEN.get(k, _ARG_DEFAULT_MAX)
        if len(val) > max_len:
            val = val[:max_len - 3] + "..."
        arg_parts.append(f"{k}={val}")

    action = args.get("action", "")
    label = f"{tool_name}:{action}" if action else tool_name
    arg_str = ", ".join(arg_parts)

    marker = f"\n\n\u2192 **{label}**"
    if arg_str:
        marker += f"  `{arg_str}`"
    marker += "\n"

    try:
        on_text_delta(marker)
    except Exception:
        pass


def _emit_tool_end(
    on_text_delta: Any, tool_name: str, result_text: str, is_error: bool,
) -> None:
    """Emit a tool result into the text stream."""
    if not result_text:
        marker = "\u2713 (done)\n\n"
        try:
            on_text_delta(marker)
        except Exception:
            pass
        return

    # Unescape literal \n from LLM-generated text
    display = result_text.replace("\\n", "\n")

    # Truncate long results
    if len(display) > _MAX_RESULT_DISPLAY:
        display = display[:_MAX_RESULT_DISPLAY - 3] + "..."

    # Find first meaningful line (skip progress dots, separators, blanks)
    lines = display.strip().split("\n")
    summary_line = ""
    extra_count = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped and not _NOISE_LINE_RE.match(stripped):
            summary_line = stripped
            extra_count = len(lines) - i - 1
            break
    if not summary_line:
        # All lines are noise — use last non-empty line
        for line in reversed(lines):
            if line.strip():
                summary_line = line.strip()
                break
        extra_count = max(0, len(lines) - 1)

    if is_error:
        marker = f"\u2717 {summary_line}"
    else:
        marker = f"\u2713 {summary_line}"

    if extra_count > 0:
        marker += f"  (+{extra_count} lines)"

    marker += "\n\n"

    try:
        on_text_delta(marker)
    except Exception:
        pass
