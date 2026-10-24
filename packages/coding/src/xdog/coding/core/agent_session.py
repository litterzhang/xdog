"""Agent session: wraps agent.Agent with session persistence, compaction, and branching."""

from __future__ import annotations

import asyncio
import inspect
import logging
import threading
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from functools import wraps
from pathlib import Path
from typing import TYPE_CHECKING, Any, Concatenate, ParamSpec, TypeVar, cast

from xdog.agent import (
    AfterToolCallContext,
    AfterToolCallResult,
    AgentEvent,
    AgentMessage,
    AgentTool,
    BeforeToolCallContext,
    BeforeToolCallResult,
    InputHandledEvent,
    MessageEndEvent,
    ToolExecutionEndEvent,
    ToolRepo,
)
from xdog.agent.access import AccessScope, AccessState
from xdog.agent.agent import Agent
from xdog.agent.capabilities import ApplicationPort, CapabilityAssembly, CapabilitySet
from xdog.agent.child_config import parse_child_start
from xdog.agent.extensions.command_result import ExtensionCommandResult
from xdog.agent.extensions.dialogs import DialogBroker
from xdog.agent.extensions.runtime import ExtensionRuntime
from xdog.agent.extensions.state import ExtensionEntries, validate_entries
from xdog.agent.history import history_points, validate_checkpoint
from xdog.agent.mcp import McpRegistry
from xdog.agent.tool_effects import ToolEffect
from xdog.agent.types import HandledInput, InputTransformFn, TransformContextFn
from xdog.ai.types import (
    AssistantMessage,
    ImageContent,
    Model,
    TextContent,
    ThinkingLevel,
    UserMessage,
)
from xdog.coding.core.child_agents import CodingChildren
from xdog.coding.core.defaults import COMPACTION_THRESHOLD_RATIO, MAX_CONTEXT_TOKENS
from xdog.coding.core.event_bus import EventBus, get_event_bus
from xdog.coding.core.messages import dicts_to_messages, messages_to_dicts
from xdog.coding.core.permissions import PermissionManager, PermissionMode
from xdog.coding.core.session_manager import SessionData, SessionManager
from xdog.coding.core.settings_manager import SettingsManager
from xdog.coding.core.system_prompt import build_system_prompt

if TYPE_CHECKING:
    from concurrent.futures import Future

    from xdog.coding.core.session_staging import StagedExtensions
    from xdog.coding.core.sessions.batches import TurnInput
    from xdog.coding.core.sessions.controller import SessionController
    from xdog.coding.core.sessions.ownership import SessionLease
    from xdog.coding.core.sessions.runtime import SessionRuntime

logger = logging.getLogger(__name__)


P = ParamSpec("P")
R = TypeVar("R")


def _idle_change(method: Callable[Concatenate["AgentSession", P], R]) -> Callable[Concatenate["AgentSession", P], R]:
    @wraps(method)
    def wrapped(self: AgentSession, /, *args: P.args, **kwargs: P.kwargs) -> R:
        if self._sdk_disposing:
            raise RuntimeError('Session is closing or closed')
        runtime = self.runtime
        controller = runtime.controller if runtime is not None else self._sdk_controller
        if (controller is None and runtime is None and self._sdk_closing is not None
                and not self._sdk_closing.done()):
            raise RuntimeError('SDK owner cleanup is still settling; wait before changing session controls')
        if controller is not None and not controller.owns_current_task:
            async def owned() -> R:
                from xdog.agent.async_work import run_owned
                return await run_owned(wrapped, self, *args, **kwargs)
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                pass
            else:
                # A synchronous wait would block this caller's loop, which may
                # own the admitted producer's completion/cancellation signal.
                raise RuntimeError("Session controls must be submitted to the session owner; "
                                   "use an owner operation or asyncio.to_thread from async callers")
            borrowed = runtime is None
            if borrowed:
                with self._sdk_lock:
                    if self._sdk_controller is not controller:
                        raise RuntimeError('SDK session owner changed; retry the control')
                    self._sdk_users += 1
            try:
                return controller.control(owned, name=method.__name__).result()
            finally:
                if borrowed:
                    closing = self._release_sdk_owner(controller)
                    if closing is not None:
                        closing.result()
        before_scope = (self.session_id, self.session_data.active_branch)
        before_resources = (self.children, self.extensions, self.mcp)
        if self._tool_selection_retiring:
            raise RuntimeError("Tool retirement persistence owns this session")
        if self._compacting:
            raise RuntimeError("Compaction owns this session; cancel/wait before changing context")
        if self._auth_changing:
            raise RuntimeError("Authorization operation still owns session changes; wait/cancel before navigating")
        with self.children.runtime.idle_scope():
            if method.__name__ in {'switch_session', 'restore_branch', 'navigate_checkpoint'}:
                self._navigation_cancel.clear()
            navigation = method.__name__ in {'new_session', 'switch_session', 'restore_branch', 'navigate_checkpoint'}
            self._navigating += int(navigation)
            self._context_changes += 1
            target_lease = None
            try:
                if method.__name__ in {'switch_session', 'navigate_checkpoint'}:
                    target = args[0] if args else kwargs.get('session_id')
                    if isinstance(target, str) and target != self.session_id:
                        from xdog.coding.core.sessions.ownership import SessionLease, lease_directory
                        pending = self._pending_lease
                        target_lease = (pending if pending is not None and pending.session_id == target else
                            SessionLease.acquire(lease_directory(self.session_manager._dir), target))
                result = method(self, *args, **kwargs)
                if target_lease is not None and self.session_id == target_lease.session_id:
                    previous, self._lease = self._lease, target_lease
                    if previous is not None:
                        self._retiring_leases.add(previous)
                return result
            finally:
                if target_lease is not None and self._lease is not target_lease:
                    target_lease.close()
                self._context_changes -= 1
                self._navigating -= int(navigation)
                self._refresh_session_hint()
                if (self._context_changes == 0
                        and (before_scope != (self.session_id, self.session_data.active_branch)
                             or any(before is not after for before, after in zip(
                                 before_resources, (self.children, self.extensions, self.mcp), strict=True)))):
                    if self.runtime is not None:
                        self.runtime.context_changed()
                    elif controller is not None:
                        discarded = controller.rotate(self.session_data.active_branch)

                        async def rebind_sdk() -> None:
                            from xdog.coding.core.sessions.store import InputStore
                            await asyncio.wrap_future(discarded)
                            await self._settle_retired_capabilities()
                            await self._settle_retired_resources()
                            previous = controller.inbox
                            if previous is not None:
                                await previous.retire()
                            controller.store = await asyncio.to_thread(InputStore, self)
                            from xdog.coding.core.sessions.intake import attach_inbox
                            inbox = attach_inbox(controller, previous.store if previous is not None else None)
                            await inbox.activate()

                        controller.settle_before_release(rebind_sdk())
                    else:
                        from xdog.coding.core.session_staging import settle
                        async def retire_cold() -> None:
                            from xdog.agent.async_work import settle_cleanup
                            await settle_cleanup(self._settle_retired_capabilities, self._settle_retired_resources)
                        settle(retire_cold)
                        self._release_retired_leases()
    return wrapped


@dataclass
class AgentSession:
    """Manages a coding conversation wrapping agent.Agent.

    The session owns the Agent instance, subscribes to its events for
    persistence, and provides higher-level operations like compaction
    and session branching.
    """

    agent: Agent
    session_data: SessionData
    session_manager: SessionManager
    settings: SettingsManager
    tool_repo: ToolRepo | None
    bash: Any  # kept for backward compat, may be None
    working_dir: Path
    context_window: int = MAX_CONTEXT_TOKENS
    max_prompt_tokens: int = 0
    model_info: Model | None = None
    permissions: PermissionManager = field(default_factory=PermissionManager)
    mcp: McpRegistry | None = None
    event_bus: EventBus = field(default_factory=get_event_bus)
    access: AccessScope = field(init=False)
    children: CodingChildren = field(init=False)
    context_messages: Any = field(init=False, repr=False)
    extensions: ExtensionRuntime = field(init=False)
    extension_dialogs: DialogBroker = field(init=False)
    provider_auth_handler: Callable[[str], Awaitable[dict[str, Any]]] | None = field(
        default=None, init=False, repr=False)
    _session_info_tool: AgentTool | None = field(default=None, init=False, repr=False)
    _base_context_transform: TransformContextFn | None = field(default=None, init=False, repr=False)
    handled_feedback: list[str] = field(default_factory=list, init=False)
    _base_input_transform: InputTransformFn | None = field(default=None, init=False, repr=False)
    runtime: SessionRuntime | None = field(default=None, init=False, repr=False)
    _sdk_controller: SessionController | None = field(default=None, init=False, repr=False)
    _sdk_users: int = field(default=0, init=False, repr=False)
    _sdk_closing: Future[None] | None = field(default=None, init=False, repr=False)
    _retiring_leases: set[SessionLease] = field(default_factory=set, init=False, repr=False)
    _sdk_disposing: bool = field(default=False, init=False, repr=False)
    _close_receipt: Future[None] | None = field(default=None, init=False, repr=False)
    _resource_closing: Future[None] | None = field(default=None, init=False, repr=False)
    _retired_resources: list[tuple[CodingChildren, ExtensionRuntime, McpRegistry | None]] = field(
        default_factory=list, init=False, repr=False)
    capabilities: CapabilitySet = field(default_factory=lambda: CapabilitySet('session'), init=False, repr=False)
    application_capabilities: CapabilitySet = field(
        default_factory=lambda: CapabilitySet('application'), init=False, repr=False)
    _application_host: ApplicationPort = field(default_factory=ApplicationPort, init=False, repr=False)
    _application_started: bool = field(default=False, init=False, repr=False)
    _retired_capability_tools: list[AgentTool] = field(default_factory=list, init=False, repr=False)
    _capability_host: Any = field(default=None, init=False, repr=False)
    _capability_loop: asyncio.AbstractEventLoop | None = field(default=None, init=False, repr=False)
    _capabilities_started: bool = field(default=False, init=False, repr=False)
    _retired_capabilities: list[tuple[asyncio.AbstractEventLoop, CapabilitySet]] = field(
        default_factory=list, init=False, repr=False)
    _capability_start: asyncio.Task[None] | None = field(default=None, init=False, repr=False)
    _capability_start_owner: CapabilitySet | None = field(default=None, init=False, repr=False)
    _user_prompt_messages: dict[int, UserMessage] = field(default_factory=dict, init=False, repr=False)
    _authorized_messages: dict[int, UserMessage] = field(default_factory=dict, init=False, repr=False)
    _user_operation_authorized: bool = field(default=False, init=False, repr=False)
    _sdk_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _lease: SessionLease | None = field(default=None, init=False, repr=False)
    _pending_lease: SessionLease | None = field(default=None, init=False, repr=False)
    _unsubscribe: Any = field(default=None, init=False, repr=False)
    _navigating: int = field(default=0, init=False, repr=False)
    _context_changes: int = field(default=0, init=False, repr=False)
    _auth_changing: bool = field(default=False, init=False, repr=False)
    #: Slugs of skills whose instructions are currently in the system prompt.
    #: Held here rather than pushed into the message history so they can be
    #: taken back out — see `activate_skill`.
    _navigation_alive: Callable[[], bool] = field(default_factory=lambda: (lambda: True), init=False, repr=False)
    _navigation_cancel: threading.Event = field(default_factory=threading.Event, init=False, repr=False)
    _history_error: BaseException | None = field(default=None, init=False, repr=False)
    _compacting: bool = field(default=False, init=False, repr=False)
    _project_files: tuple[Any, ...] = field(default=(), init=False, repr=False)
    _project_directory: Path | None = field(default=None, init=False, repr=False)
    _active_skills: frozenset[str] = field(default=frozenset(), init=False, repr=False)
    _extensions_initialized: bool = field(default=False, init=False, repr=False)
    _tool_selection_restore_pending: bool = field(default=False, init=False, repr=False)
    _tool_selection_retiring: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        self.tool_repo = ToolRepo(*self.agent.local_tools, builtin=False)
        self._refresh_session_hint()
        # Restore session-level settings
        if self.session_data.settings:
            self.settings.load_session_settings(self.session_data.settings)
        self.settings.set_session_max_messages_per_turn(self.settings.max_messages_per_turn)

        from xdog.agent.children import RESERVED_TOOLS
        if any(tool.name in RESERVED_TOOLS for tool in self.agent.state.tools):
            raise ValueError("agent delegation tools are reserved for session-owned metadata")
        # Sessions constructed outside the SDK still receive enforcement.
        self.agent.set_before_tool_call(self._before_tool_call)
        self._load_access()
        self._base_context_transform = self.agent.context_transform
        self._base_input_transform = self.agent.input_transform
        self._load_extensions()
        self._load_children()
        self.agent.set_after_tool_call(self._after_tool_call)
        if self.mcp is None:
            from xdog.coding.config import get_data_dir
            self.mcp = McpRegistry(self.settings.mcp_servers, self.working_dir,
                                  credential_dir=get_data_dir() / "mcp-credentials",
                                  credential_backend=self.settings.mcp_credential_backend)
        self.agent.set_mcp_registry(self.mcp)

        if self.session_data.active_tools is not None:
            # Block model tool availability until configured host inventory
            # is restored. A saved empty list must never become defaults.
            self.agent.set_active_tools([], expected_revision=self.agent.tool_selection_revision)
            self._tool_selection_restore_pending = True

        # Subscribe to agent events for persistence and event bus forwarding
        self._unsubscribe = self.agent.subscribe(self._on_agent_event)
        from xdog.coding.core.context_messages import create
        self.context_messages = create(self)

    def _load_access(self) -> None:
        data = self.session_data
        def persist(state: AccessState) -> None:
            with self.session_manager._write_lock:
                if self.session_data is not data:
                    raise RuntimeError('Original access owner changed')
                staged = replace(data, access=state.to_dict())
                self.session_manager.save_session(staged)
                data.access, data.updated_at = staged.access, staged.updated_at
        self.access = AccessScope(AccessState.from_dict(data.access), persist)

    async def _transform_user_input(self, message: UserMessage, source: str,
                                    cancel: asyncio.Event | None = None) -> UserMessage | HandledInput:
        authorized = self._user_prompt_messages.get(id(message)) is message
        if self._base_input_transform is not None:
            value = await self._base_input_transform(message, source, cancel)
            if isinstance(value, HandledInput):
                return value
            message = value
        value = await self.extensions.input(message, source, channel="coding")
        if authorized and isinstance(value, UserMessage):
            self._authorized_messages[id(value)] = value
        return value

    def _load_extensions(self, prepared: ExtensionRuntime | None = None) -> None:
        self._extensions_initialized = prepared is not None
        data = self.session_data
        branch = data.active_branch
        def persist(value: dict[str, Any]) -> None:
            from xdog.coding.core.session_history import HistorySnapshot
            def guard() -> None:
                if (self._navigating or self.session_data is not data or data.active_branch != branch
                        or entries._closed.is_set()):
                    raise RuntimeError('Original extension entry owner changed or closed')
            with self.session_manager._write_lock:
                guard()
                snapshot = HistorySnapshot.capture(replace(data, extension_entries=value))
                saved = snapshot.commit(self.session_manager, guard)
                guard()
                data.extension_entries = saved.extension_entries
                data.updated_at = saved.updated_at
        entries = ExtensionEntries(data.extension_entries, persist) if prepared is None else prepared.entries
        if prepared is not None:
            entries.bind_persistence(persist)
        self.extension_dialogs = DialogBroker()
        def context() -> dict[str, Any]:
            return {"session_id": self.session_id, "cwd": str(self.working_dir), "model": self.model,
                    "read_only": self.access.state.read_only, "permission_mode": self.permissions.mode}
        self.extensions = prepared or ExtensionRuntime(self.settings.trusted_extensions, self.settings.extension_root,
            self.working_dir, settings=self.settings.extension_settings, context=context, entries=entries,
            inspection=self._extension_inspection)
        from xdog.coding.core.command_names import UI_COMMANDS
        from xdog.coding.core.slash_commands import BUILTIN_COMMANDS
        self.extensions.reserve_commands(frozenset({*BUILTIN_COMMANDS, *UI_COMMANDS, 'draft'}))
        self.extensions.context = context
        self.extensions.inspection = self._extension_inspection
        self.extensions.tool_inventory = self._extension_tools
        self.extensions.session_name = self._extension_session_name
        from xdog.agent.extensions.providers import ExtensionProviderStream
        previous = self.agent.stream_fn
        base = previous.base if isinstance(previous, ExtensionProviderStream) else previous
        self.agent.set_stream_fn(ExtensionProviderStream(self.extensions, base))
        self.agent.set_transform_context(self._extension_context)
        self.agent.set_input_transform(self._transform_user_input)
        self._bind_session_info_tool()

    def session_info(self) -> dict[str, Any]:
        """Read current public facts on the existing owner; no TUI, files or model call."""
        from xdog.coding.core.session_info import snapshot
        return snapshot(self)

    def _bind_session_info_tool(self) -> None:
        from xdog.coding.core.session_info import TOOL_NAME, create_tool
        previous = self._session_info_tool
        if any(tool.name == TOOL_NAME and tool is not previous for tool in self.agent.state.tools):
            raise ValueError('Session inspection tool name is reserved for its actual host binding')
        tool = create_tool(self)
        self._session_info_tool = tool
        self._publish_tools((*[item for item in self.agent.local_tools if item is not previous], tool))

    def _extension_session_name(self) -> dict[str, str] | None:
        from xdog.agent.session_name import name_snapshot
        try:
            return name_snapshot({'session_id': self.session_id, 'owner_id': self.session_id,
                                  'name': self.session_data.name}, session_id=self.session_id, owner_id=self.session_id)
        except (ValueError, TypeError, UnicodeError):
            return None

    def _extension_inspection(self) -> dict[str, Any]:
        from xdog.agent.extensions.inspection import capture
        return capture(self.agent, self.session_id, self.session_id, self.model_info,
                       context_window=self.context_window, max_prompt_tokens=self.max_prompt_tokens)

    def _extension_tools(self) -> dict[str, Any] | None:
        from xdog.agent.extensions.tool_inventory import capture
        if self.extensions._closed:
            return None
        return capture(self.agent, self.session_id, self.session_id)

    def _load_children(self) -> None:
        from xdog.agent.children import RESERVED_TOOLS
        self.children = CodingChildren(self)
        base = tuple(tool for tool in self.agent.local_tools if tool.name not in RESERVED_TOOLS
                     and not any(tool is value for value in self._retired_capability_tools))
        self._publish_tools((*base, *self.children.tools))
        self._retired_capability_tools.clear()

    def _children_idle(self) -> None:
        if self._tool_selection_retiring:
            raise RuntimeError("Tool retirement persistence owns this session")
        if self._compacting:
            raise RuntimeError("Compaction owns this session; cancel/wait before changing context")
        if self.children.runtime.active:
            raise RuntimeError("Child work is active; cancel/wait for children before changing session policy/context")

    async def agents_command(self, args: str, *, images: tuple[ImageContent, ...] = ()) -> str:
        import json
        action, _, text = args.strip().partition(" ")
        if images and action not in {'start', 'steer', 'follow-up'}:
            raise ValueError('Child images belong only to start/steer/follow-up')
        if action in {"", "list"}:
            return json.dumps(self.children.runtime.snapshot(), indent=2)
        if action == "presets":
            return json.dumps({"presets": self.children.runtime.presets.snapshot(),
                               "error": self.children.preset_error}, indent=2)
        if action == "start":
            task, access, config = parse_child_start(text, self.children.runtime.presets)
            await self.initialize_extensions()
            self._rebuild_system_prompt()
            identifier = await self.children.runtime.start(task, access=access,
                                                              config=config.to_dict(), images=images)
            return f"Child {identifier} started ({access}); /agents inspect {identifier}"
        identifier, _, message = text.partition(" ")
        if action == "queue" and json.loads(message) != {"action": "list"}:
            raise ValueError("Native child queue is read-only; only exact list is accepted")
        result = await self.children.runtime.control(action, identifier, message, images=images)
        return json.dumps(result, indent=2)

    async def _extension_context(self, messages: list[AgentMessage]) -> list[AgentMessage]:
        if self._base_context_transform is not None:
            messages = await self._base_context_transform(messages)
        # The SDK supplies run-owned turn skills before this transform.
        result = await self.extensions.transform(messages)
        return result

    async def complete_command(self, command: str, prefix: str, identity: str, *,
                               timeout: float = 1, argument_input: dict[str, Any] | None = None) -> dict[str, Any]:
        """Read current registered completion data without command/model/UI authority."""
        return await self.extensions.complete_command(command, prefix, identity, timeout=timeout,
                                                      argument_input=argument_input)

    async def initialize_capabilities(self) -> None:
        """Open installed providers on this session's existing execution owner.

        Startup may overlap owner navigation or shutdown while doing disk IO.
        Pin the original inventory before suspension; never attach its result
        to a successor owner, or publish tools after the parent has retired.
        """
        await self._settle_retired_capabilities()
        if self._sdk_disposing:
            return
        selected = self.capabilities
        if self._capability_start is None:
            identity = (self.session_id, self.session_data.active_branch, self.working_dir)
            children = self.children
            def current() -> bool:
                return (not self._sdk_disposing and self.capabilities is selected and self.children is children
                        and identity == (self.session_id, self.session_data.active_branch, self.working_dir))
            async def start() -> None:
                from xdog.coding.core.capability_ports import SessionCapabilityHost
                if not current():
                    return
                # Retirement must be able to join this inventory even while its
                # host's first disk read has not completed yet.
                self._capability_loop = asyncio.get_running_loop()
                await self.application_capabilities.start(self._application_host)
                self._application_started = bool(self.application_capabilities.providers)
                if not current():
                    return
                host = self._capability_host
                if host is None:
                    host = await SessionCapabilityHost.open(self)
                    if not current():
                        return
                    self._capability_host = host
                if not current():
                    return
                await selected.start(host)
                if not current():
                    return
                parent = host.parent()
                if not parent.valid():
                    return  # Normal concurrent shutdown, not a provider failure.
                self._capabilities_started = bool(selected.providers)
                selected.tools  # Pin retiring definitions before provider attach.
                selected.attach(parent)
                self._publish_tools(self.capability_assembly.compose_tools(self.agent.local_tools))
                if self._extensions_initialized and self._tool_selection_restore_pending:
                    self._restore_active_tools()
                await self._persist_tool_retirement()
                await selected.flush()
                await self.application_capabilities.flush()
            self._capability_start_owner = selected
            self._capability_start = asyncio.create_task(start())
        from xdog.agent.async_work import wait_owned
        opening = self._capability_start
        opening_owner = self._capability_start_owner
        try:
            await wait_owned(opening)
        finally:
            if opening.done() and self._capability_start is opening:
                self._capability_start = None
                self._capability_start_owner = None
        if opening_owner is not self.capabilities and not self._sdk_disposing:
            # A successor activation may have joined an old in-flight startup.
            # Restore that successor too, without waiting for a human turn.
            await self.initialize_capabilities()

    def _retire_capabilities(self) -> None:
        if self._capability_host is not None:
            self._capability_host.retire()
        owner = self.capabilities
        self._retired_capability_tools.extend(owner.owned_tools)
        loop = self._capability_loop
        if loop is not None:
            self._retired_capabilities.append((loop, owner))
            if not loop.is_closed():
                loop.call_soon_threadsafe(lambda: asyncio.create_task(owner.aclose()))
        self._publish_tools(tuple(tool for tool in self.agent.local_tools if not owner.owns_tool(tool)))
        self.capabilities = CapabilitySet('session')
        self._capability_host = None
        self._capability_loop = None
        self._capabilities_started = False

    async def _settle_retired_capabilities(self) -> None:
        from functools import partial

        from xdog.agent.async_work import settle_cleanup
        from xdog.agent.cross_loop import settle_on
        async def close(item: tuple[asyncio.AbstractEventLoop, CapabilitySet]) -> None:
            loop, owner = item
            try:
                await settle_on(loop, owner.aclose())
            finally:
                self._retired_capabilities.remove(item)
        await settle_cleanup(*(partial(close, item) for item in tuple(self._retired_capabilities)),
                             message='Retired Coding capabilities failed to close')

    async def initialize_extensions(self) -> None:
        if self.extensions.enabled or self.extensions.commands():
            from xdog.agent.async_work import run_owned
            from xdog.coding.core.slash_commands import command_reservations
            builtins, skills = await run_owned(command_reservations)
            self.extensions.reserve_commands(builtins, skill_commands=skills)
            await self.extensions.initialize()
        if self.agent.state.model.startswith("ext-") and not self.agent.state.is_streaming:
            self.model_info = self.extensions.provider_model(self.agent.state.model)
            if self.model_info is not None:
                self.context_window = self.model_info.context_window
                self.max_prompt_tokens = self.model_info.max_prompt_tokens
                self.agent.set_model_capabilities(context_window=self.context_window,
                    max_prompt_tokens=self.max_prompt_tokens,
                    supports_tool_calls=self.model_info.supports_tool_calls)
        self._extensions_initialized = True
        if self._tool_selection_restore_pending:
            self._restore_active_tools()
            await self._persist_tool_retirement()

    def _publish_tools(self, tools: tuple[AgentTool, ...] | list[AgentTool]) -> None:
        """Publish local inventory, including caller-owned SDK injections.

        CapabilityAssembly and the actual child/session bindings determine
        replacement by identity. MCP is bound independently through McpRegistry.
        """
        replacement = ToolRepo(*tools, builtin=False)
        replacement.publish(self.agent)
        self.tool_repo = replacement

    async def _persist_tool_retirement(self) -> None:
        """Persist only narrowing after inventory changes; never grant a new selection."""
        from xdog.agent.async_work import run_owned
        if self._tool_selection_restore_pending:
            return  # Temporary startup mask is not retirement of the saved host inventory.
        selected, data = self.agent.active_tool_selection, self.session_data
        saved = data.active_tools
        if selected is None or saved is None or not set(selected) < set(saved):
            return
        if self._tool_selection_retiring:
            raise RuntimeError('Tool retirement persistence already owns this session')
        revision = self.agent.tool_selection_revision
        def precommit() -> None:
            if (self.session_data is not data or self.agent.tool_selection_revision != revision
                    or self.agent.active_tool_selection != selected):
                raise ValueError('Tool retirement owner/inventory changed before persistence')
        def commit() -> None:
            precommit()
            staged = replace(data, active_tools=selected, messages=self.messages)
            self.session_manager.save_session(staged, before_replace=precommit)
            data.active_tools = selected
            data.updated_at = staged.updated_at
        self._tool_selection_retiring = True
        self._context_changes += 1
        try:
            await run_owned(commit)  # Narrowed runtime stays fail-closed if storage fails.
        finally:
            self._context_changes -= 1
            self._tool_selection_retiring = False

    def _restore_active_tools(self) -> None:
        # Extension-model bootstrap runs before application/session publication.
        # Its temporary inventory must not retire a user's saved choices.
        if any(owner.providers and not owner.started for owner in self.capability_assembly.sets):
            self.agent.set_active_tools([], expected_revision=self.agent.tool_selection_revision)
            self._tool_selection_restore_pending = True
            return
        configured = {tool.name for tool in self.agent.state.tools}
        saved = self.session_data.active_tools
        selected = None if saved is None else tuple(name for name in saved if name in configured)
        # Unavailable saved names cannot create tools or regain authority on a
        # later registration; only a fresh reviewed choice can select them.
        self.agent.set_active_tools(selected, expected_revision=self.agent.tool_selection_revision)
        self._tool_selection_restore_pending = False

    @_idle_change
    def select_active_tools(self, names: list[str] | tuple[str, ...] | None, *, expected_revision: int,
                            alive: Callable[[], bool] | None = None) -> None:
        """Persist reviewed session selection before publishing it to the idle Agent."""
        from xdog.agent.extensions.active_tools import names as validate_names
        self._children_idle()
        if self._tool_selection_restore_pending or self.agent.is_streaming:
            raise ValueError('Tool selection requires initialized idle session')
        selected = validate_names(list(names) if isinstance(names, tuple) else names)
        def precommit() -> None:
            if not self._navigation_alive() or alive is not None and not alive():
                raise asyncio.CancelledError
            self.agent.validate_active_tools(selected, expected_revision=expected_revision)
        precommit()
        staged = replace(self.session_data, active_tools=selected, messages=self.messages)
        self.session_manager.save_session(staged, before_replace=precommit)
        self.agent.set_active_tools(selected, expected_revision=expected_revision)
        self.session_data.active_tools = selected
        self.session_data.updated_at = staged.updated_at

    async def extension_command(self, action: str, args: str = "", *,
                                user_authorized: bool = False) -> str | ExtensionCommandResult:
        if action == 'login':
            from xdog.agent.extensions.provider_oauth_host import login_arguments
            action, args = 'extensions', 'auth ' + login_arguments(args)
        previous_authority = self._user_operation_authorized
        try:
            native = self.extensions.is_native_command(action)
            if native:
                controller = self.runtime.controller if self.runtime is not None else self._sdk_controller
                if controller is None or not controller.owns_current_task:
                    raise RuntimeError('Native commands require the original controller owner')
                if self._tool_selection_retiring or self._compacting:
                    raise RuntimeError('Session policy/context is changing')
            else:
                self._children_idle()
            if self.agent.state.is_streaming:
                raise RuntimeError("Extension commands and reloads require an idle agent")
            await self.initialize_extensions()
            if action == "extensions":
                import json
                parts = args.split()
                if parts and parts[0] == 'auth':
                    arguments = args.split(maxsplit=1)[1] if len(parts) > 1 else ''
                    if self.provider_auth_handler is None:
                        if arguments.strip() not in ('', 'list'):
                            raise ValueError('Provider authentication requires an explicitly enabled host UI')
                        from xdog.agent.extensions.provider_oauth_host import auth_catalog
                        result = auth_catalog(self.extensions)
                    else:
                        result = await self.provider_auth_handler(arguments)
                else:
                    result = await self.extensions.manage(
                        parts[0] if parts else "list", parts[1] if len(parts) > 1 else "")
                await self.initialize_extensions()
                return json.dumps(result, indent=2)
            if user_authorized:
                self._user_operation_authorized = True
            try:
                output = await self.extensions.command(action, args)
            finally:
                self._user_operation_authorized = previous_authority
            await self.initialize_extensions()
            return output
        finally:
            self._user_operation_authorized = previous_authority

    async def _after_tool_call(self, ctx: AfterToolCallContext, cancel: asyncio.Event | None = None
                               ) -> AfterToolCallResult | None:
        if not self.extensions.enabled or ctx.is_error:
            return None  # A post hook cannot turn a denied/failed call into success.
        if any(not isinstance(part, TextContent) for part in ctx.result.content):
            return None  # Preserve non-text content rather than silently dropping it.
        text = "\n".join(part.text for part in ctx.result.content if isinstance(part, TextContent))
        try:
            result = await self.extensions.post(ctx.tool_call.name, ctx.args, text)
        except Exception:
            return None  # The original actual result remains visible after a post-hook error.
        return AfterToolCallResult(content=(TextContent(text=result),)) if result != text else None

    async def _tool_effect(self, ctx: BeforeToolCallContext) -> ToolEffect:
        """Describe the actual live binding, never model-supplied permission labels."""
        from xdog.agent.async_work import run_owned
        from xdog.coding.core.permissions import classify_tool_call

        if self.children.approval_free_control(ctx):
            queries = {'list', 'presets', 'inspect', 'wait'}
            return ToolEffect('read' if ctx.args.get('action') in queries else 'write', 'tool_data')
        active = tuple(tool for tool in self.agent.active_tools if tool.name == ctx.tool_call.name)
        supplied = tuple(tool for tool in ctx.context.tools or () if tool.name == ctx.tool_call.name)
        if len(active) == len(supplied) == 1 and active[0] is supplied[0]:
            effect = self.capability_assembly.tool_effect(active[0], dict(ctx.args))
            if effect is not None:
                return effect
        args = dict(ctx.args)
        path = args.get('path')
        root = self.agent.tool_context.get('fs_workspace')
        if (ctx.tool_call.name == 'filesystem' and isinstance(path, str) and root is not None
                and not Path(path).is_absolute()):
            args['path'] = str(Path(root) / path)
        return await run_owned(classify_tool_call, ctx.tool_call.name, args, self.working_dir)

    async def _before_tool_call(self, ctx: BeforeToolCallContext,
                                cancel: asyncio.Event | None = None) -> BeforeToolCallResult | None:
        from xdog.coding.core.session_info import TOOL_NAME
        def session_query() -> bool:
            selected = self._session_info_tool
            return (selected is not None and ctx.tool_call.name == TOOL_NAME and type(ctx.args) is dict
                    and not ctx.args and not self._sdk_disposing
                    and len([tool for tool in self.agent.active_tools if tool.name == TOOL_NAME]) == 1
                    and any(tool is selected for tool in self.agent.active_tools)
                    and ctx.context.tools is not None and any(tool is selected for tool in ctx.context.tools))
        if ctx.tool_call.name == TOOL_NAME and not session_query():
            return BeforeToolCallResult(block=True, reason="Session inspection binding is unavailable")
        if self.access.state.read_only and not session_query() and (await self._tool_effect(ctx)).access != 'read':
            return BeforeToolCallResult(block=True, reason="Host access scope is read-only")
        if self.extensions.enabled:
            await self.extensions.event("tool_start", {"tool_name": ctx.tool_call.name})
            from xdog.ai.utils.validation import validate_tool_arguments
            try:
                effective = await self.extensions.pre(ctx.tool_call.name, dict(ctx.args))
                if effective is None:
                    return BeforeToolCallResult(block=True, reason="Extension vetoed this tool call")
                tool = next(tool for tool in self.agent.state.tools if tool.name == ctx.tool_call.name)
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
        if ctx.tool_call.name == TOOL_NAME:
            return None if session_query() else BeforeToolCallResult(
                block=True, reason="Session inspection binding changed during validation")
        effect = await self._tool_effect(ctx)
        if self.access.state.read_only and effect.access != 'read':
            return BeforeToolCallResult(block=True, reason="Host access scope is read-only")
        if self.permissions.mode in {"ask", "deny"} and self.children.approval_free_control(ctx):
            return None
        result = await self.permissions.before_tool_call(ctx, cancel, effect=effect)
        current_effect = await self._tool_effect(ctx)
        if current_effect != effect:
            return BeforeToolCallResult(block=True, reason="Tool effect binding changed during approval")
        if self.access.state.read_only and current_effect.access != 'read':
            return BeforeToolCallResult(block=True, reason="Host access became read-only during approval")
        return result

    @_idle_change
    def access_control(self, read_only: bool) -> AccessState:
        """Explicit native user control, never registered as an Agent/extension tool."""
        self._children_idle()
        if self.agent.state.is_streaming:
            raise RuntimeError("Access changes require an idle agent")
        result = self.access.set_read_only(read_only)
        self._rebuild_system_prompt()
        return result

    def set_permission_mode(self, mode: str) -> PermissionMode:
        """Native user control for this live session only, never an Agent tool.

        Reject busy submissions before owner routing as well as at commit: an
        attempted grant must not sit behind a model turn or an approval queue.
        Configuration's fail-closed normalizer is deliberately not a command
        parser; a typo must leave the current policy and approval epoch intact.
        """
        selected = "allow-all" if mode == "full-access" else mode
        if selected not in {"ask", "ask-all", "allow-all", "deny"}:
            raise ValueError(f"Unknown permission mode: {mode!r}; expected ask, ask-all, allow-all or deny")
        self._children_idle()
        if (not self.agent.is_idle or self.permissions.has_pending_requests
                or self._context_changes or self._auth_changing):
            raise RuntimeError("Permission changes require an idle session without pending approvals/context changes")
        return self._set_permission_mode(cast(PermissionMode, selected))

    @_idle_change
    def _set_permission_mode(self, mode: PermissionMode) -> PermissionMode:
        """Commit only under the existing owner/context and child-admission gate."""
        self._children_idle()
        if (not self.agent.is_idle or self.permissions.has_pending_requests
                or self._context_changes != 1):
            raise RuntimeError("Permission changes require an idle session without pending approvals/context changes")
        self.permissions.set_mode(mode)
        return self.permissions.mode

    # -- Properties --

    @property
    def messages(self) -> list[AgentMessage]:
        return list(self.agent.state.messages)

    @property
    def session_id(self) -> str:
        return self.session_data.session_id

    @_idle_change
    def rename_session(self, name: str, *, expected_name: str | None = None,
                       alive: Callable[[], bool] | None = None) -> str:
        """Save metadata before publishing, retaining the data owner used by access/extensions."""
        from dataclasses import replace

        from xdog.agent.session_name import session_name
        selected = session_name(name)
        if expected_name is not None and session_name(expected_name) != self.session_data.name:
            raise ValueError('Session name changed since review; inspect before retrying')
        self._children_idle()
        if self.agent.is_streaming:
            raise RuntimeError('Session naming requires an idle agent')
        staged = replace(self.session_data, name=selected)
        if not self._navigation_alive():
            raise asyncio.CancelledError
        def guard() -> None:
            if (not self._navigation_alive() or alive is not None and not alive()
                    or expected_name is not None and self.session_data.name != expected_name):
                raise asyncio.CancelledError
        if alive is None:
            self.session_manager.save_session(staged)
        else:
            guard()
            self.session_manager.save_session(staged, before_replace=guard)
        self.session_data.name = staged.name
        self.session_data.updated_at = staged.updated_at
        return selected

    @property
    def model(self) -> str:
        return self.agent.state.model

    @property
    def is_streaming(self) -> bool:
        return self.agent.state.is_streaming

    @property
    def context_limit(self) -> int:
        """Prompt limit used for compaction; falls back to the model window."""
        return self.max_prompt_tokens or self.context_window or MAX_CONTEXT_TOKENS

    # -- Public API --

    @property
    def capability_assembly(self) -> CapabilityAssembly:
        """Compose current inventories without taking ownership of either lifetime."""
        return CapabilityAssembly(self.capabilities, self.application_capabilities)

    @property
    def active_skills(self) -> frozenset[str]:
        """Slugs whose instructions are currently in the system prompt."""
        return self.capability_assembly.active_skills(self._active_skills)

    @_idle_change
    def activate_skill(self, slug: str) -> None:
        """Put a skill's instructions in the system prompt until told otherwise.

        The obvious implementation — append the skill body as a message — is
        what the format's reference clients do, and it is a one-way door: a
        message cannot be taken back out of a conversation, so the body keeps
        occupying the window for the rest of the session whether or not it is
        still relevant. Keeping it in the system prompt is what makes
        deactivation possible at all.

        `Agent.set_skills` does the placing, so that reasoning now lives in one
        place instead of being re-derived by every product that has skills.
        """
        self._active_skills = self._active_skills | {slug}
        self._push_skills()
        self._rebuild_system_prompt()

    @_idle_change
    def deactivate_skill(self, slug: str) -> bool:
        """Drop a skill from the system prompt. False if it was not active."""
        if slug not in self._active_skills:
            return False
        self._active_skills = self._active_skills - {slug}
        self._push_skills()
        self._rebuild_system_prompt()
        return True

    def _retire_context_resources(self, *, mcp: bool = False) -> None:
        self._retired_resources.append((self.children, self.extensions, self.mcp if mcp else None))
        self.children.close()
        self.extension_dialogs.close()
        self.extensions.close()
        if mcp and self.mcp is not None:
            self.mcp.close()

    async def _settle_retired_resources(self) -> None:
        from functools import partial

        from xdog.agent.async_work import settle_cleanup
        async def close(resources: tuple[CodingChildren, ExtensionRuntime, McpRegistry | None]) -> None:
            children, extensions, mcp = resources
            try:
                await settle_cleanup(children.runtime.aclose, extensions.aclose,
                    *((mcp.aclose,) if mcp is not None else ()), message='Retired Coding worker cleanup failed')
            finally:
                if not any(owner is not None and owner._thread is not None and owner._thread.is_alive()
                           for owner in (children.runtime, extensions._manager, mcp)):
                    self._retired_resources.remove(resources)
        await settle_cleanup(*(partial(close, resources) for resources in tuple(self._retired_resources)),
                             message='Retired Coding context cleanup failed')

    def _release_retired_leases(self) -> None:
        if self._retired_resources or self._retired_capabilities:
            raise RuntimeError('Retired callback owners have not settled; writer leases retained')
        for lease in tuple(self._retiring_leases):
            lease.close()
            self._retiring_leases.discard(lease)

    async def _close_sdk_owner(self, controller: SessionController) -> None:
        from xdog.coding.core.session_lifecycle import close_input_owner
        await close_input_owner(self, controller, resources=(self._sdk_disposing
            or self._capabilities_started or self._application_started or bool(self._retired_capabilities)))

    async def _acquire_owner(self) -> tuple[SessionController, bool]:
        from xdog.coding.core.sessions.controller import SessionController
        from xdog.coding.core.sessions.ownership import SessionLease, lease_directory

        while True:
            with self._sdk_lock:
                if self._sdk_disposing:
                    raise RuntimeError('Session owner is closing')
                if self._history_error is not None:
                    raise RuntimeError('Session history persistence unavailable; close and reload explicitly'
                               ) from self._history_error
                if self.runtime is not None:
                    return self.runtime.controller, False
                closing = self._sdk_closing
                if closing is None or closing.done():
                    if closing is not None:
                        closing.result()  # Never reopen an owner whose cleanup failed.
                    if self._sdk_controller is None:
                        if self._lease is None:
                            self._lease = SessionLease.acquire(
                                lease_directory(self.session_manager._dir), self.session_id)
                        controller = SessionController(self, bound=False)
                        controller.on_control_settled = self._release_retired_leases
                        from xdog.coding.core.sessions.intake import attach_inbox
                        attach_inbox(controller)
                        controller._close_hook = lambda: self._close_sdk_owner(controller)
                        # Optional callback capabilities outlive an individual
                        # SDK await/asyncio.run; retain the one controller's own
                        # supported loop, never a second controller or daemon.
                        controller.start(own_thread=bool(
                            self.capabilities.providers or self.application_capabilities.providers))
                        self._sdk_controller = controller
                    self._sdk_users += 1
                    return self._sdk_controller, True
            await asyncio.shield(asyncio.wrap_future(closing))

    def _release_sdk_owner(self, controller: SessionController) -> Future[None] | None:
        with self._sdk_lock:
            self._sdk_users -= 1
            if (self._sdk_users == 0 and self._sdk_controller is controller
                    and not self._capabilities_started and not self._application_started
                    and not self._retired_capabilities):
                self._sdk_controller = None
                self._sdk_closing = controller.close()
                return self._sdk_closing
        return None

    async def send_message(self, user_text: str, *, images: tuple[ImageContent, ...] = (),
                           input_origin: str = "user") -> AssistantMessage | None:
        """Submit through the same owner used by ACP and native modes."""
        from xdog.agent.session_input import SessionInput
        controller, temporary = await self._acquire_owner()
        try:
            # A trusted control operation may run a turn under its existing
            # reservation, but a tool cannot recursively start another model loop.
            if controller.owns_current_task:
                if not self.agent.is_idle:
                    raise RuntimeError("Cannot recursively prompt an active agent")
                return await self._execute_message(user_text, images=images, input_origin=input_origin)
            value = SessionInput(uuid.uuid4().hex, "human" if input_origin == "user" else "automatic",
                                 "coding", user_text if user_text.strip() else "[Image/context input]", (), {})
            ticket = controller.submit(value, peer_id="local-sdk", images=images,
                                       input_origin=input_origin, raw_text=user_text)
            try:
                await asyncio.wrap_future(ticket.accepted)
                if temporary and controller.inbox is not None:
                    await controller.inbox.activate()
                result = await asyncio.wrap_future(ticket.result)
            except asyncio.CancelledError:
                await asyncio.wrap_future(controller.cancel(ticket.id, peer_id="local-sdk", wait=True))
                raise
            if result.status == 'failed':
                if result.cause is not None:
                    raise result.cause
                raise RuntimeError(result.error or 'Session task failed; inspect effects before retrying')
            if result.cause is not None:
                raise result.cause
            return result.response
        finally:
            if temporary:
                closing = self._release_sdk_owner(controller)
                if closing is not None:
                    await asyncio.shield(asyncio.wrap_future(closing))

    async def _execute_message(self, user_text: str, *, images: tuple[ImageContent, ...] = (),
                               input_origin: str = 'user') -> AssistantMessage | None:
        """Singleton compatibility entry into the same batch lifecycle."""
        from xdog.agent.session_input import SessionInput, decode_input
        from xdog.coding.core.sessions.batches import TurnInput

        if input_origin == 'session_input':
            import json
            item = TurnInput(decode_input(json.loads(user_text)), images=images, input_origin=input_origin)
        else:
            value = SessionInput(uuid.uuid4().hex, 'human' if input_origin == 'user' else 'automatic',
                                 'coding', user_text if user_text.strip() else '[Image/context input]', (), {})
            item = TurnInput(value, images=images, input_origin=input_origin, raw_text=user_text)
        return await self._execute_messages((item,), batch_id=uuid.uuid4().hex)

    async def _execute_messages(self, inputs: tuple[TurnInput, ...], *, batch_id: str) -> AssistantMessage | None:
        """Prepare independent input messages and execute exactly one shared Agent turn."""
        if self._tool_selection_retiring:
            raise RuntimeError("Tool retirement persistence owns this session")
        if self._compacting:
            raise RuntimeError("Compaction owns this session; cancel/wait before sending input")
        import json

        from xdog.agent.session_input import input_data
        from xdog.coding.core.sessions.batches import MAX_BATCH_BYTES, agent_message, input_size

        if not inputs or len(inputs) > 128 or len({item.value.id for item in inputs}) != len(inputs):
            raise ValueError('A turn requires distinct input messages')
        if not isinstance(batch_id, str) or not batch_id.strip() or len(batch_id.encode()) > 256 or '\x00' in batch_id:
            raise ValueError('Invalid batch identity')
        owner = self.runtime.controller if self.runtime is not None else self._sdk_controller
        maximum = (owner.input_byte_limit(inputs[0])
                   if len(inputs) == 1 and owner is not None and owner.owns_current_task else MAX_BATCH_BYTES)
        if sum(input_size(item) for item in inputs) > maximum:
            raise ValueError('Batch input exceeds capacity')
        origins = tuple(item.input_origin or ('user' if item.value.origin == 'human' else 'session_input')
                        for item in inputs)
        if any(origin not in {'user', 'extension', 'system', 'custom_context', 'session_input'} for origin in origins):
            raise ValueError('Invalid input origin')
        if 'custom_context' in origins and len(inputs) != 1:
            raise ValueError('Canonical continuation cannot share a batch')
        images = tuple(image for item in inputs for image in item.images)
        context_snapshot = await self.context_messages.capture() if 'user' in origins else None
        self._user_operation_authorized = False
        self._user_prompt_messages.clear()
        self._authorized_messages.clear()
        history_started = False
        try:
            self.handled_feedback = []
            await self.initialize_capabilities()
            if images:
                from xdog.ai.utils.image_files import validate_references
                await asyncio.to_thread(validate_references, images, self.model_info)
            await self.initialize_extensions()
            payload: dict[str, Any] = {'batch_id': batch_id, 'inputs': [input_data(item.value) for item in inputs]}
            if len(inputs) == 1:
                item = inputs[0]
                payload['user_input'] = (json.dumps(input_data(item.value)) if origins == ('session_input',) else
                                         item.raw_text if item.raw_text is not None else item.value.instruction)
            await self.extensions.event("turn_start", payload)
            await self.initialize_extensions()  # Lifecycle hooks may update provider metadata.
            if images:
                # A turn-start hook may replace the selected provider's vision
                # profile. Revalidate before history/model/compaction admission.
                await asyncio.to_thread(validate_references, images, self.model_info)
            await self._prepare_prompt()

            # Check for compaction before the turn
            await self._maybe_compact()

            # One lifecycle and one prompt, while retaining each member's message boundary.
            history_started = True
            if origins == ('custom_context',):
                from xdog.agent.core import CustomAgentMessage
                from xdog.agent.extensions.context_message import TYPE
                if (images or not self.agent.messages or not isinstance(self.agent.messages[-1], CustomAgentMessage)
                        or self.agent.messages[-1].type != TYPE):
                    raise ValueError('Canonical continuation requires the reviewed custom tail')
                from xdog.agent.async_work import run_owned
                from xdog.agent.extensions.context_message import images as context_images
                from xdog.ai.utils.image_files import validate_references
                await run_owned(lambda: validate_references(
                    context_images((self.agent.messages[-1],)), self.model_info))
                event_stream = await self.agent.continue_()
            else:
                messages = [agent_message(item) for item in inputs]
                self._user_prompt_messages = {
                    id(message): message for message, origin in zip(messages, origins, strict=True)
                    if origin == "user" and isinstance(message, UserMessage)}
                event_stream = await self.agent.prompt(messages,
                    initial_prompt_context=(lambda: self.context_messages.admit(context_snapshot))
                        if context_snapshot is not None else None)

            last_assistant: AssistantMessage | None = None
            async for event in event_stream:
                if isinstance(event, InputHandledEvent):
                    self.handled_feedback.append(event.feedback)
                if isinstance(event, ToolExecutionEndEvent):
                    await self.extensions.event("tool_end", {"tool_name": event.tool_name, "is_error": event.is_error})
                if isinstance(event, MessageEndEvent):
                    await self.extensions.event("message_end", {"role": getattr(event.message, "role", "custom")})
                if isinstance(event, MessageEndEvent) and isinstance(event.message, AssistantMessage):
                    last_assistant = event.message

            self.agent.drain_handled_inputs()
            await self.extensions.event("turn_end")

            return last_assistant
        finally:
            self._user_operation_authorized = False
            self._user_prompt_messages.clear()
            self._authorized_messages.clear()
            from xdog.agent.async_work import settle_cleanup
            async def stop_run() -> None:
                if not self.agent.is_idle:
                    await self.agent.cancel_and_wait()
            async def history() -> None:
                if history_started:
                    await self._persist_async()
            try:
                await settle_cleanup(stop_run, self._persist_tool_retirement, history,
                                     message='Coding turn finalization failed')
            finally:
                self._expire_turn_scoped_skills()

    def _expire_turn_scoped_skills(self) -> None:
        """Mirror SDK retirement; preparation failures do not consume pending skills."""
        retained = self._active_skills & frozenset(self.agent.active_skills)
        if retained != self._active_skills:
            self._active_skills = retained
            self._rebuild_system_prompt()

    def steer(self, message: str) -> None:
        """Queue a steering interrupt for the current turn."""
        self.agent.steer(message)

    def abort(self) -> None:
        """Cancel the current agent turn and its session-owned children."""
        self.children.abort()
        self.agent.abort()

    def cancel(self, *, preserve_identified: bool = False) -> None:
        """Cancel work; interactive owners may recover identified inputs after idle."""
        self._navigation_cancel.set()
        if not preserve_identified:
            self.agent.clear_all_queues()
        self.permissions.deny_all()
        self.children.abort()
        self.agent.abort()

    # -- Model / thinking --

    def model_catalog(self) -> tuple[Model, ...]:
        from xdog.coding.core.model_catalog import catalog
        return catalog(self.agent.stream_fn)

    def _resolve_selected_model(self, name: str) -> Model | None:
        from xdog.coding.core.model_catalog import resolve
        return resolve(self.agent.stream_fn, name)

    @_idle_change
    def set_model(self, model: str) -> None:
        """Select cached same-adapter metadata before committing any model/capability changes."""
        self._children_idle()
        if self.agent.is_streaming:
            raise RuntimeError('Model selection requires an idle owning session')
        if model.startswith("ext-") and self.extensions.provider_model(model) is None:
            import concurrent.futures
            async def initialize() -> None:
                await self.initialize_extensions()
            with concurrent.futures.ThreadPoolExecutor(max_workers=1,
                    thread_name_prefix="xdog-model-bootstrap") as pool:
                pool.submit(lambda: asyncio.run(initialize())).result()
        metadata = self._resolve_selected_model(model)
        selected = metadata.id if metadata is not None else model
        thinking = self.agent.options.thinking
        if metadata is not None:
            if not metadata.reasoning:
                thinking = None
            elif (thinking is not None and metadata.supported_efforts is not None
                  and thinking not in metadata.supported_efforts):
                raise ValueError('Model does not advertise current thinking level; choose off before switching')
        settings = {**self.settings.session_to_dict(), 'model': selected, 'thinking_level': thinking or 'off'}
        snapshot = replace(self.session_data, messages=self.messages, settings=settings, model=selected)
        self.session_manager.save_session(snapshot)  # Atomic disk commit before owning-session publication.
        self.model_info = metadata
        self.context_window = metadata.context_window if metadata is not None else MAX_CONTEXT_TOKENS
        self.max_prompt_tokens = metadata.max_prompt_tokens if metadata is not None else 0
        self.agent.set_model(selected)
        self.agent.set_model_capabilities(context_window=self.context_window, max_prompt_tokens=self.max_prompt_tokens,
                                          supports_tool_calls=(metadata.supports_tool_calls
                                                               if metadata is not None else None))
        self.agent.set_options(replace(self.agent.options, thinking=thinking))
        self.settings.load_session_settings(settings)
        self.session_data.messages = snapshot.messages
        self.session_data.settings = snapshot.settings
        self.session_data.model = snapshot.model
        self.session_data.updated_at = snapshot.updated_at

    def validate_image_inputs(self, images: tuple[tuple[str, int], ...]) -> None:
        if not images:
            return
        if self.model_info is None or self.model_info.supports_image_input is not True:
            raise ValueError("Selected model does not advertise image input; choose a vision-capable model")
        limits = self.model_info.vision_limits
        maximum = min(4, limits.max_prompt_images or 4) if limits else 4
        size = min(10 * 1024 * 1024, limits.max_prompt_image_size or 10 * 1024 * 1024) if limits else 10 * 1024 * 1024
        if len(images) > maximum:
            raise ValueError(f"Selected model accepts at most {maximum} images per prompt")
        for mime, length in images:
            if length > size or limits and limits.supported_media_types and mime not in limits.supported_media_types:
                raise ValueError("Attachment exceeds the model's image size/type limits")

    @_idle_change
    def set_thinking_level(self, level: ThinkingLevel | None) -> None:
        """Switch and persist the thinking/reasoning level."""
        self._children_idle()
        from dataclasses import replace

        model = self.model_info
        if level is not None and model is not None:
            if not model.reasoning:
                raise ValueError("Selected model does not advertise reasoning; choose off or another model")
            if model.supported_efforts is not None and level not in model.supported_efforts:
                raise ValueError("Thinking level not advertised by selected model; choose a supported level or off")
        self.agent.set_options(replace(self.agent.options, thinking=level))
        self.settings.set_session_thinking(level or "off")
        self._persist()

    # -- Compaction --

    async def compact(self, instructions: str = "", alive: Callable[[], bool] = lambda: True) -> None:
        """Reserve the same owner used by all turn and context producers."""
        from xdog.coding.core.sessions.operations import run_control

        controller, temporary = await self._acquire_owner()
        try:
            async def owned() -> None:
                self._children_idle()
                if self.agent.is_streaming or self._auth_changing:
                    raise RuntimeError("Compaction requires an idle owning session")
                await self._run_compaction(instructions=instructions, force=True, alive=alive)

            await run_control(self, owned, name='compact')
        finally:
            if temporary:
                closing = self._release_sdk_owner(controller)
                if closing is not None:
                    await asyncio.shield(asyncio.wrap_future(closing))

    async def _maybe_compact(self) -> None:
        """Check if the conversation needs compaction and run it if so."""
        messages = self.messages
        if not messages:
            return

        char_count = 0
        for msg in messages:
            if isinstance(msg, UserMessage):
                if isinstance(msg.content, str):
                    char_count += len(msg.content)
                else:
                    for part in msg.content:
                        if isinstance(part, TextContent):
                            char_count += len(part.text)
                        else:
                            char_count += 200
            elif isinstance(msg, AssistantMessage):
                for reply_part in msg.content:
                    if isinstance(reply_part, TextContent):
                        char_count += len(reply_part.text)
                    else:
                        char_count += 200
            else:
                char_count += 200

        estimated_tokens = char_count / 4
        threshold = self.context_limit * COMPACTION_THRESHOLD_RATIO

        if estimated_tokens > threshold:
            await self._run_compaction()

    async def _run_compaction(self, *, instructions: str = "", force: bool = False,
                              alive: Callable[[], bool] = lambda: True) -> None:
        """Summarize first; commit owned disk and memory together, never fake success."""
        from xdog.agent.extensions.compaction import CompactionVeto, chosen_boundary, record
        from xdog.agent.extensions.runtime import silent_extension_ui
        from xdog.agent.summarization import summary_instructions
        from xdog.coding.core.compaction.compaction import compact_messages, prepare_index
        from xdog.coding.core.compaction.utils import estimate_history_tokens
        summary_instructions(instructions)
        if not alive():
            raise ValueError("Compaction source was withdrawn")
        current_messages = self.messages
        if len(current_messages) < 4:
            return
        if self._compacting:
            raise RuntimeError("Compaction already owns this session")
        self._compacting = True
        try:
            await self.initialize_extensions()
            cut = prepare_index(current_messages, self.agent, force=force, context_limit=self.context_limit)
            if cut == 0:
                return
            outcome = await self.extensions.before_compaction({
                'messages': messages_to_dicts(current_messages), 'first_kept_index': cut,
                'original_count': len(current_messages), 'tokens_before': estimate_history_tokens(current_messages),
                'context_limit': self.context_limit, 'automatic': not force, 'instructions': instructions,
                'previous': self.session_data.compaction,
            })
            if outcome.cancel:
                raise CompactionVeto('Compaction canceled by extension ' + outcome.source + ': ' + outcome.reason)
            if not alive():
                raise RuntimeError('Compaction owner changed before summarization')
            cut = chosen_boundary(outcome, current_messages, cut)
            metadata = record(outcome, cut, len(current_messages))
            compacted = await compact_messages(current_messages, self.agent, instructions=instructions, force=force,
                                               prepared_index=cut, summary_override=outcome.summary)
            if compacted == current_messages:
                return
            if not alive() or self.messages != current_messages:
                raise RuntimeError("Compaction owner changed; original history retained")
            import time
            recovery_id = 'compaction-' + uuid.uuid4().hex
            if any(branch.get('branch_id') == recovery_id for branch in self.session_data.branches):
                raise ValueError('Compaction recovery checkpoint already exists; no overwrite')
            number = 1 + sum(branch.get('recovery_kind') == 'compaction' for branch in self.session_data.branches)
            recovery_access = self.access.state.to_dict()
            recovery = {
                'branch_id': recovery_id, 'branch_point': len(current_messages) - 1,
                'messages': messages_to_dicts(current_messages), 'parent_branch': self.session_data.active_branch,
                'created_at': time.time(),
                'label': f"Before compaction #{number} ({'manual' if force else 'automatic'})",
                'recovery_kind': 'compaction', 'model': self.model, 'settings': self.settings.session_to_dict(),
                'access': recovery_access, 'extension_entries': self.extensions.entries.snapshot(),
                'compaction': self.session_data.compaction,
                'active_tools': (list(self.agent.active_tool_selection)
                                 if self.agent.active_tool_selection is not None else None),
            }
            metadata = {**metadata, 'recovery_branch_id': recovery_id}
            # One atomic session write contains both the complete original branch
            # and the new active context; a failed save publishes neither.
            snapshot = replace(self.session_data, messages=compacted, settings=self.settings.session_to_dict(),
                               model=self.agent.state.model or "", compaction=metadata,
                               branches=[*self.session_data.branches, recovery])
            async def commit() -> None:
                await asyncio.to_thread(self.session_manager.save_session, snapshot)
                # Plan/extension persistence closures retain this data object.
                # Publish committed fields without orphaning those owners.
                self.session_data.messages = snapshot.messages
                self.session_data.settings = snapshot.settings
                self.session_data.model = snapshot.model
                self.session_data.updated_at = snapshot.updated_at
                self.session_data.compaction = snapshot.compaction
                self.session_data.branches = snapshot.branches
                self.agent.replace_messages(compacted)
                with silent_extension_ui():
                    await self.extensions.event('session_compact', {'compaction': {
                        **metadata, 'automatic': not force, 'before': len(current_messages), 'after': len(compacted)}})
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
            await self.event_bus.emit("compaction", message_count=len(compacted))
        except Exception as exc:
            if force:
                raise
            logger.warning("Automatic compaction failed; original history retained: %s", exc)
        finally:
            self._compacting = False


    def _stage_navigation(self, settings: SettingsManager, directory: Path, session_id: str, model: str,
                          entries: Any, access: Any) -> StagedExtensions:
        from xdog.coding.core.sdk import _as_thinking_level
        from xdog.coding.core.session_staging import prepare
        native = self._resolve_selected_model(model) if not model.startswith('ext-') else None
        return prepare(settings, directory, session_id, model, _as_thinking_level(settings.thinking_level),
                       entries, access, self.permissions.mode, native,
                       lambda: self._navigation_alive() and not self._navigation_cancel.is_set())

    def _publish_navigation_model(self, model: str, metadata: Model | None, thinking: ThinkingLevel | None) -> None:
        self.model_info = metadata
        self.context_window = metadata.context_window if metadata is not None else MAX_CONTEXT_TOKENS
        self.max_prompt_tokens = metadata.max_prompt_tokens if metadata is not None else 0
        self.agent.set_model(model)
        self.agent.set_model_capabilities(context_window=self.context_window, max_prompt_tokens=self.max_prompt_tokens,
            supports_tool_calls=metadata.supports_tool_calls if metadata is not None else None)
        self.agent.set_options(replace(self.agent.options, thinking=thinking))

    # -- Session management --

    @_idle_change
    def clear(self) -> None:
        """Clear conversation history."""
        self._children_idle()
        self.agent.clear_messages()
        self.session_data.compaction = None
        self._persist()

    @_idle_change
    def switch_session(self, session_id: str) -> bool:
        """Idle-only activation with workspace/model/settings and approval isolation."""
        self._children_idle()
        if self.agent.state.is_streaming:
            raise RuntimeError("Session navigation requires an idle agent")
        if session_id == self.session_id:
            return True
        loaded = self.session_manager.load_session(session_id)
        if loaded is None:
            return False
        if loaded.session_id != session_id:
            raise ValueError("Saved session identity does not match requested target")
        return self._activate_session(loaded)

    def _activate_session(self, loaded: SessionData) -> bool:
        """Stage a complete target, then persist and publish it once under the idle owner."""
        directory = Path(loaded.working_dir).expanduser().resolve() if loaded.working_dir else self.working_dir
        if not directory.is_dir():
            raise ValueError("Saved session workspace is unavailable")
        from xdog.coding.core.sdk import _as_thinking_level
        settings = SettingsManager(global_path=self.settings._global_path, project_dir=directory)
        settings.load_session_settings(loaded.settings)
        selected_model = loaded.model or settings.model or self.agent.state.model
        if not selected_model.startswith('ext-'):
            selected_metadata = self._resolve_selected_model(selected_model)
            selected_thinking = _as_thinking_level(settings.thinking_level)
            if (selected_metadata is not None and selected_metadata.reasoning and selected_thinking is not None
                    and selected_metadata.supported_efforts is not None
                    and selected_thinking not in selected_metadata.supported_efforts):
                raise ValueError('Saved model does not advertise saved thinking level; no fallback')
        tools = self.agent.local_tools
        from xdog.coding.config import get_data_dir
        next_mcp = McpRegistry(settings.mcp_servers, directory,
                              credential_dir=get_data_dir() / "mcp-credentials",
                              credential_backend=settings.mcp_credential_backend)
        if directory != self.working_dir:
            defaults = {tool.name: tool for tool in ToolRepo(cwd=directory).tools}
            tools = tuple(defaults.get(tool.name, tool) for tool in tools)
        try:
            staged = self._stage_navigation(settings, directory, loaded.session_id, selected_model,
                                             loaded.extension_entries, loaded.access)
            selected_model = staged.model.id if staged.model is not None else selected_model
            loaded.extension_entries = staged.runtime.entries.snapshot()
            if loaded.active_tools is not None:
                available = {tool.name for tool in tools}
                loaded.active_tools = tuple(name for name in loaded.active_tools if name in available)
            loaded.model = selected_model
            loaded.settings = {**settings.session_to_dict(), 'model': selected_model,
                               'thinking_level': staged.thinking or 'off'}
            settings.load_session_settings(loaded.settings)
        except BaseException:
            next_mcp.close()
            raise
        try:
            self._persist()  # Keep the previous live head before committing target metadata.
            if not self._navigation_alive() or self._navigation_cancel.is_set():
                raise asyncio.CancelledError
            self.session_manager.save_session(loaded)
        except BaseException:
            staged.close()
            next_mcp.close()
            raise
        self._retire_context_resources(mcp=True)
        self.mcp = next_mcp
        self.agent.set_mcp_registry(next_mcp)
        self.access.close()
        self.session_data = loaded
        self.working_dir = directory
        self.settings = settings
        self.permissions.clear_session_rules()
        self.agent.clear_all_queues()
        self._active_skills = frozenset()
        self._push_skills()
        self.agent.replace_messages(list(loaded.messages))
        self._publish_tools(tuple(tool for tool in tools
                                  if not any(tool is item for item in self._retired_capability_tools)))
        self._load_access()
        self._load_extensions(staged.runtime)
        self._load_children()
        self.agent.set_tool_context({**self.agent.tool_context, "fs_workspace": str(directory)})
        self._publish_navigation_model(selected_model, staged.model, staged.thinking)
        self._restore_active_tools()
        self._rebuild_system_prompt()
        return True

    @_idle_change
    def new_session(self) -> str:
        self._children_idle()
        if self.agent.state.is_streaming:
            raise RuntimeError("Session navigation requires an idle agent")
        from xdog.coding.core.sessions.ownership import SessionLease, lease_directory
        selected = uuid.uuid4().hex
        lease = SessionLease.acquire(lease_directory(self.session_manager._dir), selected)
        self._pending_lease = lease
        created = None
        original = b""
        try:
            created = self.session_manager.create_session(model=self.model, working_dir=self.working_dir,
                                                           session_id=selected)
            candidate = self.session_manager._session_path(created.session_id)
            original = candidate.read_bytes()
            if not self.switch_session(created.session_id):
                raise ValueError('New session candidate unavailable; current owner retained')
            return created.session_id
        except BaseException:
            if created is not None and self.session_id != created.session_id:
                owned = self.session_manager._session_path(created.session_id)
                if owned.is_file() and owned.read_bytes() == original:
                    self.session_manager.delete_session(created.session_id)
            raise
        finally:
            self._pending_lease = None
            if self._lease is not lease:
                lease.close()

    # -- Branch management --

    @_idle_change
    def create_branch(self, *, at_index: int | None = None, label: str = "") -> str:
        self._children_idle()
        if self.agent.state.is_streaming:
            raise RuntimeError("Branch navigation requires an idle agent")
        messages = self.messages
        points = history_points(messages)
        point = at_index if at_index is not None else (points[-1].index if points else -1)
        validate_checkpoint(messages, point)
        branch_id = uuid.uuid4().hex[:8]
        import time
        branch = {
            "branch_id": branch_id, "branch_point": point,
            "messages": messages_to_dicts(messages[:point + 1]),
            "parent_branch": self.session_data.active_branch, "created_at": time.time(), "label": label,
            "model": self.model, "settings": self.settings.session_to_dict(), "access": self.access.state.to_dict(),
            "extension_entries": self.extensions.entries.snapshot(),
            "compaction": self.session_data.compaction,
            "active_tools": (list(self.agent.active_tool_selection)
                             if self.agent.active_tool_selection is not None else None),
        }
        staged = replace(self.session_data, branches=[*self.session_data.branches, branch], messages=messages,
                         settings=self.settings.session_to_dict(), model=self.model)
        if messages and isinstance(messages[-1], AssistantMessage):
            staged.summary = next((part.text[:120] for part in messages[-1].content if isinstance(part, TextContent)),
                                  staged.summary)
        if not self._navigation_alive() or self._navigation_cancel.is_set():
            raise asyncio.CancelledError
        self.session_manager.save_session(staged)
        self.session_data.branches = staged.branches
        self.session_data.messages = staged.messages
        self.session_data.settings = staged.settings
        self.session_data.model = staged.model
        self.session_data.summary = staged.summary
        self.session_data.updated_at = staged.updated_at
        return branch_id

    @_idle_change
    def restore_branch(self, branch_id: str) -> bool:
        self._children_idle()
        if self.agent.state.is_streaming:
            raise RuntimeError("Branch navigation requires an idle agent")
        branch = next((value for value in self.session_data.branches if value.get("branch_id") == branch_id), None)
        if branch is None:
            return False
        from xdog.agent.extensions.compaction import saved_record
        restored_compaction = saved_record(branch.get('compaction'))
        selected_model = branch.get('model') or self.model
        if not selected_model.startswith('ext-'):
            from xdog.coding.core.sdk import _as_thinking_level
            selected_metadata = self._resolve_selected_model(selected_model)
            selected_thinking = _as_thinking_level(branch.get('settings', {}).get('thinking_level'))
            if (selected_metadata is not None and selected_metadata.reasoning and selected_thinking is not None
                    and selected_metadata.supported_efforts is not None
                    and selected_thinking not in selected_metadata.supported_efforts):
                raise ValueError('Branch model does not advertise saved thinking level; no fallback')
        from xdog.agent.extensions.active_tools import names as active_tool_names
        restored_tools = active_tool_names(branch.get("active_tools"))
        restored_access = AccessState.saved(branch).retain_restriction(self.access.state)
        validate_entries(branch.get("extension_entries", {}))
        restored = dicts_to_messages(branch["messages"])
        if restored:
            validate_checkpoint(restored, len(restored) - 1)
        target_settings = SettingsManager(global_path=self.settings._global_path, project_dir=self.working_dir)
        target_settings.load_session_settings(branch.get('settings', self.settings.session_to_dict()))
        staged: StagedExtensions = self._stage_navigation(target_settings, self.working_dir, self.session_id,
            selected_model, branch.get('extension_entries', {}), restored_access.to_dict())
        if restored_tools is not None:
            available = {tool.name for tool in self.agent.state.tools}
            restored_tools = tuple(name for name in restored_tools if name in available)
        selected_model = staged.model.id if staged.model is not None else selected_model
        target_settings.set_session_model(selected_model)
        target_settings.set_session_thinking(staged.thinking or 'off')
        import time
        points = history_points(self.messages)
        point = points[-1].index if points else -1
        return_branch = {'branch_id': uuid.uuid4().hex[:8], 'branch_point': point,
            'messages': messages_to_dicts(self.messages[:point + 1]), 'parent_branch': self.session_data.active_branch,
            'created_at': time.time(), 'label': 'Return checkpoint before branch navigation', 'model': self.model,
            'settings': self.settings.session_to_dict(), 'access': self.access.state.to_dict(),
            'extension_entries': self.extensions.entries.snapshot(), 'compaction': self.session_data.compaction,
            'active_tools': (list(self.agent.active_tool_selection)
                             if self.agent.active_tool_selection is not None else None)}
        committed = replace(self.session_data, messages=restored, model=selected_model,
            settings=target_settings.session_to_dict(), access=restored_access.to_dict(),
            extension_entries=staged.runtime.entries.snapshot(), compaction=restored_compaction,
            active_branch=branch_id, branches=[*self.session_data.branches, return_branch], active_tools=restored_tools)
        try:
            if not self._navigation_alive() or self._navigation_cancel.is_set():
                raise asyncio.CancelledError
            self.session_manager.save_session(committed)
        except BaseException:
            staged.close()
            raise
        self.session_data.messages = committed.messages
        self.session_data.branches = committed.branches
        self.session_data.model = committed.model
        self.session_data.settings = committed.settings
        self.session_data.updated_at = committed.updated_at
        self.settings.load_session_settings(committed.settings)
        self.agent.clear_all_queues()
        self.permissions.clear_session_rules()
        self.agent.replace_messages(restored)
        self.access.close()
        self.session_data.access = restored_access.to_dict()
        self._retire_context_resources()
        self.session_data.compaction = restored_compaction
        self.session_data.extension_entries = committed.extension_entries
        self.session_data.active_branch = branch_id
        self._load_extensions(staged.runtime)
        self._load_access()
        self.session_data.active_tools = committed.active_tools
        self._load_children()
        self._publish_navigation_model(selected_model, staged.model, staged.thinking)
        self._active_skills = frozenset()
        self._push_skills()
        self._restore_active_tools()
        self._rebuild_system_prompt()
        return True

    def _checkpoint_candidate(self, session_id: str, branch_id: str, index: int | None) -> SessionData:
        """Build the complete selection without writing/adopting its intermediate head."""
        if index is None and not branch_id:
            raise ValueError("Branch navigation requires an explicit branch")
        if index is not None and (type(index) is not int or index < -1 or index > 1000000):
            raise ValueError("Invalid zero-based checkpoint index")
        if session_id == self.session_id:
            loaded = replace(self.session_data, messages=list(self.messages), model=self.model,
                settings=self.settings.session_to_dict(), access=self.access.state.to_dict(),
                extension_entries=self.extensions.entries.snapshot())
        else:
            saved = self.session_manager.load_session(session_id)
            if saved is None:
                raise ValueError("Saved session unavailable")
            if saved.session_id != session_id:
                raise ValueError("Saved session identity does not match requested target")
            loaded = saved
        if not isinstance(loaded.branches, list) or any(not isinstance(value, dict) for value in loaded.branches):
            raise ValueError("Invalid saved branch records")
        source = next((value for value in loaded.branches if value.get("branch_id") == branch_id), None)
        if branch_id and source is None:
            raise ValueError("Saved branch unavailable")
        if source is not None:
            if not isinstance(source.get("messages"), list):
                raise ValueError("Invalid saved branch history")
            try:
                source_messages = dicts_to_messages(source["messages"])
            except (KeyError, TypeError, AttributeError, ValueError) as exc:
                raise ValueError("Invalid saved branch history") from exc
        else:
            source_messages = loaded.messages
        if index is None:
            if source_messages:
                validate_checkpoint(source_messages, len(source_messages) - 1)
            selected = source_messages
        else:
            validate_checkpoint(source_messages, index)
            selected = source_messages[:index + 1]
        model = (source.get("model") or loaded.model) if source is not None else loaded.model
        settings = source.get("settings", loaded.settings) if source is not None else loaded.settings
        if not isinstance(model, str) or not isinstance(settings, dict):
            raise ValueError("Invalid saved branch model/settings")
        from xdog.agent.extensions.active_tools import names as active_tool_names
        active_tools = active_tool_names(source.get('active_tools')) if source is not None else loaded.active_tools
        access = AccessState.saved(source).to_dict() if source is not None else loaded.access
        entries = source.get("extension_entries", {}) if source is not None else loaded.extension_entries
        from xdog.agent.extensions.compaction import saved_record
        compaction = saved_record(source.get("compaction") if source is not None else loaded.compaction)
        access = AccessState.from_dict(access).retain_restriction(self.access.state).to_dict()
        validate_entries(entries)
        import time
        created_at = time.time()
        branches = list(loaded.branches)
        active_branch = branch_id
        if index is not None:
            active_branch = uuid.uuid4().hex[:8]
            branches.append({"branch_id": active_branch, "branch_point": index,
                "messages": messages_to_dicts(selected), "parent_branch": branch_id or loaded.active_branch,
                "created_at": created_at, "label": f"Checkpoint at message {index + 1}",
                "model": model, "settings": settings, "access": access, "extension_entries": entries,
                "compaction": compaction, "active_tools": list(active_tools) if active_tools is not None else None})
        points = history_points(loaded.messages)
        return_point = points[-1].index if points else -1
        branches.append({"branch_id": uuid.uuid4().hex[:8], "branch_point": return_point,
            "messages": messages_to_dicts(loaded.messages[:return_point + 1]),
            "parent_branch": loaded.active_branch, "created_at": created_at,
            "label": "Return checkpoint before branch navigation", "model": loaded.model,
            "settings": loaded.settings, "access": loaded.access,
            "extension_entries": loaded.extension_entries, "compaction": loaded.compaction,
            "active_tools": list(loaded.active_tools) if loaded.active_tools is not None else None})
        return replace(loaded, messages=list(selected), model=model, settings=dict(settings), access=access,
            extension_entries=entries, compaction=compaction, active_branch=active_branch, branches=branches,
            active_tools=active_tools)

    @_idle_change
    def navigate_checkpoint(self, session_id: str, *, branch_id: str = "", index: int | None = None) -> str:
        """Atomically admit a saved branch/checkpoint, including cross-session selections.

        All target metadata, extensions and model preflight complete before a single
        target save/publication. Failure never first activates the target's live head.
        This is application-level staged admission, not a multi-file/OS transaction.
        """
        self._children_idle()
        if self.agent.state.is_streaming:
            raise RuntimeError("Checkpoint navigation requires an idle agent")
        candidate = self._checkpoint_candidate(session_id, branch_id, index)
        self._activate_session(candidate)
        return candidate.active_branch

    @_idle_change
    def goto_checkpoint(self, index: int) -> str:
        return self.navigate_checkpoint(self.session_id, index=index)

    # -- Internal --

    def _push_skills(self) -> None:
        """Tell the Agent which skills are active; it decides where they go.

        The index and the bodies are both its business now — this only says
        *which*, which is the part a product legitimately differs on.
        """
        try:
            self.agent.set_active_skills(sorted(self.active_skills))
        except Exception:
            logger.debug("could not push active skills to the agent", exc_info=True)

    def _native_tool_calls(self) -> bool:
        """Whether this model gets tool definitions through the API.

        Reads the same answer the Agent uses to decide whether to send them, so
        the two cannot disagree — a model whose tools are withheld from the API
        while the prompt stays silent about them has no tools at all, and
        nothing would report that.

        Unknown answers False, so the tools stay described. Both halves then
        fire, which wastes a few hundred tokens and cannot leave the model
        unable to act.
        """
        return self.agent.supports_tool_calls is True

    def _refresh_session_hint(self) -> None:
        """Nonsecret routing hint only; never an authorization capability."""
        context = self.agent.tool_context
        environment = context.get('subprocess_env', {})
        if not isinstance(environment, dict):
            raise ValueError('Invalid caller subprocess environment')
        context['subprocess_env'] = {**environment, 'XDOG_SESSION_ID': self.session_id}
        self.agent.set_tool_context(context)

    async def _prepare_prompt(self) -> None:
        from xdog.agent.async_work import run_owned
        from xdog.coding.core.session_preparation import PromptResources
        scope = (self.session_id, self.session_data.active_branch, self.working_dir)
        active = tuple(sorted(self.active_skills))
        prepared = await run_owned(PromptResources.read, self.working_dir, self.agent._skills, active)
        if scope != (self.session_id, self.session_data.active_branch, self.working_dir) or self._sdk_disposing:
            raise RuntimeError('Prompt resource owner changed during preparation')
        self._project_files, self._project_directory = prepared.files, self.working_dir
        self.agent.set_active_skills(active, prepared_skills=prepared.skills)
        self._rebuild_system_prompt()

    def _rebuild_system_prompt(self) -> None:
        """Rebuild and set the system prompt from config and tools."""
        self._refresh_session_hint()
        from xdog.coding.config import PlatformInfo, RuntimeConfig

        # Get tool definitions from the agent's current tools
        tool_defs = [
            {"name": t.name, "description": t.description, "parameters": t.parameters}
            for t in self.agent.active_tools
        ]

        # Build a minimal RuntimeConfig for system prompt generation
        prompt = build_system_prompt(
            RuntimeConfig(
                model=self.agent.state.model or "unknown",
                thinking_level=str(self.agent.options.thinking or "off"),
                permission_mode=self.permissions.mode,
                allowed_tools=tuple(t.name for t in self.agent.active_tools),
                custom_instructions=self.settings.custom_instructions,
                extensions=(),
                working_dir=str(self.working_dir),
                platform_info=PlatformInfo.detect(),
            ),
            tool_defs,
            file_entries=[{'type': 'file_content', 'path': item.relative_path, 'content': item.content}
                          for item in self._project_files if self._project_directory == self.working_dir],
            extra_context=_skills_context(frozenset(
                slug for slug, skill in self.agent._resolved_skills.items()
                if slug in self._active_skills and not skill.expires_after_turn)),
            native_tool_calls=self._native_tool_calls(),
            session_id=self.session_id,
        )
        self.agent.set_system_prompt(prompt + self.access.state.prompt())
    def _on_agent_event(self, event: AgentEvent) -> None:
        """Handle agent lifecycle events."""
        if (isinstance(event, MessageEndEvent) and isinstance(event.message, UserMessage)
                and self._authorized_messages.get(id(event.message)) is event.message):
            self._user_operation_authorized = True

    def _history_candidate(self) -> SessionData:
        if self._history_error is not None:
            raise RuntimeError('Session history persistence unavailable; close and reload explicitly'
                               ) from self._history_error
        summary = self.session_data.summary
        messages = self.messages
        if messages and isinstance(messages[-1], AssistantMessage):
            summary = next((part.text[:120] for part in messages[-1].content if isinstance(part, TextContent)), summary)
        return replace(self.session_data, messages=messages, settings=self.settings.session_to_dict(),
                       model=self.agent.state.model or '', summary=summary,
                       active_tools=(self.session_data.active_tools if self._tool_selection_restore_pending
                                     else self.agent.active_tool_selection))

    def _publish_history(self, owner: SessionData, saved: SessionData) -> None:
        if self.session_data is not owner or owner.active_branch != saved.active_branch:
            raise RuntimeError('History commit no longer belongs to this session owner')
        owner.messages, owner.settings, owner.model = saved.messages, saved.settings, saved.model
        owner.summary, owner.active_tools, owner.updated_at = saved.summary, saved.active_tools, saved.updated_at

    async def _persist_async(self) -> None:
        from xdog.agent.async_work import wait_owned
        from xdog.coding.core.session_history import HistorySnapshot
        if self.context_messages.uncertain:
            return  # A separate context admission receipt already owns this uncertainty.
        owner, lease = self.session_data, self._lease
        scope = (self.session_id, owner.active_branch)
        # Capture the baseline BEFORE the payload: a callback committed during
        # capture must invalidate the payload, never lend it a newer version.
        version = owner.updated_at
        snapshot = HistorySnapshot.capture(self._history_candidate())
        def guard() -> None:
            if (self.session_data is not owner or scope != (self.session_id, owner.active_branch)
                    or self._lease is not lease or lease is not None and lease._fd < 0
                    or owner.updated_at != version):
                raise RuntimeError('Original history writer changed during commit')
        def write() -> None:
            with self.session_manager._write_lock:
                saved = snapshot.commit(self.session_manager, guard)
                guard()
                self._publish_history(owner, saved)
        async def commit() -> None:
            try:
                await asyncio.to_thread(write)
            except BaseException as exc:
                self._history_error = exc
                controller = self.runtime.controller if self.runtime is not None else self._sdk_controller
                if controller is not None:
                    controller._broken = exc
                raise
        await wait_owned(asyncio.create_task(commit(), name='coding-history-commit'))

    def _persist(self) -> None:
        """Synchronous control/bootstrap save; ordinary turns use owned async IO."""
        if hasattr(self, 'context_messages') and self.context_messages.uncertain:
            return
        with self.session_manager._write_lock:
            owner = self.session_data
            saved = self._history_candidate()
            self.session_manager.save_session(saved)
            self._publish_history(owner, saved)

    async def manage_mcp_auth(self, server: str, action: str, receipt: str, lease: str,
                              alive: Callable[[], bool]) -> dict[str, Any]:
        if self.mcp is None:
            raise ValueError("MCP registry unavailable")
        if action in {"status", "cancel"}:
            return await self.mcp.manage_auth(server, action, receipt, lease=lease, alive=alive)
        if self._auth_changing or self.agent.is_streaming:
            raise RuntimeError("Authorization changes require an idle session")
        self._children_idle()
        with self.children.runtime.idle_scope():
            self._auth_changing = True
            try:
                result = await self.mcp.manage_auth(server, action, receipt, lease=lease, alive=alive)
                if action == "forget":
                    self.agent.sync_mcp_tools()
                    await self._persist_tool_retirement()
                return result
            finally:
                self._auth_changing = False


    async def manage_mcp(self, action: str, server: str = "") -> str:
        """Explicit idle-only process trust and tool-set management, not a second loop."""
        if self.mcp is None:
            raise ValueError("MCP registry unavailable")
        if action in {"list", "status"}:
            import json
            return json.dumps(self.mcp.status(), indent=2)
        self._children_idle()
        if self._auth_changing or self.agent.state.is_streaming:
            raise RuntimeError("MCP connection/schema changes require an idle agent")
        if action not in {"connect", "disconnect", "refresh"} or not server:
            raise ValueError("Usage: /mcp [list|connect SERVER|disconnect SERVER|refresh SERVER]")
        with self.children.runtime.idle_scope():
            if action == "connect":
                await self.mcp.connect(server)
            elif action == "disconnect":
                await self.mcp.disconnect(server)
            else:
                await self.mcp.refresh(server)
            try:
                self.agent.sync_mcp_tools()
            except ValueError:
                # SDK owns cross-registry name validation. Retire this failed
                # connection rather than leave a colliding server admitted.
                await self.mcp.disconnect(server)
                self.agent.sync_mcp_tools()
                await self._persist_tool_retirement()
                raise
            await self._persist_tool_retirement()
            return f"MCP {server}: {action} completed; external tools remain subject to current permission policy"

    def enable_runtime(self, *, bound: bool) -> SessionRuntime:
        from xdog.coding.core.sessions.runtime import SessionRuntime
        with self._sdk_lock:
            if self._sdk_disposing:
                raise RuntimeError('Session is closing or closed')
            if self.runtime is None:
                if self._sdk_controller is not None or self._sdk_closing is not None and not self._sdk_closing.done():
                    raise RuntimeError('An SDK owner is active/settling; cannot create a second session owner')
                self.runtime = SessionRuntime(self, bound=bound)
            return self.runtime

    async def _aclose_resources(self) -> None:
        from xdog.coding.core.session_lifecycle import close_resources
        await close_resources(self)

    async def aclose(self) -> None:
        """Await one shared close, including failures, despite repeated cancellation."""
        from xdog.agent.async_work import wait_owned
        from xdog.coding.core.session_lifecycle import request_close
        await wait_owned(asyncio.wrap_future(request_close(self, asynchronous=True)))

    def dispose(self) -> Future[None]:
        """Request closure; await aclose() (or this receipt) for actual settlement."""
        from xdog.coding.core.session_lifecycle import request_close
        return request_close(self)

    def _dispose_resources(self) -> None:
        from xdog.coding.core.session_lifecycle import settle_sync
        def unsubscribe() -> None:
            if self._unsubscribe is not None:
                self._unsubscribe()
                self._unsubscribe = None
        def release_writer() -> None:
            if self._retired_resources or self._retired_capabilities:
                raise RuntimeError('Retired callback cleanup is incomplete; original writer lease retained')
            if not self.agent.closed:
                raise RuntimeError('Agent cleanup is incomplete; original writer lease retained')
            owners = (self.children.runtime, self.extensions._manager, self.mcp)
            if any(owner is not None and owner._thread is not None and owner._thread.is_alive() for owner in owners):
                raise RuntimeError('Worker cleanup is incomplete; original writer lease retained')
            if self._lease is not None:
                self._lease.close()
                self._lease = None
        settle_sync(self.extension_dialogs.close, self.access.close, self.permissions.close,
                    unsubscribe, self._release_retired_leases, release_writer,
                    message='Coding session finalization failed')


def _skills_context(active: frozenset[str] = frozenset()) -> str:
    """Coding's UI instruction only; Agent alone renders skill index and bodies."""
    if not active:
        return ''
    return (
        '# Active skills\n\nThe user activated these; follow them.\n\n'
        'You cannot deactivate a skill — there is no tool for it, by design. '
        'If one looks finished or irrelevant, suggest `/unload <name>`; the user decides.\n\n'
        '(active: ' + ', '.join(sorted(active)) + ')'
    )
