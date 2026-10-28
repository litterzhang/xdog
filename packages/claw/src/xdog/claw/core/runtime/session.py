"""Agent session — owns a long-lived Agent and manages one conversation.

Holds the Agent instance, runs turns, manages compaction, and
persists transcripts. Session lifecycle (creation, resets, caching)
is managed by ``GroupRuntime``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
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
    StreamFn,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
)
from xdog.agent.access import AccessScope
from xdog.agent.async_work import run_owned, settle_cleanup
from xdog.agent.context_messages import ContextMessages
from xdog.agent.extensions.dialogs import DialogBroker
from xdog.agent.extensions.runtime import ExtensionRuntime
from xdog.agent.extensions.state import ExtensionEntries
from xdog.agent.tools import ToolRepo
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
from xdog.claw.core.persistence.session_preparation import PreparedSession
from xdog.claw.core.persistence.transcript_convert import (
    estimate_turn_usage as _estimate_turn_usage,
)
from xdog.claw.core.persistence.transcript_convert import (
    extract_final_text,
)
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
from xdog.claw.core.types import (
    ExternalInput,
    GroupInput,
    SessionMeta,
    SystemInput,
    UserInput,
)

if TYPE_CHECKING:
    from xdog.claw.core.persistence.transcript_commit import HistoryCommit
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

    children: ClawChildren

    def __init__(self, *, runtime: GroupRuntime, session_meta: SessionMeta,
                 prepared: PreparedSession | None = None, prepared_stream: StreamFn | None = None) -> None:
        self._runtime = runtime
        self._meta = session_meta
        self._group_id = runtime.group.id
        from xdog.claw.core.persistence.session_preparation import default_stream, read
        effective_stream_fn = prepared_stream if prepared_stream is not None else runtime.stream_fn
        if prepared is None and effective_stream_fn is None:
            effective_stream_fn = default_stream()  # Explicit synchronous composition only.
        if effective_stream_fn is None:
            raise ValueError("Model adapter must be prepared before Agent construction")
        data = prepared or read(runtime.transcript_store, session_meta, runtime.workspace_dir,
                                runtime.model, include_history=False, skills=runtime.skill_repo,
                                extension_root=runtime.extension_root, stream_fn=effective_stream_fn)
        if (data.meta != session_meta or data.workspace != runtime.workspace_dir
                or data.store_root != runtime.transcript_store._sessions_dir
                or session_meta.group_id != self._group_id):
            raise ValueError('Prepared session belongs to another context/workspace/store')
        self._prepared_history = data.history
        self._prepared_entries = data.entries_json
        self._prepared_extension_root = data.extension_root
        self._model_metadata = data.model
        self._human_inputs: dict[str, str] = {}
        self._authorized_messages: dict[int, UserMessage] = {}
        self._user_operation_authorized = False
        self._human_prompt = False
        self._compaction_metadata: dict[str, Any] | None = None
        self._has_compaction_entry = False
        self._compacting = False
        self._tool_selection_changing = False
        self._tool_selection_retiring = False
        self._saved_active_tools = data.active_tools
        self._extensions_initialized = False
        self._tool_selection_restore_pending = self._saved_active_tools is not None
        self._active_skills = frozenset(data.active_skills)
        try:
            self._host_loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        except RuntimeError:
            self._host_loop = None

        self.access = AccessScope(data.access,
                              lambda state: runtime.transcript_store.save_access(
                                  session_meta.session_id, state.to_dict()))
        from xdog.agent.children import RESERVED_TOOLS
        if any(tool.name in RESERVED_TOOLS for tool in runtime.tools):
            raise ValueError("agent delegation tools are reserved for session-owned metadata")
        self._load_extensions()
        # Create the long-lived Agent
        if data.model.model_id != runtime.model:
            raise ValueError("Prepared model belongs to another context configuration")
        native_metadata = data.model.metadata
        if (data.saved_model and not runtime.model.startswith("ext-")
                and native_metadata is None):
            raise ValueError("Saved native model unavailable on configured adapter; no fallback")
        if (native_metadata is not None and data.saved_model
                and (native_metadata.model_type != "chat" or not native_metadata.supports_streaming
                     or native_metadata.context_window <= 0)):
            raise ValueError("Saved native model is no longer a streaming chat model; no fallback")
        if native_metadata is not None:
            runtime.context_window = native_metadata.context_window or runtime.context_window
            runtime.max_prompt_tokens = native_metadata.max_prompt_tokens
        from xdog.agent.extensions.providers import ExtensionProviderStream
        saved_thinking = data.thinking
        options = runtime.agent_config.options
        if saved_thinking is not None:
            from typing import cast

            from xdog.ai.types import ThinkingLevel
            options = replace(options,
                              thinking=None if saved_thinking == "off" else cast(ThinkingLevel, saved_thinking))
            if options.thinking is not None and not (runtime.model or "").startswith("ext-"):
                metadata = native_metadata
                if (metadata is None or not metadata.reasoning or metadata.supported_efforts is not None
                        and options.thinking not in metadata.supported_efforts):
                    raise ValueError("Saved reasoning is unavailable on the selected model; no fallback")
        self._agent: Agent = Agent(
            ExtensionProviderStream(self.extensions, effective_stream_fn),
            config=AgentConfig(
                model=runtime.model or "test/dummy",
                supports_tool_calls=data.model.supports_tool_calls,
                system_prompt="",  # rebuilt each turn
                context_window=runtime.context_window,
                max_prompt_tokens=runtime.max_prompt_tokens,
                options=options,
            ),
            tools=list(runtime.tools), mcp_registry=runtime.mcp,
            # The index goes in the system prompt and a loaded body goes after
            # it; the Agent decides both, so the group's prompt builder no
            # longer carries a skills summary of its own.
            skills=getattr(runtime, "skill_repo", None),
            active_skills=tuple(sorted(self._active_skills)),
            prepared_skills=data.skills,
            tool_ctx={
                "group_id": self._group_id,
                "workspace_dir": str(runtime.workspace_dir) if runtime.workspace_dir else "",
                # The key the filesystem tool actually reads. claw was setting
                # `workspace_dir`, which only its own memory tool consults, so a
                # relative path from the agent resolved against the process cwd.
                "fs_workspace": str(runtime.workspace_dir) if runtime.workspace_dir else "",
                "data_dir": str(runtime.data_dir),
                "_memory_search": runtime.memory.search if runtime.memory else None,
                "_memory_manager": runtime.memory,
                "_history_reader": self._history_reader(),
                "_skill_repo": runtime.skill_repo,
            },
            transform_context=self._extension_context,
            before_tool_call=self._before_tool_call,
            after_tool_call=self._after_tool_call,
            input_transform=self._transform_user_input,
        )

        self._human_input_unsubscribe = self._agent.subscribe(self._observe_human_input)
        self.children = ClawChildren(self, data.children)
        self._child_tools = self.children.runtime.tools()
        self._tool_repo: ToolRepo
        runtime.publish_tools(self)
        if self._tool_selection_restore_pending:
            self._agent.set_active_tools([], expected_revision=self._agent.tool_selection_revision)
        self._input_is_user = True
        self._input_channel = ""
        self._persisted_count: int = 0
        self._history_fault = False
        self._history_commit_task: asyncio.Task[SessionMeta] | None = None
        self._restored: bool = False
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
        from xdog.claw.core.persistence.session_preparation import history
        data = self._prepared_history or history(self._store, self._meta.session_id)
        metadata = json.loads(data.compaction_json) if data.compaction_json is not None else None
        self._agent.replace_messages(list(data.messages))
        self._source_channels = dict(data.channels)
        self._has_compaction_entry = data.has_compaction
        self._compaction_metadata = metadata
        self._persisted_count = len(data.messages)
        self._prepared_history = None
        self._restored = True  # A failed read/conversion must remain unavailable on the next attempt.

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
        # Clear only the inventory this session actually published. A retired
        # session cannot revoke another session's replacement repository.
        for name in self._tool_repo.names():
            self._tool_repo.unregister(name)
        self._human_input_unsubscribe()
        self._human_inputs.clear()
        self.children.runtime.close()
        self.extension_dialogs.close()
        self.extensions.close()
        self.access.close()

    def require_history_ready(self) -> None:
        if self._history_fault or self.context_messages.uncertain:
            raise RuntimeError('History persistence unavailable; reopen the original context and inspect prior effects')

    def _history_batch(self, input: GroupInput | None = None,
                       usage: dict[str, int] | None = None) -> HistoryCommit | None:
        from xdog.agent.messages import messages_to_dicts
        from xdog.claw.core.persistence.transcript_commit import HistoryCommit
        if self._history_fault or self.context_messages.uncertain:
            return None
        messages = self._agent.state.messages
        if len(messages) <= self._persisted_count:
            return None
        channel = (input.channel if isinstance(input, UserInput) else
                   input.kind if isinstance(input, SystemInput) else '')
        try:
            encoded = json.dumps(messages_to_dicts(list(messages)), allow_nan=False)
        except Exception:
            self._history_fault = True
            self.context_messages.uncertain = True
            raise
        return HistoryCommit(self._meta, self._store._sessions_dir, self._store.active_branch(self._meta.session_id),
            encoded, self._persisted_count, tuple(self._source_channels.items()), channel,
            isinstance(input, ExternalInput),
            tuple((usage or {}).items()), input is not None)

    def _persist(self) -> None:
        """Explicit synchronous fixture/composition save using the same transaction."""
        from xdog.claw.core.persistence.transcript_commit import commit
        batch = self._history_batch()
        if batch is not None:
            self._meta = commit(self._store, batch)
            self._persisted_count = len(self._agent.messages)

    async def _persist_history(self, input: GroupInput | None = None, usage: dict[str, int] | None = None) -> None:
        from xdog.agent.async_work import wait_owned
        from xdog.claw.core.persistence.transcript_commit import commit
        if self._history_commit_task is not None:
            raise RuntimeError('Original history commit still owns this context')
        batch = self._history_batch(input, usage)
        if batch is None:
            return
        count = len(self._agent.messages)
        task = self._history_commit_task = asyncio.create_task(asyncio.to_thread(commit, self._store, batch))
        try:
            await wait_owned(task)
        finally:
            if task.done():
                self._history_commit_task = None
                if task.cancelled() or task.exception() is not None:
                    self._history_fault = True
                    self.context_messages.uncertain = True
                else:
                    # Actual committed result wins, even if the waiting caller
                    # was cancelled. Never enter fallback append with the old cursor.
                    self._meta = task.result()
                    self._persisted_count = count

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
        entries = ExtensionEntries(json.loads(self._prepared_entries),
                                   lambda value: store.save_extension_entries(session_id, value))
        self.extensions = ExtensionRuntime(
            self._runtime.extension_names, self._runtime.extension_root, self._runtime.workspace_dir,
            settings=self._runtime.extension_settings, context=lambda: {"session_id": self._meta.session_id,
                "group_id": self._group_id, "cwd": str(self._runtime.workspace_dir), "model": self._runtime.model,
                "read_only": self.access.state.read_only, "permission_mode": "configured"}, entries=entries,
            inspection=self._extension_inspection, prepared_root=self._prepared_extension_root)
        from xdog.claw.core.command_names import SLASH_COMMANDS
        self.extensions.reserve_commands(frozenset(SLASH_COMMANDS))
        self.extensions.tool_inventory = self._extension_tools
        self.extensions.session_name = self._extension_session_name
        if hasattr(self, "_agent"):
            self._agent.set_transform_context(self._extension_context)
            from xdog.agent.extensions.providers import ExtensionProviderStream
            previous = self._agent.stream_fn
            base = previous.base if isinstance(previous, ExtensionProviderStream) else previous
            self._agent.set_stream_fn(ExtensionProviderStream(self.extensions, base))

    def _history_reader(self) -> Any:
        from xdog.claw.core.persistence.history_reader import HistoryReader
        return HistoryReader(self._runtime.transcript_store, self._group_id, self._meta.session_id)

    async def _extension_context(self, messages: list[Any]) -> list[Any]:
        try:
            # Use the Agent extension runtime's transient context projection.
            from xdog.claw.core.persistence.handoff import project
            result = await self.extensions.transform(messages)
            return [project(message) for message in result]
        finally:
            await self._persist_tool_retirement()

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
        from xdog.agent.extensions.inspection import capture
        from xdog.ai.types import Model
        model_id = self._agent.state.model
        metadata = self._model_metadata
        selected = (self.extensions.provider_model(model_id) if model_id.startswith("ext-") else
                    metadata.metadata if metadata.model_id == model_id else None)
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
            await self._persist_tool_retirement()

    async def initialize_extensions(self, *, require_model: bool = False) -> None:
        try:
            if self.extensions.enabled or self.extensions.commands():
                from xdog.claw.core.command_names import SLASH_COMMANDS
                skill_names = await run_owned(
                    lambda: {skill.slug for skill in self._runtime.skill_repo.list_skills()})
                self.extensions.reserve_commands(frozenset(SLASH_COMMANDS), skill_commands=frozenset(skill_names))
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
            await self._persist_tool_retirement()

        self._extensions_initialized = True
        self._restore_active_tools()
        await self._persist_tool_retirement()

    def _restore_active_tools(self) -> None:
        # Gateway extension-model bootstrap precedes application publication.
        # Keep the startup mask and saved choice until every scope is ready.
        if (not self._extensions_initialized or not self._tool_selection_restore_pending
                or any(owner.providers and not owner.started
                       for owner in self._runtime.capability_assembly.sets)):
            return
        configured = {tool.name for tool in self._agent.state.tools}
        selected = tuple(name for name in self._saved_active_tools or () if name in configured)
        self._agent.set_active_tools(selected, expected_revision=self._agent.tool_selection_revision)
        self._tool_selection_restore_pending = False

    async def _persist_tool_retirement(self) -> None:
        """Await durable narrowing before a callback/turn can acknowledge inventory retirement."""
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
            with self.children.runtime.idle_scope():
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
                return message  # System work is not intercepted as human input.
            value = await self.extensions.input(message, source, channel=self._input_channel)
            if source == "prompt" and self._human_prompt and isinstance(value, UserMessage):
                self._authorized_messages[id(value)] = value
            return value
        finally:
            await self._persist_tool_retirement()

    def _read_only_tool_call(self, ctx: BeforeToolCallContext) -> bool:
        from xdog.claw.core.runtime.session_tools import read_only_tool_call
        return read_only_tool_call(self, ctx)

    async def _before_tool_call(
        self, ctx: BeforeToolCallContext, cancel: asyncio.Event | None = None,
        *, memory_maintenance: bool = False,
    ) -> BeforeToolCallResult | None:
        from xdog.claw.core.runtime.session_tools import before_tool_call
        return await before_tool_call(self, ctx, cancel, memory_maintenance=memory_maintenance)

    async def _after_tool_call(
        self, ctx: AfterToolCallContext, cancel: asyncio.Event | None = None,
    ) -> AfterToolCallResult | None:
        from xdog.claw.core.runtime.session_tools import after_tool_call
        return await after_tool_call(self, ctx, cancel)

    def _children_idle(self) -> None:
        if self._tool_selection_changing or self._tool_selection_retiring:
            raise RuntimeError('Active-tool persistence owns this session; wait/cancel before changing context')
        if self._compacting:
            raise RuntimeError("Compaction owns this session; cancel/wait before changing context")
        if self.children.runtime.active:
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
            return self.children.runtime.snapshot()
        if action == "presets":
            return {"presets": self.children.runtime.presets.snapshot()}
        if action == "start":
            await self.initialize_extensions()
            self._agent.set_system_prompt(await self._prompt_blocks())
            child_id = await self.children.runtime.start(text, access=access, config=config, images=images)
            return {"child_id": child_id, 'accepted_input': self.children.runtime.admission_receipt(child_id),
                    **self.children.runtime.snapshot()}
        return await self.children.runtime.control(action, identifier, text, images=images,
                                                      return_owner=return_owner, return_alive=return_alive)

    # -- Public API -----------------------------------------------------------

    async def skills_control(self, action: str, slug: str = '',
                             alive: Callable[[], bool] = lambda: True) -> dict[str, Any]:
        from xdog.agent.skills.repo import is_valid_skill_name
        if (not isinstance(action, str) or not isinstance(slug, str)
                or action not in {'list', 'load', 'unload'}
                or action != 'list' and slug != 'all' and not is_valid_skill_name(slug)):
            raise ValueError('Use /skills [load SLUG|unload SLUG|unload all]')
        manager = self._runtime.skill_repo
        if action != 'list':
            self._children_idle()
            if self._agent.is_streaming:
                raise ValueError('Skill selection requires an idle owning session')
            active = set(self._active_skills)
            if action == 'load':
                skill = await run_owned(manager.load_skill, slug)
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
        skills = await run_owned(manager.list_skills)
        effective = self._runtime.capability_assembly.active_skills(self._active_skills)
        return {'session_id': self._meta.session_id, 'active': sorted(effective), 'skills': [
            {'slug': skill.slug, 'name': skill.name, 'description': skill.description,
             'scope': skill.scope, 'packaged': skill.packaged, 'active': skill.slug in effective}
            for skill in skills], 'notice': 'Selection changes instructions only; no model/tool/permission grant'}

    async def _save_selected_skills(self, chosen: tuple[str, ...], *, update_agent: bool = True) -> None:
        # Once a write is admitted, settle BOTH disk and owning-loop state before
        # releasing the group reservation, even if its receipt is disconnected.
        async def commit() -> None:
            from xdog.agent.skills.types import SkillPlacement
            effective = tuple(sorted(self._runtime.capability_assembly.active_skills(chosen)))
            placement = await run_owned(SkillPlacement.read, self._runtime.skill_repo, effective)
            await run_owned(self._store.save_active_skills, self._meta.session_id, chosen)
            self._active_skills = frozenset(chosen)
            if update_agent:
                self._agent.set_active_skills(effective, prepared_skills=placement)
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

    def _observe_human_input(self, event: Any) -> None:
        if (isinstance(event, MessageEndEvent) and isinstance(event.message, UserMessage)
                and self._authorized_messages.get(id(event.message)) is event.message):
            self._user_operation_authorized = True
        if isinstance(event, InputConsumedEvent) and event.input.id in self._human_inputs:
            channel = self._human_inputs.pop(event.input.id)
            self._user_operation_authorized = True  # Authorize only after this real user input is actually consumed.
            self._source_channels[len(self._agent.messages) - 1] = channel
        elif isinstance(event, (InputHandledEvent, InputRejectedEvent)):
            self._human_inputs.pop(event.input_id, None)

    def queue_human_input(self, message: UserInput, delivery: str) -> None:
        from typing import cast

        from xdog.agent.input_queue import Delivery
        if (isinstance(message, ExternalInput) or message.metadata.get('input_origin', 'user') != 'user'
                or delivery not in {'steer', 'follow_up'}):
            raise ValueError('Only host-identified human input can authorize user controls')
        if message.images:
            self._runtime.validate_images(message.images)
        pending = {item.id for item in self._agent.pending_inputs()}
        self._human_inputs = {key: value for key, value in self._human_inputs.items() if key in pending}
        identifier = 'claw-human-' + uuid.uuid4().hex
        self._agent.queue_input(identifier, message.content, cast(Delivery, delivery), message.images)
        self._human_inputs[identifier] = message.channel or 'user'

    def steer(self, content: str) -> None:
        self._agent.steer(content)

    def follow_up(self, content: str) -> None:
        self._agent.follow_up(content)

    def abort(self) -> None:
        self.children.runtime.abort()
        self._agent.abort()

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
        self.require_history_ready()
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
        self._user_operation_authorized = False
        self._authorized_messages.clear()
        self._human_prompt = (isinstance(input, UserInput) and not isinstance(input, ExternalInput)
                              and input.metadata.get('input_origin', 'user') == 'user')
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
                await run_owned(self._runtime.validate_images, input.images)
            system_prompt = await self._rebuild_system_prompt(input)
            await self._maybe_compact()

            previous_count = len(self._agent.state.messages)
            self._source_generation += 1
            channel = (input.channel if isinstance(input, UserInput)
                       else input.kind if isinstance(input, SystemInput) else "")
            self._source_channels[previous_count] = channel
            self._input_is_user = isinstance(input, UserInput) and not isinstance(input, ExternalInput)
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
                await self._persist_turn(input, turn.usage)
                await self.extensions.event("turn_end")
                return TurnResult(error="aborted")
            if self._agent.state.error:
                error = self._agent.state.error
                await self._persist_turn(input, turn.usage)
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
                previous_count=response_start, empty="",
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

            await self._persist_turn(input, usage)
            await self.extensions.event("turn_end")

            return TurnResult(
                response_text=final_text,
                tool_calls=turn.tool_calls,
                usage=usage,
            )

        except asyncio.CancelledError:
            # The consumer and producer are separate tasks. Keep the original
            # execution owner until model/tool cancellation and persistence settle,
            # including a second cancellation while cleanup is awaiting IO.
            async def stop_cancelled() -> None:
                self._agent.abort()
                await self._agent.wait_for_idle()
            await settle_cleanup(stop_cancelled, self._persist_history,
                                 message='Cancelled Claw turn cleanup failed')
            raise
        except Exception as error:
            logger.error("Agent turn failed for group %s: %s", self._group_id, error)
            try:
                await settle_cleanup(self._agent.cancel_and_wait, self._persist_history,
                                     message='Failed Claw turn cleanup failed')
            except asyncio.CancelledError:
                raise  # The original application cancellation owns its interrupted receipt.
            except Exception as cleanup:
                raise ExceptionGroup('Claw turn and cleanup failed', [error, cleanup]) from None
            return TurnResult(error=str(error))
        finally:
            self._user_operation_authorized = False
            self._authorized_messages.clear()
            self._human_prompt = False
            await self._persist_tool_retirement()
            await self._expire_selected_skills()


    # -- Turn sub-steps -------------------------------------------------------

    async def _prompt_blocks(self, *, bootstrap: bool = False) -> tuple[SystemPromptBlock, ...]:
        from xdog.claw.core.runtime.session_prompt import prompt_blocks
        return await prompt_blocks(self, bootstrap=bootstrap)

    async def _rebuild_system_prompt(self, input: GroupInput) -> str:
        from xdog.claw.core.runtime.session_prompt import rebuild_system_prompt
        return await rebuild_system_prompt(self, input)

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

    async def _persist_turn(self, input: GroupInput, usage: dict[str, int]) -> None:
        await self._persist_history(input, usage)

    def _compaction_tools(self, *, memory_only: bool = False) -> list[AgentTool]:
        from xdog.claw.core.runtime.session_tools import compaction_tools
        return compaction_tools(self, memory_only=memory_only)

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
        from xdog.claw.core.runtime.session_compaction import compact_history
        return await compact_history(self, force=force, instructions=instructions, alive=alive)


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
