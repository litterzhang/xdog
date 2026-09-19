"""Per-group runtime — workspace, transcript store, memory, tools, goals.

``GroupRuntime`` holds all mutable runtime resources for a group and
owns the session lifecycle (caching, resets, steer/follow_up/abort).
Workspace management (identity files, system prompt, bootstrap) is
inlined here since it's exclusively a group concern.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

from xdog.agent import AgentConfig, AgentTool, StreamFn
from xdog.agent.skills import SkillManager
from xdog.ai.types import SystemPromptBlock
from xdog.claw.core.compaction.flush_runner import FlushRunner
from xdog.claw.core.compaction.summarizer import Summarizer
from xdog.claw.core.memory.manager import MemoryManager
from xdog.claw.core.persistence.transcript_convert import TOOL_RESULT_ROLE, entry_text
from xdog.claw.core.persistence.transcript_store import TranscriptStore
from xdog.claw.core.planning.goal_manager import GoalManager
from xdog.claw.core.prompt import build_system_prompt, init_workspace, workspace_path
from xdog.claw.core.runtime.display_events import display_arguments, display_result
from xdog.claw.core.tools import create_tools
from xdog.claw.core.types import Group

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Model resolution
# ---------------------------------------------------------------------------

def resolve_model_name(model_id: str, fallback: str = "") -> str:
    """Resolve a model ID to a usable model name."""
    if not model_id:
        return fallback
    try:
        import xdog.ai as ai
        runtime = ai.load()
        m = runtime.model(model_id)
        if m is not None:
            return m.id
        return model_id
    except Exception as exc:
        logger.warning("Failed to resolve model %r, using as-is: %s", model_id, exc)
        return model_id or fallback


def _get_model_limits(model_id: str) -> tuple[int, int]:
    try:
        import xdog.ai as ai
        runtime = ai.load()
        m = runtime.model(model_id)
        if m is not None:
            return (m.context_window or 200_000, m.max_prompt_tokens or 0)
    except Exception:
        pass
    return (200_000, 0)


class GroupRuntime:
    """Runtime state for a registered group.

    Owns resources (workspace, transcript store, memory, goals, tools)
    and the session lifecycle (AgentSession caching, resets, steering).

    Use ``GroupRuntime.create()`` to build from a ``Group`` config.
    Direct construction is available for testing.
    """

    def __init__(
        self,
        *,
        group: Group,
        data_dir: Path,
        model: str = "",
        context_window: int = 200_000,
        max_prompt_tokens: int = 0,
        stream_fn: StreamFn | None = None,
        agent_config: AgentConfig | None = None,
        workspace_dir: Path | None = None,
        transcript_store: TranscriptStore | None = None,
        memory: MemoryManager | None = None,
        goal_manager: GoalManager | None = None,
        skill_manager: SkillManager | None = None,
        flush_runner: FlushRunner | None = None,
        summarizer: Summarizer | None = None,
        send_fn: Callable[..., Awaitable[None]] | None = None,
    ) -> None:
        self.group = group
        self.data_dir = data_dir
        self.model = model
        self.context_window = context_window
        self.max_prompt_tokens = max_prompt_tokens
        self.stream_fn = stream_fn
        self.agent_config = agent_config or group.agent_config
        self.workspace_dir = workspace_dir or data_dir / "groups" / group.id / "workspace"
        self.transcript_store = transcript_store or TranscriptStore(
            data_dir / "groups" / group.id / "sessions",
        )
        self.memory = memory
        self.goal_manager = goal_manager or GoalManager(
            goals_file=data_dir / "groups" / group.id / "goals.json",
            goals_dir=data_dir / "goals",
            model=model,
            stream_fn=stream_fn,
        )
        self.skill_manager = skill_manager or SkillManager(
            shared_dir=data_dir / "skills",
            group_dir=(workspace_dir or data_dir / "groups" / group.id / "workspace") / "skills",
        )
        self.flush_runner = flush_runner
        self.summarizer = summarizer
        self._send_fn = send_fn
        self._enabled_tools = group.enabled_tools
        self._tools: list[AgentTool] | None = None

        # Session lifecycle state
        from xdog.claw.core.runtime.session import AgentSession
        self._active_session: AgentSession | None = None
        self._session_last_active: float = 0.0

        # Memory snapshot cache (frozen per-turn, reload on mtime change)
        self._memory_snapshot: str | None = None
        self._memory_mtime: float = 0.0

    @classmethod
    def create(
        cls,
        group: Group,
        data_dir: Path,
        *,
        model: str = "",
        stream_fn: StreamFn | None = None,
        send_fn: Callable[..., Awaitable[None]] | None = None,
    ) -> GroupRuntime:
        """Build a fully-initialized GroupRuntime from a Group config."""
        cfg = group.agent_config
        resolved_model = resolve_model_name(cfg.model, model)
        ctx_window, max_prompt = _get_model_limits(resolved_model)

        group_dir = data_dir / "groups" / group.id
        ws = Path(group.workspace) if group.workspace else workspace_path(group_dir)
        # Seed identity files with the configured agent name (falls back to the
        # group id) so IDENTITY.md matches config instead of the generic default.
        init_workspace(ws, agent_name=group.name or group.id)

        # Build embed_fn from the ai provider for API-based embedding fallback
        embed_fn = None
        if resolved_model:
            try:
                import xdog.ai as ai
                from xdog.agent.helpers import embed_fn_from_provider
                provider = ai.load()
                if provider.active_providers():
                    embed_fn = embed_fn_from_provider(provider, resolved_model)
            except Exception:
                pass  # No API embedding available — local only

        memory = MemoryManager(ws, group_dir, group.id, embed_fn=embed_fn)
        flush_runner = FlushRunner(resolved_model, stream_fn) if resolved_model else None
        summarizer = Summarizer(resolved_model) if resolved_model else None
        goal_manager = GoalManager(
            goals_file=group_dir / "goals.json",
            goals_dir=data_dir / "goals",
            model=resolved_model,
            stream_fn=stream_fn,
        )

        return cls(
            group=group,
            data_dir=data_dir,
            model=resolved_model,
            context_window=ctx_window,
            max_prompt_tokens=max_prompt,
            stream_fn=stream_fn,
            agent_config=cfg,
            workspace_dir=ws,
            transcript_store=TranscriptStore(group_dir / "sessions"),
            memory=memory,
            goal_manager=goal_manager,
            skill_manager=SkillManager(
                shared_dir=data_dir / "skills",
                group_dir=ws / "skills",
            ),
            flush_runner=flush_runner,
            summarizer=summarizer,
            send_fn=send_fn,
        )

    # -- Tools -----------------------------------------------------------------

    @property
    def tools(self) -> list[AgentTool]:
        if self._tools is None:
            self._tools = create_tools(
                enabled=self._enabled_tools, workspace_dir=self.workspace_dir, image_model=self.group.image_model,
            )
        return self._tools

    @property
    def reindex_fn(self) -> Any:
        return self.memory.reindex_fn if self.memory else None

    # -- Memory snapshot -------------------------------------------------------

    def get_memory_snapshot(self) -> str:
        """Return cached memory content. Reload only when file changes."""
        memory_file = self.workspace_dir / "MEMORY.md"
        if memory_file.exists():
            mtime = memory_file.stat().st_mtime
            if mtime != self._memory_mtime:
                self._memory_snapshot = memory_file.read_text(encoding="utf-8")
                self._memory_mtime = mtime
        return self._memory_snapshot or ""

    # -- System prompt ---------------------------------------------------------

    def build_system_prompt(
        self, goals_summary: str = "", bootstrap_content: str | None = None
    ) -> tuple[SystemPromptBlock, ...]:
        return build_system_prompt(
            self.workspace_dir,
            tools=self.tools,
            model=self.model,
            goals_summary=goals_summary,
            bootstrap_content=bootstrap_content,
            memory_content=self.get_memory_snapshot(),
        )

    def goals_summary(self) -> str:
        return self.goal_manager.goals_summary()

    # -- Session lifecycle -----------------------------------------------------

    def get_or_create_session(self) -> Any:
        from xdog.claw.core.runtime.session import AgentSession

        group_id = self.group.id
        config = self.group.config

        if self.transcript_store.needs_daily_reset(group_id, config.daily_reset_hour):
            self._drop_session()
            self.transcript_store.reset_session(group_id)
        elif self.transcript_store.needs_idle_reset(group_id, config.idle_reset_seconds):
            self._drop_session()
            self.transcript_store.reset_session(group_id)

        meta = self.transcript_store.get_active_session(group_id)
        if meta is None:
            meta = self.transcript_store.create_session(group_id)

        if self._active_session is not None and self._active_session.meta.session_id == meta.session_id:
            self._session_last_active = time.time()
            return self._active_session

        if self._active_session is not None:
            self._active_session.dispose()
        session = AgentSession(runtime=self, session_meta=meta)
        self._active_session = session
        self._session_last_active = time.time()
        return session

    def reset_session(self) -> None:
        self._drop_session()
        self.transcript_store.reset_session(self.group.id)

    def steer(self, content: str) -> None:
        if self._active_session is not None:
            self._active_session.steer(content)

    def follow_up(self, content: str) -> None:
        if self._active_session is not None:
            self._active_session.follow_up(content)

    def abort(self) -> None:
        if self._active_session is not None:
            self._active_session.abort()

    async def wait_for_idle(self) -> None:
        if self._active_session is not None:
            await self._active_session.agent.wait_for_idle()

    # -- Goal facades ----------------------------------------------------------

    def has_running_goals(self) -> bool:
        return self.goal_manager.has_active_goals()

    def pop_goal_notifications(self) -> list[Any]:
        return self.goal_manager.pop_notifications()

    # -- Status queries --------------------------------------------------------

    def get_session_info(self) -> dict[str, Any]:
        group_id = self.group.id
        info: dict[str, Any] = {}
        meta = self.transcript_store.get_active_session(group_id)
        if meta:
            info["session_id"] = meta.session_id
            info["turn_count"] = meta.turn_count
            transcript = self.transcript_store.load_transcript(meta.session_id)
            info["history_format"] = 2
            info["history"] = [
                display_entry
                for entry in transcript
                if (display_entry := _display_history_entry(entry)) is not None
            ]
            info["usage"] = _sum_transcript_usage(transcript)
        if self.model:
            info["model"] = self.model
        return info

    def get_new_session_id(self) -> str | None:
        meta = self.transcript_store.get_active_session(self.group.id)
        return meta.session_id if meta else None

    def _drop_session(self) -> None:
        if self._active_session is not None:
            self._active_session.dispose()
            self._active_session = None
        self._session_last_active = 0.0


def _display_history_entry(entry: dict[str, Any]) -> dict[str, Any] | None:
    """Build a bounded, redacted display projection from a transcript entry."""
    role = entry.get("role")
    if role not in ("user", "assistant", TOOL_RESULT_ROLE):
        return None
    content = entry.get("content")
    if not isinstance(content, list):
        text = display_result(str(content or ""))
        return {"role": role, "content": text, "channel": entry.get("channel", "")}

    parts: list[dict[str, Any]] = []
    for part in content[:16]:
        if not isinstance(part, dict):
            continue
        part_type = part.get("type")
        if part_type == "text":
            parts.append({"type": "text", "text": display_result(str(part.get("text", "")))})
        elif part_type == "thinking" and not part.get("redacted"):
            parts.append({"type": "thinking", "thinking": display_result(str(part.get("thinking", "")))})
        elif part_type == "toolCall":
            arguments = part.get("arguments")
            parts.append({
                "type": "toolCall",
                "id": str(part.get("id", "")),
                "name": str(part.get("name", "tool")),
                "arguments": display_arguments(arguments) if isinstance(arguments, dict) else {},
            })

    projected: dict[str, Any] = {"role": role, "content": parts}
    for key in ("channel", "tool_call_id", "tool_name", "is_error"):
        if key in entry:
            projected[key] = entry[key]
    return projected


def _sum_transcript_usage(transcript: list[dict[str, Any]]) -> dict[str, int]:
    total = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    has_any = False
    for entry in transcript:
        usage = entry.get("usage")
        if usage and entry.get("role") == "assistant":
            has_any = True
            total["input"] += usage.get("input", 0)
            total["output"] += usage.get("output", 0)
            total["cache_read"] += usage.get("cache_read", 0)
            total["cache_write"] += usage.get("cache_write", 0)
    if not has_any:
        input_chars = sum(
            len(entry_text(e)) for e in transcript
            if e.get("role") in ("user", TOOL_RESULT_ROLE)
        )
        output_chars = sum(
            len(entry_text(e)) for e in transcript if e.get("role") == "assistant"
        )
        total["input"] = max(1, input_chars // 4) if input_chars else 0
        total["output"] = max(1, output_chars // 4) if output_chars else 0
    return total
