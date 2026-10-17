"""Agent session — owns a long-lived Agent and manages one conversation.

Holds the Agent instance, runs turns, manages compaction, and
persists transcripts. Session lifecycle (creation, resets, caching)
is managed by ``GroupRuntime``.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from xdog.agent import (
    AfterToolCallContext,
    AfterToolCallResult,
    Agent,
    AgentConfig,
    AgentTool,
    BeforeToolCallContext,
    BeforeToolCallResult,
    InputConsumedEvent,
    InputHandledEvent,
    InputRejectedEvent,
    InputTransformedEvent,
    MessageEndEvent,
    MessageUpdateEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
)
from xdog.agent.access import AccessScope, AccessState, read_only_call
from xdog.agent.context_messages import ContextMessages
from xdog.agent.extensions.dialogs import DialogBroker
from xdog.agent.extensions.runtime import ExtensionRuntime
from xdog.agent.extensions.state import ExtensionEntries, validate_entries
from xdog.agent.types import HandledInput
from xdog.ai.types import (
    AssistantMessage,
    ImageContent,
    SystemPromptBlock,
    TextContent,
    TextDeltaEvent,
    ToolCall,
    UserMessage,
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
from xdog.claw.core.runtime.child_agents import ClawChildren
from xdog.claw.core.runtime.display_events import (
    AssistantTextDelta,
    DisplayEvent,
    ExtensionNotice,
    InputConsumed,
    ToolFinished,
    ToolStarted,
    ToolUpdated,
)
from xdog.claw.core.runtime.transcript_source import SourceCapture, SourceTool
from xdog.claw.core.types import ExternalInput, GroupInput, SessionMeta, SystemInput, UserInput

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
    input_handled: bool = False
    session_id: str = ""
    retain_partial: bool = False
    canceled: bool = False


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
        self._compaction_metadata: dict[str, Any] | None = None
        self._has_compaction_entry = False
        self._compacting = False
        self._tool_selection_changing = False
        self._tool_selection_retiring = False
        self._saved_active_tools = runtime.transcript_store.get_active_tools(session_meta.session_id)
        self._tool_selection_restore_pending = self._saved_active_tools is not None
        self._active_skills = frozenset(runtime.transcript_store.get_active_skills(session_meta.session_id))
        try:
            self._host_loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        except RuntimeError:
            self._host_loop = None

        self.access = AccessScope(AccessState.from_dict(runtime.transcript_store.get_access(session_meta.session_id)),
                              lambda state: runtime.transcript_store.save_access(
                                  session_meta.session_id, state.to_dict()))
        from xdog.agent.supervision import RESERVED_TOOLS
        if any(tool.name in RESERVED_TOOLS for tool in runtime.tools):
            raise ValueError("agent_spawn/agent_control are reserved for session-owned metadata")
        self._load_extensions()
        # Create the long-lived Agent
        effective_stream_fn = runtime.stream_fn
        if effective_stream_fn is None:
            import xdog.ai as ai
            from xdog.agent.helpers import stream_fn_from_provider
            effective_stream_fn = stream_fn_from_provider(ai.load())

        from xdog.agent.core import ModelStreamFn
        native_metadata = (effective_stream_fn.resolve_model(runtime.model)
                           if isinstance(effective_stream_fn, ModelStreamFn) and not runtime.model.startswith("ext-")
                           else None)
        if (runtime.transcript_store.get_model(session_meta.session_id) and not runtime.model.startswith("ext-")
                and native_metadata is None):
            raise ValueError("Saved native model unavailable on configured adapter; no fallback")
        if (native_metadata is not None and runtime.transcript_store.get_model(session_meta.session_id)
                and (native_metadata.model_type != "chat" or not native_metadata.supports_streaming
                     or native_metadata.context_window <= 0)):
            raise ValueError("Saved native model is no longer a streaming chat model; no fallback")
        if native_metadata is not None:
            runtime.context_window = native_metadata.context_window or runtime.context_window
            runtime.max_prompt_tokens = native_metadata.max_prompt_tokens
        from xdog.agent.extensions.providers import ExtensionProviderStream
        from xdog.agent.helpers import model_supports_tool_calls
        saved_thinking = runtime.transcript_store.get_reasoning(session_meta.session_id, runtime.model or "test/dummy")
        options = runtime.agent_config.options
        if saved_thinking is not None:
            from typing import cast

            from xdog.ai.types import ThinkingLevel
            options = replace(options,
                              thinking=None if saved_thinking == "off" else cast(ThinkingLevel, saved_thinking))
            if options.thinking is not None and not (runtime.model or "").startswith("ext-"):
                from xdog.agent.core import ModelStreamFn
                metadata = (effective_stream_fn.resolve_model(runtime.model)
                            if isinstance(effective_stream_fn, ModelStreamFn) else None)
                if (metadata is None or not metadata.reasoning or metadata.supported_efforts is not None
                        and options.thinking not in metadata.supported_efforts):
                    raise ValueError("Saved reasoning is unavailable on the selected model; no fallback")
        self._agent = Agent(
            ExtensionProviderStream(self.extensions, effective_stream_fn),
            config=AgentConfig(
                model=runtime.model or "test/dummy",
                supports_tool_calls=(native_metadata.supports_tool_calls if native_metadata is not None
                                     else model_supports_tool_calls(runtime.model or "")),
                system_prompt="",  # rebuilt each turn
                context_window=runtime.context_window,
                max_prompt_tokens=runtime.max_prompt_tokens,
                options=options,
            ),
            tools=list(runtime.tools),
            # The index goes in the system prompt and a loaded body goes after
            # it; the Agent decides both, so the group's prompt builder no
            # longer carries a skills summary of its own.
            skills=getattr(runtime, "skill_manager", None),
            active_skills=tuple(sorted(self._active_skills)),
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
                "_task_scheduler": runtime.task_scheduler,
            },
            transform_context=self._extension_context,
            before_tool_call=self._before_tool_call,
            after_tool_call=self._after_tool_call,
            input_transform=self._transform_user_input,
        )

        self.children = ClawChildren(self)
        self._agent.set_tools((*self._agent.state.tools, *self.children.supervisor.tools()))
        if self._tool_selection_restore_pending:
            self._agent.set_active_tools([], expected_revision=self._agent.tool_selection_revision)
        self._input_is_user = True
        self._input_channel = ""
        self._persisted_count: int = 0
        self._restored = False
        self._source_channels: dict[int, str] = {}
        self._source_tools: dict[str, SourceTool] = {}
        self._source_call_ids: dict[str, str] = {}
        self._source_generation = 0
        from xdog.claw.core.runtime.context_messages import create
        self.context_messages: ContextMessages = create(self)

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
        from xdog.agent.extensions.compaction import saved_record
        self._has_compaction_entry = bool(transcript and transcript[0].get('is_compaction') is True)
        self._compaction_metadata = next((saved_record(entry.get('compaction')) for entry in reversed(transcript)
                                         if entry.get('is_compaction') is True), None)
        messages = transcript_to_messages(transcript)
        self._source_channels = {index: str(entry.get("channel", "")) for index, entry in enumerate(transcript)}
        if messages:
            self._agent.replace_messages(messages)
            self._persisted_count = len(messages)

    def clear_messages(self) -> None:
        self._children_idle()
        self._agent.clear_messages()
        self._compaction_metadata = None
        self._has_compaction_entry = False
        self._persisted_count = 0
        self._source_channels.clear()
        self._source_tools.clear()
        self._source_call_ids.clear()

    def dispose(self) -> None:
        self.children.supervisor.close()
        self.extension_dialogs.close()
        self.extensions.close()
        self.access.close()
        pass

    def _persist(self) -> None:
        """Crash-safe: persist new messages without metadata."""
        if hasattr(self, 'context_messages') and self.context_messages.uncertain:
            return
        messages = self._agent.state.messages
        if len(messages) <= self._persisted_count:
            return
        new_transcript = messages_to_transcript(messages[self._persisted_count:])
        for offset, entry in enumerate(new_transcript, self._persisted_count):
            channel = self._source_channels.get(offset)
            if channel:
                entry["channel"] = channel
            self._store.append_turn(self._meta.session_id, entry)
        self._persisted_count = len(messages)

    def capture_source(self) -> SourceCapture:
        """Capture immutable state on the owning loop; serialization runs off-loop."""
        state = self._agent.state
        session_id, store = self._meta.session_id, self._store
        return SourceCapture(
            session_id, lambda: store.load_transcript(session_id),
            messages=state.messages if self._restored else None,
            channels=tuple(self._source_channels.items()), partial=state.stream_message,
            tools=tuple(self._source_tools.values()),
        )

    def _source_tool_started(self, call_id: str, name: str, args: dict[str, Any]) -> None:
        source_id = f"live:{self._source_generation}:{call_id}"
        for index in range(len(self._agent.messages) - 1, -1, -1):
            message = self._agent.messages[index]
            if isinstance(message, AssistantMessage):
                found = next((number for number, part in enumerate(message.content)
                              if isinstance(part, ToolCall) and part.id == call_id), None)
                if found is not None:
                    source_id = f"m{index}:p{found}"
                    break
        self._source_call_ids[call_id] = source_id
        self._source_tools[source_id] = SourceTool(source_id, name, dict(args))

    def _source_tool_output(self, call_id: str, output: str, *, final: bool = False) -> None:
        source_id = self._source_call_ids.get(call_id)
        if source_id is not None and source_id in self._source_tools:
            previous = self._source_tools[source_id]
            self._source_tools[source_id] = replace(previous, output=output,
                                                    progress=previous.progress if final else output)

    def _load_extensions(self) -> None:
        session_id = self._meta.session_id
        store = self._runtime.transcript_store
        self.extension_dialogs = DialogBroker()
        entries = ExtensionEntries(store.get_extension_entries(session_id),
                                   lambda value: store.save_extension_entries(session_id, value))
        self.extensions = ExtensionRuntime(
            self._runtime.extension_names, self._runtime.extension_root, self._runtime.workspace_dir,
            settings=self._runtime.extension_settings, context=lambda: {"session_id": self._meta.session_id,
                "group_id": self._group_id, "cwd": str(self._runtime.workspace_dir), "model": self._runtime.model,
                "read_only": self.access.state.read_only, "permission_mode": "configured"}, entries=entries,
            inspection=self._extension_inspection)
        self.extensions.tool_inventory = self._extension_tools
        self.extensions.session_name = self._extension_session_name
        if hasattr(self, "_agent"):
            self._agent.set_transform_context(self._extension_context)
            from xdog.agent.extensions.providers import ExtensionProviderStream
            previous = self._agent.stream_fn
            base = previous.base if isinstance(previous, ExtensionProviderStream) else previous
            self._agent.set_stream_fn(ExtensionProviderStream(self.extensions, base))

    async def _extension_context(self, messages: list[Any]) -> list[Any]:
        try:
            # SDK-owned transient projection is shared with direct SDK callers.
            result = await self.extensions.transform(messages)
            return result
        finally:
            await self._sync_extension_tools()

    def _extension_session_name(self) -> dict[str, str] | None:
        from xdog.agent.session_name import name_snapshot
        try:
            meta = self._store.get_active_session(self._runtime.group.id)
            if meta is None or meta.session_id != self._meta.session_id:
                return None  # Unadmitted/stale sessions cannot borrow the active head's name.
            return name_snapshot({'session_id': meta.session_id, 'owner_id': meta.session_id, 'name': meta.label},
                                 session_id=self._meta.session_id, owner_id=self._meta.session_id)
        except (ValueError, TypeError, UnicodeError, RuntimeError, OSError):
            return None

    def _extension_inspection(self) -> dict[str, Any] | None:
        if not hasattr(self, "_agent"):
            return None
        from xdog.agent.core import ModelStreamFn
        from xdog.agent.extensions.inspection import capture
        from xdog.ai.types import Model
        stream = self._agent.stream_fn
        selected = stream.resolve_model(self._agent.state.model) if isinstance(stream, ModelStreamFn) else None
        return capture(self._agent, self._meta.session_id, self._meta.session_id,
                       selected if isinstance(selected, Model) else None,
                       context_window=self._runtime.context_window, max_prompt_tokens=self._runtime.max_prompt_tokens)

    def _extension_tools(self) -> dict[str, Any] | None:
        from xdog.agent.extensions.tool_inventory import capture
        if not hasattr(self, '_agent') or self.extensions._closed:
            return None
        return capture(self._agent, self._meta.session_id, self._meta.session_id)

    async def complete_command(self, command: str, prefix: str, identity: str, *,
                               timeout: float = 1, argument_input: dict[str, Any] | None = None) -> dict[str, Any]:
        """Read current registered completion data without command/model/UI authority."""
        try:
            return await self.extensions.complete_command(command, prefix, identity, timeout=timeout,
                                                          argument_input=argument_input)
        finally:
            await self._sync_extension_tools()

    async def initialize_extensions(self, *, require_model: bool = False) -> None:
        try:
            if self.extensions.enabled:
                await self.extensions.initialize()
            if self._agent.state.model.startswith("ext-"):
                model = self.extensions.provider_model(self._agent.state.model)
                if model is None and require_model:
                    raise ValueError("Selected extension model is not active; no primary-provider fallback")
                if (model is not None and require_model and self._agent.options.thinking is not None
                        and model.reasoning and model.supported_efforts is not None
                        and self._agent.options.thinking not in model.supported_efforts):
                    raise ValueError("Configured thinking level is not advertised by selected extension model")
                if model is not None and not self._agent.state.is_streaming:
                    self._agent.set_model_capabilities(context_window=model.context_window,
                        max_prompt_tokens=model.max_prompt_tokens, supports_tool_calls=model.supports_tool_calls)
                    self._runtime.context_window = model.context_window
                    self._runtime.max_prompt_tokens = model.max_prompt_tokens
        finally:
            await self._sync_extension_tools()

        if self._tool_selection_restore_pending:
            configured = {tool.name for tool in self._agent.state.tools}
            selected = tuple(name for name in self._saved_active_tools or () if name in configured)
            self._agent.set_active_tools(selected, expected_revision=self._agent.tool_selection_revision)
            self._tool_selection_restore_pending = False
            await self._persist_tool_retirement()

    async def _sync_extension_tools(self) -> None:
        self.extensions.sync_tools(self._agent)
        await self._persist_tool_retirement()

    async def _persist_tool_retirement(self) -> None:
        """Await durable narrowing before a callback/turn can acknowledge inventory retirement."""
        from xdog.agent.async_work import run_owned
        if self._tool_selection_restore_pending or self._runtime._active_session is not self:
            return  # Unadmitted prepared sessions publish their narrowed choice in the navigation journal.
        selected, saved = self._agent.active_tool_selection, self._saved_active_tools
        if selected is None or saved is None or not set(selected) < set(saved):
            return
        if self._tool_selection_retiring:
            raise RuntimeError('Tool retirement persistence already owns this session')
        revision, identifier = self._agent.tool_selection_revision, self._meta.session_id
        def precommit() -> None:
            if (self._runtime._active_session is not self or self._runtime.get_new_session_id() != identifier
                    or self._agent.tool_selection_revision != revision
                    or self._agent.active_tool_selection != selected):
                raise ValueError('Tool retirement owner/inventory changed before persistence')
        def commit() -> None:
            precommit()
            self._store.save_active_tools(identifier, selected, before_replace=precommit)
            self._saved_active_tools = selected
        self._tool_selection_retiring = True
        try:
            await run_owned(commit)  # Cancellation settles actual restriction, never restores retired tools.
        finally:
            self._tool_selection_retiring = False

    async def select_active_tools(self, names: list[str] | tuple[str, ...] | None, *, expected_revision: int,
                                  alive: Callable[[], bool] = lambda: True) -> None:
        """Owned disk commit; keep the group reserved until the real worker settles."""
        from xdog.agent.async_work import run_owned
        from xdog.agent.extensions.active_tools import names as validate_names
        self._children_idle()
        if self._tool_selection_restore_pending:
            raise ValueError('Tool selection requires initialized inventory')
        selected = validate_names(list(names) if isinstance(names, tuple) else names)
        owner = asyncio.current_task()
        runtime, agent, session_id = self._runtime, self._agent, self._meta.session_id
        def precommit() -> None:
            if (owner is None or owner.cancelling() or not alive() or runtime._active_session is not self
                    or runtime.get_new_session_id() != session_id or self._compacting):
                raise ValueError('Active-tool selection owner changed before commit')
            agent.validate_active_tools(selected, expected_revision=expected_revision)
        def commit() -> None:
            with self.children.supervisor.idle_scope():
                precommit()
                self._store.save_active_tools(session_id, selected, before_replace=precommit)
                # No cancellation/connection check after an admitted atomic replace:
                # an already persisted selection must also become the live selection.
                agent.set_active_tools(selected, expected_revision=expected_revision)
                self._saved_active_tools = selected
        self._tool_selection_changing = True
        try:
            await run_owned(commit)
        finally:
            self._tool_selection_changing = False

    # -- Hooks ----------------------------------------------------------------

    async def _transform_user_input(self, message: UserMessage, source: str,
                                    cancel: asyncio.Event | None = None) -> UserMessage | HandledInput:
        try:
            if source == "prompt" and not self._input_is_user:
                return message  # Goal/scheduler/system work is not intercepted as human input.
            return await self.extensions.input(message, source, channel=self._input_channel)
        finally:
            await self._sync_extension_tools()

    async def _before_tool_call(
        self, ctx: BeforeToolCallContext, cancel: asyncio.Event | None = None,
    ) -> BeforeToolCallResult | None:
        try:
            if self.access.state.read_only and not read_only_call(ctx.tool_call.name, ctx.args):
                return BeforeToolCallResult(block=True, reason="Host access scope is read-only")
            if self.extensions.enabled:
                await self.extensions.event("tool_start", {"tool_name": ctx.tool_call.name})
                from xdog.ai.utils.validation import validate_tool_arguments
                try:
                    effective = await self.extensions.pre(ctx.tool_call.name, dict(ctx.args))
                    if effective is None:
                        return BeforeToolCallResult(block=True, reason="Extension vetoed this tool call")
                    tool = next(tool for tool in self._agent.state.tools if tool.name == ctx.tool_call.name)
                    if tool.validate_arguments is not None:
                        checked = tool.validate_arguments(effective)
                        if inspect.isawaitable(checked):
                            await checked
                    elif not validate_tool_arguments(effective, tool.parameters).valid:
                        raise ValueError("Extension rewrote parameters outside the tool schema")
                    ctx.args.clear()
                    ctx.args.update(effective)
                except Exception as exc:
                    return BeforeToolCallResult(block=True, reason=str(exc))
            if self.access.state.read_only and not read_only_call(ctx.tool_call.name, ctx.args):
                return BeforeToolCallResult(block=True, reason="Host access scope is read-only")
            if ctx.tool_call.name in self.extensions.aliases:
                owner = self.extensions.tool_owner(ctx.tool_call.name)
                if owner not in self._runtime.extension_allow_tools:
                    return BeforeToolCallResult(
                        block=True, reason="Extension tool use disabled; configure extension_allow_tools")
            logger.info("Tool call: %s (group=%s)", ctx.tool_call.name, self._group_id)
            if any(tool.name == ctx.tool_call.name for tool in self._runtime.mcp.tools()):
                if not self._runtime.mcp.allowed(ctx.tool_call.name):
                    return BeforeToolCallResult(block=True, reason="External MCP tool use is disabled; "
                                                "configure allow_tools explicitly for this server")
            return None
        finally:
            await self._sync_extension_tools()

    async def _after_tool_call(
        self, ctx: AfterToolCallContext, cancel: asyncio.Event | None = None,
    ) -> AfterToolCallResult | None:
        try:
            if not self.extensions.enabled or ctx.is_error or any(not isinstance(part, TextContent)
                                                                  for part in ctx.result.content):
                return None
            text = "\n".join(part.text for part in ctx.result.content if isinstance(part, TextContent))
            try:
                result = await self.extensions.post(ctx.tool_call.name, ctx.args, text)
            except Exception:
                return None
            return AfterToolCallResult(content=(TextContent(text=result),)) if result != text else None
        finally:
            await self._sync_extension_tools()

    def _children_idle(self) -> None:
        if self._tool_selection_changing or self._tool_selection_retiring:
            raise RuntimeError('Active-tool persistence owns this session; wait/cancel before changing context')
        if self._compacting:
            raise RuntimeError("Compaction owns this session; cancel/wait before changing context")
        if self.children.supervisor.active:
            raise RuntimeError("Child work is active; cancel/wait before changing session context/policy")

    async def manage_children(self, action: str, identifier: str = "", text: str = "", access: str = "read-only",
                              config: dict[str, Any] | None = None, *,
                              images: tuple[ImageContent, ...] = (), return_owner: str = '',
                              return_alive: Callable[[], bool] = lambda: True) -> dict[str, Any]:
        if images and action not in {'start', 'steer', 'follow-up'}:
            raise ValueError('Child images belong only to start/steer/follow-up')
        self._host_loop = asyncio.get_running_loop()
        self._restore_messages()
        if action == "list":
            return self.children.supervisor.snapshot()
        if action == "presets":
            return {"presets": self.children.supervisor.presets.snapshot()}
        if action == "start":
            await self.initialize_extensions()
            self._agent.set_system_prompt(self._runtime.build_system_prompt())
            child_id = await self.children.supervisor.start(text, access=access, config=config, images=images)
            return {"child_id": child_id, 'accepted_input': self.children.supervisor.admission_receipt(child_id),
                    **self.children.supervisor.snapshot()}
        return await self.children.supervisor.control(action, identifier, text, images=images,
                                                      return_owner=return_owner, return_alive=return_alive)

    # -- Public API -----------------------------------------------------------

    async def skills_control(self, action: str, slug: str = '',
                             alive: Callable[[], bool] = lambda: True) -> dict[str, Any]:
        from xdog.agent.skills.manager import is_valid_skill_name
        if (not isinstance(action, str) or not isinstance(slug, str)
                or action not in {'list', 'load', 'unload'}
                or action != 'list' and slug != 'all' and not is_valid_skill_name(slug)):
            raise ValueError('Use /skills [load SLUG|unload SLUG|unload all]')
        manager = self._runtime.skill_manager
        if action != 'list':
            self._children_idle()
            if self._agent.is_streaming:
                raise ValueError('Skill selection requires an idle owning session')
            active = set(self._active_skills)
            if action == 'load':
                skill = await asyncio.to_thread(manager.load_skill, slug)
                if skill is None:
                    raise ValueError('Skill is unavailable in this group')
                active.add(slug)
            elif slug == 'all':
                active.clear()
            else:
                active.discard(slug)
            self._children_idle()
            if not alive() or self._agent.is_streaming:
                raise ValueError('Skill selection owner changed; no replay')
            chosen = tuple(sorted(active))
            await self._save_selected_skills(chosen)
        skills = await asyncio.to_thread(manager.list_skills)
        return {'session_id': self._meta.session_id, 'active': sorted(self._active_skills), 'skills': [
            {'slug': skill.slug, 'name': skill.name, 'description': skill.description,
             'scope': skill.scope, 'packaged': skill.packaged, 'active': skill.slug in self._active_skills}
            for skill in skills], 'notice': 'Selection changes instructions only; no model/tool/permission grant'}

    async def _save_selected_skills(self, chosen: tuple[str, ...], *, update_agent: bool = True) -> None:
        # Once a write is admitted, settle BOTH disk and owning-loop state before
        # releasing the group reservation, even if its receipt is disconnected.
        async def commit() -> None:
            await asyncio.to_thread(self._store.save_active_skills, self._meta.session_id, chosen)
            self._active_skills = frozenset(chosen)
            if update_agent:
                self._agent.set_active_skills(chosen)
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

    async def _expire_selected_skills(self) -> None:
        retained = self._active_skills & frozenset(self._agent.active_skills)
        if retained != self._active_skills:
            await self._save_selected_skills(tuple(sorted(retained)), update_agent=False)

    def steer(self, content: str) -> None:
        self._agent.steer(content)

    def follow_up(self, content: str) -> None:
        self._agent.follow_up(content)

    def abort(self) -> None:
        self.children.supervisor.abort()
        self._agent.abort()

    # -- Branching ---------------------------------------------------------------

    def create_branch(self, *, at_index: int | None = None) -> str | None:
        self._children_idle()
        import uuid
        messages = list(self._agent.state.messages)
        branch_point = at_index if at_index is not None else len(messages) - 1
        if branch_point < 0:
            return None
        branch_id = uuid.uuid4().hex[:8]
        snapshot = messages_to_transcript(messages[:branch_point + 1])
        if snapshot and self._has_compaction_entry:
            snapshot[0].update(is_compaction=True, compaction=self._compaction_metadata)
        selected = (self._saved_active_tools if self._tool_selection_restore_pending
                    else self._agent.active_tool_selection)
        self._store.save_branch(self._meta.session_id, branch_id, snapshot,
                               metadata={'active_tools': list(selected) if selected is not None else None})
        return branch_id

    def restore_branch(self, branch_id: str) -> bool:
        self._children_idle()
        if self._agent.state.is_streaming:
            raise RuntimeError("Branch restoration requires an idle agent")
        transcript = self._store.load_branch(self._meta.session_id, branch_id)
        if transcript is None:
            return False
        state = AccessState.from_dict(self._store.branch_access(
            self._meta.session_id, branch_id)).retain_restriction(self.access.state)
        entries = self._store.get_extension_entries(self._meta.session_id, branch_id)
        validate_entries(entries)
        from xdog.agent.extensions.compaction import saved_record
        restored_compaction = next((saved_record(entry.get('compaction')) for entry in reversed(transcript)
                                   if entry.get('is_compaction') is True), None)
        messages = transcript_to_messages(transcript)
        if self._runtime._active_session is not self:
            raise ValueError('Branch restoration requires the current session owner')
        selected = self._store.get_active_tools(self._meta.session_id, branch_id)
        self._agent.validate_active_tools(self._agent.active_tool_selection,
                                         expected_revision=self._agent.tool_selection_revision)
        self._store.commit_navigation(self._group_id, self._meta.session_id, branch_id, transcript,
                                      state.to_dict(), entries, branches=[], active_tools=selected)
        self._compaction_metadata = restored_compaction
        self._has_compaction_entry = bool(transcript and transcript[0].get('is_compaction') is True)
        self._runtime._source_revision += 1
        self._agent.replace_messages(messages)
        self._source_channels = {index: str(entry.get("channel", "")) for index, entry in enumerate(transcript)}
        self._source_tools.clear()
        self._source_call_ids.clear()
        self._persisted_count = len(messages)
        self.children.supervisor.close()
        self.extension_dialogs.close()
        self.extensions.close()
        self._runtime._retired_extensions.append(self.extensions)
        old_aliases = self.extensions.aliases
        self._agent.set_tools(tuple(tool for tool in self._agent.state.tools if tool.name not in old_aliases))
        self._load_extensions()
        self.access.close()
        self.access = AccessScope(state, lambda value: self._store.save_access(self._meta.session_id, value.to_dict()))
        self.children = ClawChildren(self)
        base = tuple(tool for tool in self._agent.state.tools if tool.name not in {"agent_spawn", "agent_control"})
        self._agent.set_tools((*base, *self.children.supervisor.tools()))
        self._saved_active_tools = selected
        self._tool_selection_restore_pending = selected is not None
        self._agent.set_active_tools([] if selected is not None else None,
                                     expected_revision=self._agent.tool_selection_revision)
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
        child_ui: tuple[str, Callable[[dict[str, Any]], None]] | None = None,
    ) -> TurnResult:
        """Execute a turn, optionally delivering each completed assistant message.

        Message delivery contains only public text, not reasoning or tool
        results. ``response_text`` still aggregates the turn for non-streaming
        callers; callers using message delivery must not send it again.
        """
        if self._tool_selection_changing or self._tool_selection_retiring:
            raise RuntimeError('Active-tool persistence owns this session; wait before sending input')
        if self._compacting:
            raise RuntimeError("Compaction owns this session; cancel/wait before sending input")
        if on_display_event is None and on_text_delta is not None:
            def _compat_display_event(event: DisplayEvent) -> None:
                if isinstance(event, AssistantTextDelta):
                    on_text_delta(event.delta)
            on_display_event = _compat_display_event
        self._restore_messages()
        context_snapshot = (await self.context_messages.capture()
            if isinstance(input, UserInput) and input.metadata.get('input_origin', 'user') == 'user' else None)
        self.children.extensions.default_ui = child_ui
        display_loop = asyncio.get_running_loop()
        self._host_loop = display_loop
        def publish_extension(event: dict[str, Any]) -> None:
            if on_display_event is not None:
                display_loop.call_soon_threadsafe(on_display_event, ExtensionNotice(event))
        self.extensions.publish = publish_extension
        self.extension_dialogs.publish = publish_extension
        self.extensions.dialog_handler = self.extension_dialogs.ask if on_display_event is not None else None
        self.extensions.dialog_cancel = self.extension_dialogs.cancel_extension
        try:
            await self.initialize_extensions()
            await self.extensions.event("turn_start", {"user_input": input.content})
            await self.initialize_extensions()  # Lifecycle hooks may update provider metadata.
            if isinstance(input, UserInput) and input.images:
                await asyncio.to_thread(self._runtime.validate_images, input.images)
            system_prompt = self._rebuild_system_prompt(input)
            await self._maybe_compact()

            previous_count = len(self._agent.state.messages)
            self._source_generation += 1
            channel = (input.channel if isinstance(input, UserInput)
                       else input.kind if isinstance(input, SystemInput) else "")
            self._source_channels[previous_count] = channel
            self._input_is_user = isinstance(input, UserInput)
            self._input_channel = str(channel)
            from xdog.claw.core.types import ContextContinuation
            if isinstance(input, ContextContinuation):
                from xdog.agent.core import CustomAgentMessage
                from xdog.agent.extensions.context_message import TYPE
                from xdog.agent.extensions.context_message import images as context_images
                history = self._agent.messages
                if (not history or not isinstance(history[-1], CustomAgentMessage) or history[-1].type != TYPE
                        or history[-1].data.get('id') != input.record_id):
                    raise ValueError('Canonical continuation tail changed; no new user input fabricated')
                from xdog.agent.async_work import run_owned
                await run_owned(lambda: self._runtime.validate_images(context_images((history[-1],))))
                event_stream = await self._agent.continue_()
            elif isinstance(input, ExternalInput):
                from xdog.agent.session_input import encode_input
                event_stream = await self._agent.prompt(encode_input(input.canonical))
            else:
                event_stream = await self._agent.prompt(
                    input.content, images=list(input.images) if isinstance(input, UserInput) and input.images else None,
                    initial_prompt_context=(lambda: self.context_messages.admit(context_snapshot))
                        if context_snapshot is not None else None,
                )
            turn = await self._drain_events(event_stream, on_display_event, on_assistant_message)
            handled = self._agent.drain_handled_inputs()

            if self._agent.cancellation_requested:
                self._persist_turn(input, turn.usage)
                await self.extensions.event("turn_end")
                return TurnResult(error="aborted")
            if self._agent.state.error:
                error = self._agent.state.error
                self._persist_turn(input, turn.usage)
                await self.extensions.event("turn_end")
                final = self._agent.messages[-1] if self._agent.messages else None
                return TurnResult(error=error, tool_calls=turn.tool_calls, usage=turn.usage, retain_partial=True,
                                  canceled=isinstance(final, AssistantMessage) and final.stop_reason == "aborted")
            response_start = previous_count if isinstance(input, ContextContinuation) else previous_count + 1
            if len(self._agent.state.messages) <= response_start:
                if handled:
                    await self.extensions.event("turn_end")
                    return TurnResult(response_text="\n".join(event.feedback for event in handled), input_handled=True)
                return TurnResult(error="Agent produced no response")

            final_text = extract_final_text(
                self._agent.state.messages,
                previous_count=response_start,
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
            await self.extensions.event("turn_end")

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
        finally:
            await self._sync_extension_tools()
            await self._expire_selected_skills()


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

        if self.access.state.read_only:
            blocks = (*blocks, SystemPromptBlock(text=self.access.state.prompt(), cache=False))
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
            if isinstance(event, MessageEndEvent):
                await self.extensions.event("message_end", {"role": getattr(event.message, "role", "custom")})
            if isinstance(event, ToolExecutionEndEvent):
                await self.extensions.event("tool_end", {"tool_name": event.tool_name, "is_error": event.is_error})
            if isinstance(event, MessageUpdateEvent):
                inner = event.assistant_message_event
                if on_display_event and isinstance(inner, TextDeltaEvent):
                    on_display_event(AssistantTextDelta(inner.delta))

            elif isinstance(event, ToolExecutionStartEvent):
                self._source_tool_started(event.tool_call_id, event.tool_name, event.args)
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
                if event.partial_result:
                    update_text = "\n".join(
                        part.text
                        for part in event.partial_result.content
                        if isinstance(part, TextContent)
                    )
                    self._source_tool_output(event.tool_call_id, update_text)
                    if on_display_event:
                        on_display_event(ToolUpdated(
                            tool_call_id=event.tool_call_id, name=event.tool_name, result=update_text,
                        ))

            elif isinstance(event, ToolExecutionEndEvent):
                text_parts = ([part.text for part in event.result.content if isinstance(part, TextContent)]
                              if event.result else [])
                text = "\n".join(text_parts)
                images: list[dict[str, str]] = []
                image_bytes = omitted = 0
                for part in event.result.content if event.result else ():
                    if isinstance(part, ImageContent):
                        size = len(part.data.encode())
                        if len(images) >= 4 or image_bytes + size > 3 * 1024 * 1024:
                            omitted += 1
                        else:
                            images.append({"data": part.data, "mime_type": part.mime_type})
                            image_bytes += size
                if omitted:
                    text += (f"\n[Images: {omitted} omitted from bounded live preview; "
                             "original image data remains in the transcript]")
                self._source_tool_output(event.tool_call_id, text, final=True)
                if on_display_event:
                    on_display_event(ToolFinished(
                        tool_call_id=event.tool_call_id, name=event.tool_name, result=text,
                        is_error=event.is_error, images=tuple(images),
                    ))

            elif isinstance(event, (InputTransformedEvent, InputRejectedEvent, InputHandledEvent)):
                if on_display_event:
                    original = event.original
                    parts = (original.content if not isinstance(original.content, str)
                             else (TextContent(text=original.content),))
                    value: dict[str, Any] = {"type": event.type, "source": event.source, "id": event.input_id,
                        "original": "\n".join(part.text for part in parts if isinstance(part, TextContent)),
                        "image_count": sum(isinstance(part, ImageContent) for part in parts)}
                    if isinstance(event, InputHandledEvent):
                        value["feedback"] = event.feedback
                    if isinstance(event, InputTransformedEvent):
                        changed = event.message.content
                        value["content"] = (changed if isinstance(changed, str) else
                                            "\n".join(part.text for part in changed if isinstance(part, TextContent)))
                    on_display_event(ExtensionNotice(value))

            elif isinstance(event, InputConsumedEvent):
                if on_display_event:
                    on_display_event(InputConsumed(
                        event.input.id, event.input.content, event.input.delivery, len(event.input.images),
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
        if self.context_messages.uncertain:
            return  # Do not overwrite an uncertain canonical context commit before reload.
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
                if entry.get("role") == "user" or isinstance(input, ExternalInput):
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

    def _compaction_tools(self) -> list[AgentTool]:
        from xdog.agent import AgentContext, AgentToolResult
        tools = []
        active = {tool.name for tool in self._agent.active_tools}
        for tool in self._runtime.tools:
            if tool.name not in active:
                continue  # Silent compaction is not authority to regain excluded parent tools.
            def guarded(original: AgentTool) -> AgentTool:
                async def execute(call_id: str, params: dict[str, Any], *args: Any, **kwargs: Any) -> AgentToolResult:
                    # Managed durable-memory writes are compaction metadata, not
                    # permission to run shell/workspace mutations in planning.
                    metadata = (self.access.state.read_only and original.name == "memory"
                                and params.get("action") == "write" and params.get("target") in {"daily", "memory"})
                    ctx = BeforeToolCallContext(assistant_message=AssistantMessage(), args=params,
                        tool_call=ToolCall(id=call_id, name=original.name, arguments=params), context=AgentContext())
                    block = None if metadata else await self._before_tool_call(ctx)
                    if block is not None and block.block:
                        return AgentToolResult(content=(TextContent(text=block.reason),))
                    if original.execute is None:
                        raise ValueError("Compaction tool has no executor")
                    return await original.execute(call_id, params, *args, **kwargs)
                return replace(original, execute=execute)
            tools.append(guarded(tool))
        return tools

    async def compact(self, instructions: str = "", alive: Callable[[], bool] = lambda: True) -> dict[str, Any]:
        """Explicit idle-only compaction, never a normal user/model/tool turn."""
        self._children_idle()
        if self._agent.is_streaming:
            raise ValueError("Compaction requires an idle owning session")
        self._restore_messages()
        return await self._run_compaction(force=True, instructions=instructions, alive=alive)

    async def _maybe_compact(self) -> None:
        await self._run_compaction()

    async def _run_compaction(self, *, force: bool = False, instructions: str = "",
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
        if self._compacting:
            raise ValueError("Compaction already owns this session")
        self._compacting = True
        try:
            from xdog.claw.core.persistence.transcript_convert import live_transcript_snapshot
            original = self._agent.state.messages
            stored = await asyncio.to_thread(self._store.load_transcript, self._meta.session_id)
            transcript = await asyncio.to_thread(live_transcript_snapshot, original, stored)
            before = len(transcript)
            result: dict[str, Any] = {"session_id": self._meta.session_id, "before": before,
                                      "after": before, "changed": False}
            token_est = estimate_tokens(transcript)
            context_window = min(self._runtime.context_window,
                                 self._agent.max_prompt_tokens or self._runtime.context_window)
            target = max(1, int(context_window * 0.5))
            if force:
                target = min(target, max(1, token_est // 2))
            elif not should_compact(self._meta.turn_count, token_est, context_window):
                return result
            cut = retained_turn_start(list(original), find_cut_point(transcript, target))
            if before < 4 or cut == 0:
                return result
            if not alive():
                raise ValueError("Compaction owner changed; original history retained")
            await self.initialize_extensions(require_model=True)
            previous = next((saved_record(entry.get('compaction')) for entry in reversed(transcript)
                             if entry.get('is_compaction') is True), None)
            outcome = await self.extensions.before_compaction({
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
            ws = self._runtime.workspace_dir
            from xdog.ai.types import system_prompt_text
            sp = system_prompt_text(self._agent.state.system_prompt) or ""
            if not sp:
                sp = system_prompt_text(self._runtime.build_system_prompt()) or ""
            # Model selection/extension adapters are session-owned. Never use a
            # stale initial model or quietly send an extension model to ai.load().
            summarizer = self._runtime.summarizer
            if isinstance(summarizer, Summarizer):
                summarizer = Summarizer(self._agent.state.model, self._agent.stream_fn, self._agent.options)
            flush = self._runtime.flush_runner
            if isinstance(flush, FlushRunner):
                flush = FlushRunner(self._agent.state.model, self._agent.stream_fn)
            revision = self._runtime._source_revision
            compacted = await run_compaction(
                transcript=transcript, messages=list(original), system_prompt=sp,
                tools=self._compaction_tools(), context_window=context_window, group_id=self._group_id,
                flush_runner=flush, summarizer=summarizer,
                conversations_dir=ws / "conversations" if ws else None,
                reindex_fn=self._runtime.reindex_fn, instructions=instructions, target_tokens=target,
                summary_override=outcome.summary, first_kept_index=cut,
            )
            if not alive() or self._agent.state.messages != original or self._runtime._source_revision != revision:
                raise ValueError("Compaction owner changed; original history retained")
            if compacted == transcript:
                return result
            import uuid
            recovery_id = 'compaction-' + uuid.uuid4().hex
            number = 1 + sum(branch.get('recovery_kind') == 'compaction'
                             for branch in self._store.branch_metadata(self._meta.session_id))
            recovery = {'label': f"Before compaction #{number} ({'manual' if force else 'automatic'})",
                        'recovery_kind': 'compaction', 'branch_point': len(original) - 1,
                        'model': self._agent.state.model, 'access': self.access.state.to_dict(),
                        'extension_entries': self.extensions.entries.snapshot(),
                        'active_tools': (list(self._agent.active_tool_selection)
                                         if self._agent.active_tool_selection is not None else None)}
            metadata = {**metadata, 'recovery_branch_id': recovery_id}
            compacted[0]['compaction'] = metadata
            compacted_messages = transcript_to_messages(compacted)
            async def commit() -> None:
                # A failed checkpoint write cannot discard original context.
                # If replacement later fails, the durable extra branch is safe
                # and inspectable; it is not a false compaction-success receipt.
                await asyncio.to_thread(self._store.save_compaction_checkpoint, self._meta.session_id,
                                        recovery_id, transcript, metadata=recovery)
                await asyncio.to_thread(self._store.replace_transcript, self._meta.session_id, compacted)
                self._runtime._source_revision += 1
                self._agent.replace_messages(compacted_messages)
                self._source_channels = {index: str(entry.get("channel", ""))
                                         for index, entry in enumerate(compacted)}
                self._source_tools.clear()
                self._source_call_ids.clear()
                self._persisted_count = len(compacted_messages)
                self._compaction_metadata = metadata
                self._has_compaction_entry = True
                with silent_extension_ui():
                    await self.extensions.event('session_compact', {'compaction': {
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
            self._compacting = False


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
