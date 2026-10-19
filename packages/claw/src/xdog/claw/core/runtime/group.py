"""Per-group runtime — workspace, transcript store, memory, tools, goals.

``GroupRuntime`` holds all mutable runtime resources for a group and
owns the session lifecycle (caching, resets, steer/follow_up/abort).
Workspace management (identity files, system prompt, bootstrap) is
inlined here since it's exclusively a group concern.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import replace
from importlib.util import find_spec
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable
from weakref import WeakSet

from xdog.agent import AgentConfig, AgentTool, StreamFn
from xdog.agent.history import history_points
from xdog.agent.mcp import McpManager
from xdog.agent.skills import SkillManager
from xdog.ai.types import ImageContent, SystemPromptBlock
from xdog.ai.utils.image_files import validate_references
from xdog.claw.core.compaction.flush_runner import FlushRunner
from xdog.claw.core.compaction.summarizer import Summarizer
from xdog.claw.core.memory.manager import MemoryManager
from xdog.claw.core.persistence.transcript_convert import TOOL_RESULT_ROLE, entry_text, transcript_to_messages
from xdog.claw.core.persistence.transcript_store import TranscriptStore
from xdog.claw.core.prompt import build_system_prompt, init_workspace, workspace_path
from xdog.claw.core.runtime.display_events import display_arguments, display_result
from xdog.claw.core.runtime.transcript_source import SourceCapture
from xdog.claw.core.tools import create_tools
from xdog.claw.core.types import Group
from xdog.tui.transcript_browser import TranscriptRecord

if TYPE_CHECKING:
    from xdog.agent.extensions.runtime import ExtensionRuntime
    from xdog.claw.core.inputs.consumer import InputService
    from xdog.claw.core.observers.manager import ManagedObservers
    from xdog.claw.core.runtime.goal_adapter import ClawGoalHost

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Model resolution
# ---------------------------------------------------------------------------

def resolve_model_name(model_id: str, fallback: str = "") -> str:
    """Resolve a model ID to a usable model name."""
    if not model_id:
        return fallback
    if model_id.startswith("ext-"):
        return model_id
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
    if model_id.startswith("ext-"):
        return (200_000, 0)  # Actual trusted metadata is applied before readiness/first turn.
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
        skill_manager: SkillManager | None = None,
        flush_runner: FlushRunner | None = None,
        summarizer: Summarizer | None = None,
        send_fn: Callable[..., Awaitable[None]] | None = None,
        observers: ManagedObservers | None = None,
        mcp_servers: dict[str, Any] | None = None,
        mcp_credential_backend: str = 'file',
        extensions: tuple[str, ...] = (), extension_dir: str = "",
        extension_settings: dict[str, dict[str, Any]] | None = None,
        extension_allow_tools: tuple[str, ...] = (),
        child_presets: dict[str, Any] | None = None,
    ) -> None:
        self.group = group
        self.data_dir = data_dir
        self.model = model
        self._configured_model = model
        self._configured_limits = context_window, max_prompt_tokens
        self.context_window = context_window
        self.max_prompt_tokens = max_prompt_tokens
        self.stream_fn = stream_fn
        self.agent_config = agent_config or group.agent_config
        self.workspace_dir = (workspace_dir or (Path(group.workspace) if group.workspace
                              else data_dir / "groups" / group.id / "workspace")).resolve()
        self.transcript_store = transcript_store or TranscriptStore(
            data_dir / "groups" / group.id / "sessions", owner_group=group.id,
        )
        self.memory = memory
        self.goal_host: ClawGoalHost | None = None
        self.capability_tools: tuple[AgentTool, ...] = ()
        self._goal_start: asyncio.Task[None] | None = None
        self.skill_manager = skill_manager or SkillManager(
            shared_dir=data_dir / "skills",
            group_dir=(workspace_dir or data_dir / "groups" / group.id / "workspace") / "skills",
        )
        self.flush_runner = flush_runner
        self.summarizer = summarizer
        self._send_fn = send_fn
        self.observers = observers
        self._enabled_tools = group.enabled_tools
        self._tools: list[AgentTool] | None = None
        self.mcp = McpManager(mcp_servers or {}, self.workspace_dir,
                              credential_dir=data_dir / "groups" / group.id / "mcp-credentials",
                              credential_backend=mcp_credential_backend)
        self.extension_names = extensions
        self.extension_root = Path(extension_dir) if extension_dir else data_dir / "extensions"
        self.extension_settings = extension_settings or {}
        self.extension_allow_tools = extension_allow_tools
        from xdog.agent.supervision_config import ChildPresets
        self.child_presets = ChildPresets(child_presets)
        # Closing worker/callbacks own their lifetime; do not retain settled histories.
        self._retired_extensions: WeakSet[ExtensionRuntime] = WeakSet()

        # Session lifecycle state
        from xdog.claw.core.runtime.session import AgentSession
        self._active_session: AgentSession | None = None
        self._session_last_active: float = 0.0
        self._source_revision = 0

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
        observers: ManagedObservers | None = None,
        mcp_servers: dict[str, Any] | None = None,
        mcp_credential_backend: str = 'file',
        extensions: tuple[str, ...] = (), extension_dir: str = "",
        extension_settings: dict[str, dict[str, Any]] | None = None,
        extension_allow_tools: tuple[str, ...] = (),
        child_presets: dict[str, Any] | None = None,
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

        return cls(
            group=group,
            data_dir=data_dir,
            model=resolved_model,
            context_window=ctx_window,
            max_prompt_tokens=max_prompt,
            stream_fn=stream_fn,
            agent_config=cfg,
            workspace_dir=ws,
            transcript_store=TranscriptStore(group_dir / "sessions", owner_group=group.id),
            memory=memory,
            skill_manager=SkillManager(
                shared_dir=data_dir / "skills",
                group_dir=ws / "skills",
            ),
            flush_runner=flush_runner,
            summarizer=summarizer,
            send_fn=send_fn, observers=observers, mcp_servers=mcp_servers,
            mcp_credential_backend=mcp_credential_backend,
            extensions=extensions, extension_dir=extension_dir,
            extension_settings=extension_settings, extension_allow_tools=extension_allow_tools,
            child_presets=child_presets,
        )

    # -- Tools -----------------------------------------------------------------

    @property
    def tools(self) -> list[AgentTool]:
        if self._tools is None:
            enabled = self._enabled_tools
            if self.goal_available and enabled is not None:
                enabled = tuple(name for name in enabled if name != "goal")
            self._tools = create_tools(
                enabled=enabled, workspace_dir=self.workspace_dir, image_model=self.group.image_model,
            )
        return [*self._tools, *self.mcp.tools(), *self.capability_tools]

    @property
    def goal_available(self) -> bool:
        """Installed capability, not a configuration or task-delegation switch."""
        return find_spec("xdog.goal") is not None

    async def initialize_goals(self, inputs: InputService) -> None:
        if not self.goal_available:
            return
        if self.goal_host is None:
            if self._goal_start is None:
                async def start() -> None:
                    from xdog.claw.core.runtime.goal_adapter import ClawGoalHost
                    self.goal_host = await ClawGoalHost.open(self, inputs)
                self._goal_start = asyncio.create_task(start())
            from xdog.agent.async_work import wait_owned
            opening = self._goal_start
            try:
                await wait_owned(opening)
            finally:
                if opening.done() and self._goal_start is opening:
                    self._goal_start = None
        if self._active_session is not None:
            assert self.goal_host is not None
            self.goal_host.attach(self._active_session)

    async def manage_mcp(self, action: str, server: str = "") -> tuple[dict[str, Any], ...]:
        if action in {"list", "status"}:
            return self.mcp.status()
        if self._active_session is not None:
            self._active_session._children_idle()
        if self._active_session is not None and self._active_session.agent.state.is_streaming:
            raise RuntimeError("MCP changes require an idle agent")
        if action == "connect":
            await self.mcp.connect(server)
        elif action == "disconnect":
            await self.mcp.disconnect(server)
        elif action == "refresh":
            await self.mcp.refresh(server)
        else:
            raise ValueError("Unknown MCP management action")
        if self._active_session is not None:
            self._active_session.agent.set_tools((*self.tools,
                                                  *self._active_session.extensions.tools(),
                                                  *self._active_session.children.supervisor.tools()))
            await self._active_session._persist_tool_retirement()
        return self.mcp.status()

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
        self, bootstrap_content: str | None = None
    ) -> tuple[SystemPromptBlock, ...]:
        tools = self.tools
        if self._active_session is not None:
            active = {tool.name for tool in self._active_session.agent.active_tools}
            tools = [tool for tool in tools if tool.name in active]
        return build_system_prompt(
            self.workspace_dir,
            tools=tools,
            model=self.model,
            bootstrap_content=bootstrap_content,
            memory_content=self.get_memory_snapshot(),
        )

    # -- Session lifecycle -----------------------------------------------------

    def get_or_create_session(self, *, allow_reset: bool = True) -> Any:
        from xdog.claw.core.runtime.session import AgentSession

        group_id = self.group.id
        config = self.group.config

        from xdog.agent.access import AccessState
        current = self.transcript_store.get_active_session(group_id)
        read_only = (current is not None and AccessState.from_dict(
            self.transcript_store.get_access(current.session_id)).read_only)
        children_active = self._active_session is not None and self._active_session.children.supervisor.active
        if (allow_reset and not read_only and not children_active
                and (self.transcript_store.needs_daily_reset(group_id, config.daily_reset_hour)
                     or self.transcript_store.needs_idle_reset(group_id, config.idle_reset_seconds))):
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
        saved_model = self.transcript_store.get_model(meta.session_id)
        self.model = saved_model or self._configured_model
        self.context_window, self.max_prompt_tokens = self._configured_limits
        session = AgentSession(runtime=self, session_meta=meta)
        self._active_session = session
        self._session_last_active = time.time()
        return session

    def navigation_entries(self, scope: str) -> list[dict[str, Any]]:
        store = self.transcript_store
        active = store.get_active_session(self.group.id)
        if scope == "sessions":
            return [{"id": data["session_id"], "kind": "session", "session_id": data["session_id"],
                     "title": data.get("label") or data.get("summary") or data["session_id"],
                     "detail": f"{data.get('message_count', '?')} messages · {data.get('last_active', '')}",
                     "active": active is not None and active.session_id == data["session_id"]}
                    for data in store.list_sessions(self.group.id)]
        parts = scope.split("|")
        if len(parts) != 3 or parts[0] != "tree":
            raise ValueError("Invalid navigation scope")
        session_id, branch_id = parts[1:]
        if session_id not in {data["session_id"] for data in store.list_sessions(self.group.id)}:
            raise ValueError("Session belongs to another group or is unavailable")
        transcript = store.load_branch(session_id, branch_id) if branch_id else store.load_transcript(session_id)
        if transcript is None:
            raise ValueError("Branch unavailable")
        branches = store.branch_metadata(session_id)
        parents = {data["branch_id"]: data.get("parent_branch", "") for data in branches}
        result = []
        if not branch_id:
            for data in branches:
                key = data["branch_id"]
                parent, seen = parents.get(key), {key}
                depth = 0
                while parent and parent in parents and parent not in seen:
                    depth += 1
                    seen.add(parent)
                    parent = parents[parent]
                result.append({"id": f"b:{key}", "kind": "branch", "session_id": session_id, "branch_id": key,
                               "title": data.get("label") or key,
                               "detail": f"{data.get('message_count', '?')} messages",
                               "depth": depth, "active": key == store.active_branch(session_id)})
        for point in history_points(transcript_to_messages(transcript)):
            result.append({"id": f"p:{branch_id}:{point.index}", "kind": "checkpoint", "session_id": session_id,
                           "branch_id": branch_id, "index": point.index,
                           "title": f"{point.index + 1}. {point.role}: {point.preview}",
                           "detail": "Read-only historical checkpoint; execution cannot return here"})
        return result

    def rename_session(self, expected_session: str, name: str,
                       alive: Callable[[], bool] = lambda: True, *, expected_name: str | None = None) -> str:
        from xdog.agent.session_name import session_name
        selected = session_name(name)
        if self._active_session is not None:
            if self._active_session.meta.session_id != expected_session:
                raise ValueError('Cached session owner changed; refresh before naming')
            self._active_session._children_idle()
            if self._active_session.agent.state.is_streaming:
                raise RuntimeError('Session naming requires an idle agent')
        if not alive():
            raise asyncio.CancelledError
        meta = self.transcript_store.rename_session(self.group.id, expected_session, selected, alive,
                                                     expected_name=expected_name)
        if self._active_session is not None:
            self._active_session._meta = meta
        return meta.label

    def navigate_session(self, action: str, session_id: str, branch_id: str, index: int,
                         expected_session: str, alive: Callable[[], bool] = lambda: True,
                         owner_loop: asyncio.AbstractEventLoop | None = None) -> None:
        """Compatibility boundary for context rotation, never history activation.

        A group has one logical main Agent. Saved transcripts and legacy branches
        are read-only evidence, not alternate execution routes.
        """
        if action != "new":
            raise ValueError("Claw history is read-only; resume/fork/branch/goto route switching is not supported")
        if session_id or branch_id or index != -1:
            raise ValueError("New context cannot select a historical session, branch or checkpoint")
        active = self.transcript_store.get_active_session(self.group.id)
        if (active.session_id if active is not None else "") != expected_session:
            raise ValueError("Active context changed; refresh before creating a new context")
        if not alive():
            raise asyncio.CancelledError
        self.reset_session()

    def reset_session(self) -> None:
        """Rotate an idle group's context without deleting or reassigning Goals."""
        if self._active_session is not None:
            self._active_session._children_idle()
            if self._active_session.agent.state.is_streaming:
                raise RuntimeError("Context reset requires an idle agent")
        self._drop_session()
        self.transcript_store.reset_session(self.group.id)

    def steer(self, content: str) -> None:
        if self._active_session is not None:
            self._active_session.steer(content)

    def follow_up(self, content: str) -> None:
        if self._active_session is not None:
            self._active_session.follow_up(content)

    def image_model_info(self) -> Any:
        if self.model.startswith("ext-") and self._active_session is not None:
            return self._active_session.extensions.provider_model(self.model)
        try:
            import xdog.ai as ai
            return ai.load().model(self.model)
        except Exception:
            return None

    def validate_images(self, images: tuple[ImageContent, ...]) -> None:
        validate_references(images, self.image_model_info())

    def control_input(
        self, action: str, input_id: str, content: str = "", delivery: str = "", direction: int = 0,
        images: tuple[ImageContent, ...] = (), *, images_validated: bool = False,
    ) -> bool:
        if self._active_session is None:
            return False
        agent = self._active_session.agent
        if action == "add":
            if len(agent.pending_inputs()) >= 50 or len(content.encode("utf-8")) > 64 * 1024:
                raise ValueError("Runtime queue is full or input exceeds 64 KiB")
            from typing import cast

            from xdog.agent import Delivery
            if not images_validated:
                self.validate_images(images)
            agent.queue_input(input_id, content, cast(Delivery, delivery), images)
            self._active_session._human_inputs[input_id] = 'tui'
            return True
        changed = agent.change_input(input_id, action, content, direction)
        if changed and action == 'remove':
            self._active_session._human_inputs.pop(input_id, None)
        return changed

    def pending_inputs(self, prefix: str) -> tuple[Any, ...]:
        if self._active_session is None:
            return ()
        return tuple(item for item in self._active_session.agent.pending_inputs() if item.id.startswith(prefix))

    def drain_inputs(self, prefix: str) -> tuple[Any, ...]:
        if self._active_session is None:
            return ()
        items = self._active_session.agent.drain_inputs(prefix)
        for item in items:
            self._active_session._human_inputs.pop(item.id, None)
        return items

    def abort(self) -> None:
        if self._active_session is not None:
            self._active_session.abort()

    async def wait_for_idle(self) -> None:
        if self._active_session is not None:
            await self._active_session.agent.wait_for_idle()

    # -- Status queries --------------------------------------------------------

    def get_session_info(self) -> dict[str, Any]:
        group_id = self.group.id
        info: dict[str, Any] = {"workspace_dir": str(self.workspace_dir.absolute())}
        meta = self.transcript_store.get_active_session(group_id)
        if meta:
            info["session_id"] = meta.session_id
            info["turn_count"] = meta.turn_count
            info["session_name"] = meta.label
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
            model = self.image_model_info()
            info["image_input"] = getattr(model, "supports_image_input", None)
            limits = getattr(model, "vision_limits", None)
            info["image_limits"] = {"count": getattr(limits, "max_prompt_images", 0) or 4,
                                    "bytes": getattr(limits, "max_prompt_image_size", 0) or 10 * 1024 * 1024,
                                    "types": list(getattr(limits, "supported_media_types", ())) }
        return info

    def capture_source(self, expected_session: str) -> SourceCapture:
        session_id = self.get_new_session_id() or ""
        if session_id != expected_session:
            raise ValueError("Active session changed; refresh context before reading source")
        if self._active_session is not None and self._active_session.meta.session_id == session_id:
            return replace(self._active_session.capture_source(), revision=self._source_revision)
        return SourceCapture(session_id,
                             lambda: self.transcript_store.load_transcript(session_id) if session_id else [],
                             revision=self._source_revision)

    def capture_saved_source(self, expected_session: str, target_session: str, branch_id: str) -> SourceCapture:
        if (self.get_new_session_id() or '') != expected_session:
            raise ValueError('Saved source owner changed')
        store = self.transcript_store
        def load() -> list[dict[str, Any]]:
            if target_session not in {row['session_id'] for row in store.list_sessions(self.group.id)}:
                raise ValueError('Saved source belongs to another group or is unavailable')
            if branch_id:
                result = store.load_branch(target_session, branch_id)
                if result is None:
                    raise ValueError('Saved branch unavailable')
            else:
                result = store.load_transcript(target_session)
            return result
        if branch_id == '@timeline':
            from xdog.agent.messages import messages_to_dicts
            from xdog.claw.core.persistence.transcript_convert import transcript_to_messages
            from xdog.claw.core.runtime.transcript_source import source_records
            from xdog.tui.history_timeline import TimelineSource, timeline_records
            live = (self._active_session.agent.state.messages if self._active_session is not None
                    and target_session == expected_session else None)
            def records(canceled: threading.Event) -> tuple[TranscriptRecord, ...]:
                if target_session not in {row['session_id'] for row in store.list_sessions(self.group.id)}:
                    raise ValueError('Timeline belongs to another group or is unavailable')
                branches = store.branch_metadata(target_session)
                if len(branches) >= 256:
                    raise ValueError('Timeline branch capacity exceeded; inspect sources separately')
                sources = []
                raw: dict[str, list[dict[str, Any]]] = {}
                for row in branches:
                    branch = store.load_branch(target_session, row['branch_id'])
                    if branch is None:
                        raise ValueError('Timeline branch disappeared')
                    raw[row['branch_id']] = branch
                    metadata = next((entry.get('compaction') for entry in branch
                                     if entry.get('is_compaction') is True), None)
                    sources.append(TimelineSource(row['branch_id'], row.get('label') or row['branch_id'],
                        tuple(messages_to_dicts(transcript_to_messages(branch))), row.get('parent_branch', ''),
                        metadata))
                head = store.load_transcript(target_session)
                raw['@head'] = head
                metadata = next((entry.get('compaction') for entry in head if entry.get('is_compaction') is True), None)
                sources.append(TimelineSource('@head', 'Current context',
                    tuple(messages_to_dicts(list(live) if live is not None else transcript_to_messages(head))),
                    store.active_branch(target_session), metadata))
                def formatted(source: TimelineSource) -> tuple[TranscriptRecord, ...]:
                    captured = SourceCapture(expected_session, lambda: raw[source.id],
                        messages=live if source.id == '@head' and live is not None
                        else tuple(transcript_to_messages(raw[source.id])),
                        channels=tuple((index, str(entry.get('channel', '')))
                                       for index, entry in enumerate(raw[source.id])))
                    return source_records(captured, canceled)
                return timeline_records(tuple(sources), format_source=formatted, canceled=canceled.is_set)
            return SourceCapture(expected_session, lambda: [], revision=self._source_revision, records_load=records)
        return SourceCapture(expected_session, load, revision=self._source_revision)

    def get_new_session_id(self) -> str | None:
        meta = self.transcript_store.get_active_session(self.group.id)
        return meta.session_id if meta else None

    def _drop_session(self) -> None:
        if self._active_session is not None:
            self._active_session._children_idle()
        self._source_revision += 1
        if self._active_session is not None:
            self._active_session.dispose()
            self._retired_extensions.add(self._active_session.extensions)
            self._active_session = None
        self._session_last_active = 0.0


def _display_history_entry(entry: dict[str, Any]) -> dict[str, Any] | None:
    """Build a bounded, redacted display projection from a transcript entry."""
    role = entry.get("role")
    if role == "custom" and entry.get("type") == "xdog.session_input.v1":
        from xdog.agent.session_input import decode_input
        try:
            value = decode_input(entry.get("data"))
        except ValueError:
            return None
        return {"role": "user", "channel": "acp", "message_id": value.id,
                "content": display_result(f"[External: {value.source}]\n{value.instruction}")}
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

    image_count = sum(1 for part in content if isinstance(part, dict) and part.get("type") == "image")
    projected: dict[str, Any] = {"role": role, "content": parts, "image_count": image_count}
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
