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
from typing import TYPE_CHECKING, Any, Concatenate, ParamSpec, TypeVar

from xdog.agent import (
    AfterToolCallContext,
    AfterToolCallResult,
    AgentEndEvent,
    AgentEvent,
    AgentMessage,
    BeforeToolCallContext,
    BeforeToolCallResult,
    InputHandledEvent,
    MessageEndEvent,
    ToolExecutionEndEvent,
)
from xdog.agent.agent import Agent
from xdog.agent.extensions.command_result import ExtensionCommandResult
from xdog.agent.extensions.dialogs import DialogBroker
from xdog.agent.extensions.runtime import ExtensionRuntime
from xdog.agent.extensions.state import ExtensionEntries, validate_entries
from xdog.agent.history import history_points, validate_checkpoint
from xdog.agent.mcp import McpManager
from xdog.agent.planning import PlanOwner, PlanState
from xdog.agent.supervision_config import parse_child_start
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
from xdog.coding.core.permissions import PermissionManager
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
        runtime = self.runtime
        controller = runtime.controller if runtime is not None else self._sdk_controller
        if (controller is None and runtime is None and self._sdk_closing is not None
                and not self._sdk_closing.done()):
            raise RuntimeError('SDK owner cleanup is still settling; wait before changing session controls')
        if controller is not None and not controller.owns_current_task:
            async def owned() -> R:
                return wrapped(self, *args, **kwargs)
            try:
                same_loop = asyncio.get_running_loop() is controller._loop
            except RuntimeError:
                same_loop = False
            if same_loop:
                raise RuntimeError("Session controls must be submitted to the session owner")
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
        if self._tool_selection_retiring:
            raise RuntimeError("Tool retirement persistence owns this session")
        if self._compacting:
            raise RuntimeError("Compaction owns this session; cancel/wait before changing context")
        if self._auth_changing:
            raise RuntimeError("Authorization operation still owns session changes; wait/cancel before navigating")
        with self.children.supervisor.idle_scope():
            if method.__name__ in {'switch_session', 'restore_branch', 'navigate_checkpoint'}:
                self._navigation_cancel.clear()
            self._context_changes += 1
            target_lease = None
            try:
                if method.__name__ in {'switch_session', 'navigate_checkpoint'}:
                    target = args[0] if args else kwargs.get('session_id')
                    if isinstance(target, str) and target != self.session_id:
                        from xdog.coding.core.sessions.ownership import SessionLease, runtime_directory
                        pending = self._pending_lease
                        target_lease = (pending if pending is not None and pending.session_id == target else
                            SessionLease.acquire(runtime_directory(self.session_manager._dir), target))
                result = method(self, *args, **kwargs)
                if target_lease is not None and self.session_id == target_lease.session_id:
                    previous, self._lease = self._lease, target_lease
                    if previous is not None:
                        if self.runtime is not None:
                            self.runtime.retain_lease(previous)
                        elif controller is not None:
                            self._sdk_retiring_leases.add(previous)
                        else:
                            previous.close()
                return result
            finally:
                if target_lease is not None and self._lease is not target_lease:
                    target_lease.close()
                self._context_changes -= 1
                self._refresh_session_hint()
                if (self._context_changes == 0
                        and before_scope != (self.session_id, self.session_data.active_branch)):
                    if self.runtime is not None:
                        self.runtime.context_changed()
                    elif controller is not None:
                        discarded = controller.rotate(self.session_data.active_branch)

                        async def rebind_sdk() -> None:
                            from xdog.coding.core.sessions.store import InputStore
                            await asyncio.wrap_future(discarded)
                            previous = controller.inbox
                            if previous is not None:
                                await previous.retire()
                            controller.store = await asyncio.to_thread(InputStore, self)
                            from xdog.coding.core.sessions.intake import attach_inbox
                            inbox = attach_inbox(controller, previous.store if previous is not None else None)
                            await inbox.activate()

                        controller.settle_before_release(rebind_sdk())
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
    tool_registry: Any  # kept for backward compat, may be None
    bash: Any  # kept for backward compat, may be None
    working_dir: Path
    context_window: int = MAX_CONTEXT_TOKENS
    max_prompt_tokens: int = 0
    model_info: Model | None = None
    permissions: PermissionManager = field(default_factory=PermissionManager)
    mcp: McpManager | None = None
    event_bus: EventBus = field(default_factory=get_event_bus)
    plan: PlanOwner = field(init=False)
    children: CodingChildren = field(init=False)
    context_messages: Any = field(init=False, repr=False)
    extensions: ExtensionRuntime = field(init=False)
    extension_dialogs: DialogBroker = field(init=False)
    provider_auth_handler: Callable[[str], Awaitable[dict[str, Any]]] | None = field(
        default=None, init=False, repr=False)
    _base_context_transform: TransformContextFn | None = field(default=None, init=False, repr=False)
    handled_feedback: list[str] = field(default_factory=list, init=False)
    _base_input_transform: InputTransformFn | None = field(default=None, init=False, repr=False)
    runtime: SessionRuntime | None = field(default=None, init=False, repr=False)
    _sdk_controller: SessionController | None = field(default=None, init=False, repr=False)
    _sdk_users: int = field(default=0, init=False, repr=False)
    _sdk_closing: Future[None] | None = field(default=None, init=False, repr=False)
    _sdk_retiring_leases: set[SessionLease] = field(default_factory=set, init=False, repr=False)
    _sdk_disposing: bool = field(default=False, init=False, repr=False)
    _sdk_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _lease: SessionLease | None = field(default=None, init=False, repr=False)
    _pending_lease: SessionLease | None = field(default=None, init=False, repr=False)
    _unsubscribe: Any = field(default=None, init=False, repr=False)
    _context_changes: int = field(default=0, init=False, repr=False)
    _auth_changing: bool = field(default=False, init=False, repr=False)
    #: Slugs of skills whose instructions are currently in the system prompt.
    #: Held here rather than pushed into the message history so they can be
    #: taken back out — see `activate_skill`.
    _navigation_alive: Callable[[], bool] = field(default_factory=lambda: (lambda: True), init=False, repr=False)
    _navigation_cancel: threading.Event = field(default_factory=threading.Event, init=False, repr=False)
    _compacting: bool = field(default=False, init=False, repr=False)
    _active_skills: frozenset[str] = field(default=frozenset(), init=False, repr=False)
    _tool_selection_restore_pending: bool = field(default=False, init=False, repr=False)
    _tool_selection_retiring: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        self._refresh_session_hint()
        # Restore session-level settings
        if self.session_data.settings:
            self.settings.load_session_settings(self.session_data.settings)
        self.settings.set_session_max_messages_per_turn(self.settings.max_messages_per_turn)

        from xdog.agent.supervision import RESERVED_TOOLS
        if any(tool.name in RESERVED_TOOLS for tool in self.agent.state.tools):
            raise ValueError("update_plan/agent_spawn/agent_control are reserved for session-owned metadata")
        # Sessions constructed outside the SDK still receive enforcement.
        self.agent.set_before_tool_call(self._before_tool_call)
        self._load_plan()
        self._base_context_transform = self.agent.context_transform
        self._base_input_transform = self.agent.input_transform
        self._load_extensions()
        self._load_children()
        self.agent.set_after_tool_call(self._after_tool_call)
        if self.mcp is None:
            from xdog.coding.config import get_data_dir
            self.mcp = McpManager(self.settings.mcp_servers, self.working_dir,
                                  credential_dir=get_data_dir() / "mcp-credentials",
                                  credential_backend=self.settings.mcp_credential_backend)

        if self.session_data.active_tools is not None:
            # Block model tool availability until configured extension inventory
            # is restored. A saved empty list must never become defaults.
            self.agent.set_active_tools([], expected_revision=self.agent.tool_selection_revision)
            self._tool_selection_restore_pending = True

        # Subscribe to agent events for persistence and event bus forwarding
        self._unsubscribe = self.agent.subscribe(self._on_agent_event)
        from xdog.coding.core.context_messages import create
        self.context_messages = create(self)

    def _load_plan(self) -> None:
        data = self.session_data
        def persist(state: PlanState) -> None:
            previous = data.plan
            data.plan = state.to_dict()
            try:
                self.session_manager.save_session(data)
            except BaseException:
                data.plan = previous
                raise
        self.plan = PlanOwner(PlanState.from_dict(data.plan), persist)
        self.agent.set_tools((*[tool for tool in self.agent.state.tools if tool.name != "update_plan"],
                              self.plan.tool()))

    async def _transform_user_input(self, message: UserMessage, source: str,
                                    cancel: asyncio.Event | None = None) -> UserMessage | HandledInput:
        try:
            if self._base_input_transform is not None:
                value = await self._base_input_transform(message, source, cancel)
                if isinstance(value, HandledInput):
                    return value
                message = value
            return await self.extensions.input(message, source, channel="coding")
        finally:
            await self._sync_extension_tools()

    def _load_extensions(self, prepared: ExtensionRuntime | None = None) -> None:
        data = self.session_data
        def persist(value: dict[str, Any]) -> None:
            previous = data.extension_entries
            data.extension_entries = value
            try:
                self.session_manager.save_session(data)
            except BaseException:
                data.extension_entries = previous
                raise
        entries = ExtensionEntries(data.extension_entries, persist) if prepared is None else prepared.entries
        if prepared is not None:
            entries.bind_persistence(persist)
        self.extension_dialogs = DialogBroker()
        def context() -> dict[str, Any]:
            return {"session_id": self.session_id, "cwd": str(self.working_dir), "model": self.model,
                    "planning_mode": self.plan.state.mode, "permission_mode": self.permissions.mode}
        self.extensions = prepared or ExtensionRuntime(self.settings.trusted_extensions, self.settings.extension_root,
            self.working_dir, settings=self.settings.extension_settings, context=context, entries=entries,
            inspection=self._extension_inspection)
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
        self.children = CodingChildren(self)
        base = tuple(tool for tool in self.agent.state.tools if tool.name not in {"agent_spawn", "agent_control"})
        self.agent.set_tools((*base, *self.children.supervisor.tools()))

    def _children_idle(self) -> None:
        if self._tool_selection_retiring:
            raise RuntimeError("Tool retirement persistence owns this session")
        if self._compacting:
            raise RuntimeError("Compaction owns this session; cancel/wait before changing context")
        if self.children.supervisor.active:
            raise RuntimeError("Child work is active; cancel/wait for children before changing session policy/context")

    async def agents_command(self, args: str, *, images: tuple[ImageContent, ...] = ()) -> str:
        import json
        action, _, text = args.strip().partition(" ")
        if images and action not in {'start', 'steer', 'follow-up'}:
            raise ValueError('Child images belong only to start/steer/follow-up')
        if action in {"", "list"}:
            return json.dumps(self.children.supervisor.snapshot(), indent=2)
        if action == "presets":
            return json.dumps({"presets": self.children.supervisor.presets.snapshot(),
                               "error": self.children.preset_error}, indent=2)
        if action == "start":
            task, access, config = parse_child_start(text, self.children.supervisor.presets)
            await self.initialize_extensions()
            self._rebuild_system_prompt()
            identifier = await self.children.supervisor.start(task, access=access,
                                                              config=config.to_dict(), images=images)
            return f"Child {identifier} started ({access}); /agents inspect {identifier}"
        identifier, _, message = text.partition(" ")
        result = await self.children.supervisor.control(action, identifier, message, images=images)
        return json.dumps(result, indent=2)

    async def _extension_context(self, messages: list[AgentMessage]) -> list[AgentMessage]:
        try:
            if self._base_context_transform is not None:
                messages = await self._base_context_transform(messages)
            # The SDK supplies run-owned turn skills before this transform.
            result = await self.extensions.transform(messages)
            return result
        finally:
            await self._sync_extension_tools()

    async def complete_command(self, command: str, prefix: str, identity: str, *,
                               timeout: float = 1, argument_input: dict[str, Any] | None = None) -> dict[str, Any]:
        """Read current registered completion data without command/model/UI authority."""
        try:
            return await self.extensions.complete_command(command, prefix, identity, timeout=timeout,
                                                          argument_input=argument_input)
        finally:
            await self._sync_extension_tools()

    async def initialize_extensions(self) -> None:
        try:
            if self.extensions.enabled:
                await self.extensions.initialize()
            if self.agent.state.model.startswith("ext-") and not self.agent.state.is_streaming:
                self.model_info = self.extensions.provider_model(self.agent.state.model)
                if self.model_info is not None:
                    self.context_window = self.model_info.context_window
                    self.max_prompt_tokens = self.model_info.max_prompt_tokens
                    self.agent.set_model_capabilities(context_window=self.context_window,
                        max_prompt_tokens=self.max_prompt_tokens,
                        supports_tool_calls=self.model_info.supports_tool_calls)
        finally:
            await self._sync_extension_tools()
        if self._tool_selection_restore_pending:
            self._restore_active_tools()
            await self._persist_tool_retirement()

    async def _sync_extension_tools(self) -> None:
        self.extensions.sync_tools(self.agent)
        await self._persist_tool_retirement()

    async def _persist_tool_retirement(self) -> None:
        """Persist only narrowing after inventory changes; never grant a new selection."""
        from xdog.agent.extensions.runtime import _owned_disk
        if self._tool_selection_restore_pending:
            return  # Temporary startup mask is not retirement of the saved extension inventory.
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
            await _owned_disk(commit)  # Narrowed runtime stays fail-closed if storage fails.
        finally:
            self._context_changes -= 1
            self._tool_selection_retiring = False

    def _restore_active_tools(self) -> None:
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

    async def extension_command(self, action: str, args: str = "") -> str | ExtensionCommandResult:
        if action == 'login':
            from xdog.agent.extensions.provider_oauth_host import login_arguments
            action, args = 'extensions', 'auth ' + login_arguments(args)
        try:
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
            output = await self.extensions.command(action, args)
            await self.initialize_extensions()
            return output
        finally:
            await self._sync_extension_tools()

    async def _after_tool_call(self, ctx: AfterToolCallContext, cancel: asyncio.Event | None = None
                               ) -> AfterToolCallResult | None:
        try:
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
        finally:
            await self._sync_extension_tools()

    async def _before_tool_call(self, ctx: BeforeToolCallContext,
                                cancel: asyncio.Event | None = None) -> BeforeToolCallResult | None:
        try:
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
            if self.plan.state.mode == "planning":
                safe = (ctx.tool_call.name == "current_time" or ctx.tool_call.name == "update_plan"
                        or ctx.tool_call.name == "filesystem"
                        and ctx.args.get("action") in {"read", "ls", "grep", "find"})
                if not safe:
                    return BeforeToolCallResult(
                        block=True, reason="Planning is read-only; tool blocked until explicit execution")
            if ctx.tool_call.name == "update_plan":
                return None
            return await self.permissions.before_tool_call(ctx, cancel)
        finally:
            await self._sync_extension_tools()

    @_idle_change
    def plan_control(self, action: str, revision: int, objective: str = "") -> PlanState:
        self._children_idle()
        if self.agent.state.is_streaming:
            raise RuntimeError("Planning transitions require an idle agent")
        result = self.plan.change(action, revision, objective)
        self._rebuild_system_prompt()
        return result

    # -- Properties --

    @property
    def messages(self) -> list[AgentMessage]:
        return list(self.agent.state.messages)

    @property
    def session_id(self) -> str:
        return self.session_data.session_id

    def rename_session(self, name: str, *, expected_name: str | None = None,
                       alive: Callable[[], bool] | None = None) -> str:
        """Save metadata before publishing, retaining the data owner used by plan/extensions."""
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
    def active_skills(self) -> frozenset[str]:
        """Slugs whose instructions are currently in the system prompt."""
        return self._active_skills

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

    def _release_sdk_leases(self) -> None:
        for lease in tuple(self._sdk_retiring_leases):
            lease.close()
            self._sdk_retiring_leases.discard(lease)

    async def _close_sdk_owner(self, controller: SessionController) -> None:
        if controller.inbox is not None:
            await controller.inbox.retire()
        self._release_sdk_leases()

    async def _acquire_owner(self) -> tuple[SessionController, bool]:
        from xdog.coding.core.sessions.controller import SessionController
        from xdog.coding.core.sessions.ownership import SessionLease, runtime_directory

        while True:
            with self._sdk_lock:
                if self._sdk_disposing:
                    raise RuntimeError('Session owner is closing')
                if self.runtime is not None:
                    return self.runtime.controller, False
                closing = self._sdk_closing
                if closing is None or closing.done():
                    if closing is not None:
                        closing.result()  # Never reopen an owner whose cleanup failed.
                    if self._sdk_controller is None:
                        if self._lease is None:
                            self._lease = SessionLease.acquire(
                                runtime_directory(self.session_manager._dir), self.session_id)
                        controller = SessionController(self, bound=False)
                        controller.on_control_settled = self._release_sdk_leases
                        from xdog.coding.core.sessions.intake import attach_inbox
                        attach_inbox(controller)
                        controller._close_hook = lambda: self._close_sdk_owner(controller)
                        controller.start()
                        self._sdk_controller = controller
                    self._sdk_users += 1
                    return self._sdk_controller, True
            await asyncio.shield(asyncio.wrap_future(closing))

    def _release_sdk_owner(self, controller: SessionController) -> Future[None] | None:
        with self._sdk_lock:
            self._sdk_users -= 1
            if self._sdk_users == 0 and self._sdk_controller is controller:
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
        if sum(input_size(item) for item in inputs) > MAX_BATCH_BYTES:
            raise ValueError('Batch input exceeds capacity')
        origins = tuple(item.input_origin or ('user' if item.value.origin == 'human' else 'session_input')
                        for item in inputs)
        if any(origin not in {'user', 'extension', 'system', 'custom_context', 'session_input'} for origin in origins):
            raise ValueError('Invalid input origin')
        if 'custom_context' in origins and len(inputs) != 1:
            raise ValueError('Canonical continuation cannot share a batch')
        images = tuple(image for item in inputs for image in item.images)
        context_snapshot = await self.context_messages.capture() if 'user' in origins else None
        try:
            self.handled_feedback = []
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
            # Rebuild system prompt before each turn
            self._rebuild_system_prompt()

            # Check for compaction before the turn
            await self._maybe_compact()

            # One lifecycle and one prompt, while retaining each member's message boundary.
            if origins == ('custom_context',):
                from xdog.agent.core import CustomAgentMessage
                from xdog.agent.extensions.context_message import TYPE
                if (images or not self.agent.messages or not isinstance(self.agent.messages[-1], CustomAgentMessage)
                        or self.agent.messages[-1].type != TYPE):
                    raise ValueError('Canonical continuation requires the reviewed custom tail')
                from xdog.agent.extensions.context_message import images as context_images
                from xdog.agent.supervision_transfer import settled_image_work
                from xdog.ai.utils.image_files import validate_references
                await settled_image_work(lambda: validate_references(
                    context_images((self.agent.messages[-1],)), self.model_info))
                event_stream = await self.agent.continue_()
            else:
                messages = [agent_message(item) for item in inputs]
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
            # Persist after turn
            self._persist()

            await self.extensions.event("turn_end")

            return last_assistant
        finally:
            await self._sync_extension_tools()
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
            recovery_plan = self.plan.state.to_dict()
            recovery_plan['steps'] = list(recovery_plan['steps'])
            recovery = {
                'branch_id': recovery_id, 'branch_point': len(current_messages) - 1,
                'messages': messages_to_dicts(current_messages), 'parent_branch': self.session_data.active_branch,
                'created_at': time.time(),
                'label': f"Before compaction #{number} ({'manual' if force else 'automatic'})",
                'recovery_kind': 'compaction', 'model': self.model, 'settings': self.settings.session_to_dict(),
                'plan': recovery_plan, 'extension_entries': self.extensions.entries.snapshot(),
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
                          entries: Any, plan: Any) -> StagedExtensions:
        from xdog.coding.core.sdk import _as_thinking_level
        from xdog.coding.core.session_staging import prepare
        native = self._resolve_selected_model(model) if not model.startswith('ext-') else None
        return prepare(settings, directory, session_id, model, _as_thinking_level(settings.thinking_level),
                       entries, plan, self.permissions.mode, native,
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
        from xdog.coding.core.tools import get_default_tools
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
        tools = self.agent.state.tools
        if self.mcp is not None:
            aliases = {tool.name for tool in self.mcp.tools()}
            tools = tuple(tool for tool in tools if tool.name not in aliases)
        from xdog.coding.config import get_data_dir
        next_mcp = McpManager(settings.mcp_servers, directory,
                              credential_dir=get_data_dir() / "mcp-credentials",
                              credential_backend=settings.mcp_credential_backend)
        if directory != self.working_dir:
            defaults = {tool.name: tool for tool in get_default_tools(directory)}
            tools = tuple(defaults.get(tool.name, tool) for tool in tools)
        try:
            staged = self._stage_navigation(settings, directory, loaded.session_id, selected_model,
                                             loaded.extension_entries, loaded.plan)
            selected_model = staged.model.id if staged.model is not None else selected_model
            loaded.extension_entries = staged.runtime.entries.snapshot()
            if loaded.active_tools is not None:
                available = {tool.name for tool in tools if tool.name not in self.extensions.aliases}
                available.update(tool.name for tool in staged.runtime.tools())
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
        if self.mcp is not None:
            self.mcp.close()
        self.mcp = next_mcp
        self.children.close()
        self.extension_dialogs.close()
        self.extensions.close()
        old_ext = self.extensions.aliases
        tools = tuple(tool for tool in tools if tool.name not in old_ext)
        self.plan.close()
        self.session_data = loaded
        self.working_dir = directory
        self.settings = settings
        self.permissions.clear_session_rules()
        self.agent.clear_all_queues()
        self._active_skills = frozenset()
        self._push_skills()
        self.agent.replace_messages(list(loaded.messages))
        self.agent.set_tools(tools)
        self._load_plan()
        self._load_extensions(staged.runtime)
        self._load_children()
        self.agent.set_tool_context({**self.agent.tool_context, "fs_workspace": str(directory)})
        self._publish_navigation_model(selected_model, staged.model, staged.thinking)
        self.extensions.sync_tools(self.agent)
        self._restore_active_tools()
        self._rebuild_system_prompt()
        return True

    @_idle_change
    def new_session(self) -> str:
        self._children_idle()
        if self.agent.state.is_streaming:
            raise RuntimeError("Session navigation requires an idle agent")
        from xdog.coding.core.sessions.ownership import SessionLease, runtime_directory
        selected = uuid.uuid4().hex
        lease = SessionLease.acquire(runtime_directory(self.session_manager._dir), selected)
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
            "model": self.model, "settings": self.settings.session_to_dict(), "plan": self.plan.state.to_dict(),
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
        restored_plan = PlanState.from_dict(branch.get("plan"))
        validate_entries(branch.get("extension_entries", {}))
        restored = dicts_to_messages(branch["messages"])
        if restored:
            validate_checkpoint(restored, len(restored) - 1)
        target_settings = SettingsManager(global_path=self.settings._global_path, project_dir=self.working_dir)
        target_settings.load_session_settings(branch.get('settings', self.settings.session_to_dict()))
        staged: StagedExtensions = self._stage_navigation(target_settings, self.working_dir, self.session_id,
            selected_model, branch.get('extension_entries', {}), branch.get('plan'))
        if restored_tools is not None:
            available = {tool.name for tool in self.agent.state.tools if tool.name not in self.extensions.aliases}
            available.update(tool.name for tool in staged.runtime.tools())
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
            'settings': self.settings.session_to_dict(), 'plan': self.plan.state.to_dict(),
            'extension_entries': self.extensions.entries.snapshot(), 'compaction': self.session_data.compaction,
            'active_tools': (list(self.agent.active_tool_selection)
                             if self.agent.active_tool_selection is not None else None)}
        committed = replace(self.session_data, messages=restored, model=selected_model,
            settings=target_settings.session_to_dict(), plan=restored_plan.to_dict(),
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
        self.plan.close()
        self.session_data.plan = restored_plan.to_dict()
        self.children.close()
        self.extension_dialogs.close()
        self.extensions.close()
        old_extensions = self.extensions.aliases
        self.agent.set_tools(tuple(tool for tool in self.agent.state.tools if tool.name not in old_extensions))
        self.session_data.compaction = restored_compaction
        self.session_data.extension_entries = committed.extension_entries
        self._load_extensions(staged.runtime)
        self._load_plan()
        self.session_data.active_branch = branch_id
        self.session_data.active_tools = committed.active_tools
        self._load_children()
        self._publish_navigation_model(selected_model, staged.model, staged.thinking)
        self._active_skills = frozenset()
        self._push_skills()
        self.extensions.sync_tools(self.agent)
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
                settings=self.settings.session_to_dict(), plan=self.plan.state.to_dict(),
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
        plan = source.get("plan") if source is not None else loaded.plan
        entries = source.get("extension_entries", {}) if source is not None else loaded.extension_entries
        from xdog.agent.extensions.compaction import saved_record
        compaction = saved_record(source.get("compaction") if source is not None else loaded.compaction)
        PlanState.from_dict(plan)
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
                "model": model, "settings": settings, "plan": plan, "extension_entries": entries,
                "compaction": compaction, "active_tools": list(active_tools) if active_tools is not None else None})
        points = history_points(loaded.messages)
        return_point = points[-1].index if points else -1
        branches.append({"branch_id": uuid.uuid4().hex[:8], "branch_point": return_point,
            "messages": messages_to_dicts(loaded.messages[:return_point + 1]),
            "parent_branch": loaded.active_branch, "created_at": created_at,
            "label": "Return checkpoint before branch navigation", "model": loaded.model,
            "settings": loaded.settings, "plan": loaded.plan,
            "extension_entries": loaded.extension_entries, "compaction": loaded.compaction,
            "active_tools": list(loaded.active_tools) if loaded.active_tools is not None else None})
        return replace(loaded, messages=list(selected), model=model, settings=dict(settings), plan=plan,
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
            self.agent.set_active_skills(sorted(self._active_skills))
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
                thinking_level=str(self.agent.options.thinking or "normal"),
                permission_mode=self.permissions.mode,
                allowed_tools=tuple(t.name for t in self.agent.active_tools),
                custom_instructions=self.settings.custom_instructions,
                extensions=(),
                working_dir=str(self.working_dir),
                platform_info=PlatformInfo.detect(),
            ),
            tool_defs,
            extra_context=_skills_context(self._active_skills),
            native_tool_calls=self._native_tool_calls(),
        )
        self.agent.set_system_prompt(prompt + self.plan.state.prompt())
    def _on_agent_event(self, event: AgentEvent) -> None:
        """Handle agent lifecycle events."""
        if isinstance(event, AgentEndEvent):
            self._persist()

    def _persist(self) -> None:
        """Save session state to disk."""
        if hasattr(self, 'context_messages') and self.context_messages.uncertain:
            return  # Preserve an uncertain atomic history receipt until explicit session reload.
        self.session_data.messages = self.messages
        self.session_data.settings = self.settings.session_to_dict()
        self.session_data.model = self.agent.state.model or ""
        if not self._tool_selection_restore_pending:
            self.session_data.active_tools = self.agent.active_tool_selection

        # Update summary from last assistant message
        msgs = self.messages
        if msgs:
            last = msgs[-1]
            if isinstance(last, AssistantMessage):
                for part in last.content:
                    if isinstance(part, TextContent):
                        self.session_data.summary = part.text[:120]
                        break

        self.session_manager.save_session(self.session_data)

    async def manage_mcp_auth(self, server: str, action: str, receipt: str, lease: str,
                              alive: Callable[[], bool]) -> dict[str, Any]:
        if self.mcp is None:
            raise ValueError("MCP manager unavailable")
        if action in {"status", "cancel"}:
            return await self.mcp.manage_auth(server, action, receipt, lease=lease, alive=alive)
        if self._auth_changing or self.agent.is_streaming:
            raise RuntimeError("Authorization changes require an idle session")
        self._children_idle()
        with self.children.supervisor.idle_scope():
            self._auth_changing = True
            try:
                old = {tool.name for tool in self.mcp.tools()}
                result = await self.mcp.manage_auth(server, action, receipt, lease=lease, alive=alive)
                if action == "forget":
                    self.agent.set_tools((*[tool for tool in self.agent.state.tools if tool.name not in old],
                                          *self.mcp.tools()))
                    await self._persist_tool_retirement()
                return result
            finally:
                self._auth_changing = False


    async def manage_mcp(self, action: str, server: str = "") -> str:
        """Explicit idle-only process trust and tool-set management, not a second loop."""
        if self.mcp is None:
            raise ValueError("MCP manager unavailable")
        if action in {"list", "status"}:
            import json
            return json.dumps(self.mcp.status(), indent=2)
        self._children_idle()
        if self._auth_changing or self.agent.state.is_streaming:
            raise RuntimeError("MCP connection/schema changes require an idle agent")
        if action not in {"connect", "disconnect", "refresh"} or not server:
            raise ValueError("Usage: /mcp [list|connect SERVER|disconnect SERVER|refresh SERVER]")
        with self.children.supervisor.idle_scope():
            old = {tool.name for tool in self.mcp.tools()}
            base = tuple(tool for tool in self.agent.state.tools if tool.name not in old)
            if action == "connect":
                await self.mcp.connect(server)
            elif action == "disconnect":
                await self.mcp.disconnect(server)
            else:
                await self.mcp.refresh(server)
            external = self.mcp.tools()
            if {tool.name for tool in base} & {tool.name for tool in external}:
                await self.mcp.disconnect(server)
                raise ValueError("MCP tool alias collides with a local tool; server was disconnected")
            self.agent.set_tools((*base, *external))
            await self._persist_tool_retirement()
            return f"MCP {server}: {action} completed; external tools remain subject to current permission policy"

    def enable_runtime(self, *, bound: bool) -> SessionRuntime:
        from xdog.coding.core.sessions.runtime import SessionRuntime
        with self._sdk_lock:
            if self.runtime is None:
                if self._sdk_controller is not None or self._sdk_closing is not None and not self._sdk_closing.done():
                    raise RuntimeError('An SDK owner is active/settling; cannot create a second session owner')
                self.runtime = SessionRuntime(self, bound=bound)
            return self.runtime

    async def _aclose_resources(self) -> None:
        await self.children.supervisor.aclose()
        self.extension_dialogs.close()
        await self.extensions.aclose()
        if self.mcp is not None:
            await self.mcp.aclose()

    def dispose(self) -> None:
        """Close the owner before releasing resources and the writer lease."""
        if self.runtime is not None and not self.runtime.disposing:
            self.runtime.close()
            return
        self._sdk_disposing = True
        controller = self._sdk_controller
        closing = controller.close() if controller is not None else self._sdk_closing
        if closing is not None and not closing.done():
            def settled(done: Future[None]) -> None:
                self._release_sdk_leases()
                self._dispose_resources()
            closing.add_done_callback(settled)
            return
        self._release_sdk_leases()
        self._dispose_resources()

    def _dispose_resources(self) -> None:
        self.children.close()
        self.extension_dialogs.close()
        self.extensions.close()
        self.plan.close()
        self.permissions.close()
        if self.mcp is not None:
            self.mcp.close()
        if self._unsubscribe:
            self._unsubscribe()
            self._unsubscribe = None
        if self._lease is not None:
            self._lease.close()
            self._lease = None


def _skills_context(active: frozenset[str] = frozenset()) -> str:
    """The skills section of the system prompt.

    Two levels, following the format's progressive disclosure: every skill
    contributes one line so the model knows it exists, and the ones the user
    has activated contribute their full instructions. Rebuilt from scratch on
    every turn, which is what makes deactivation work — an activated skill
    leaves no trace once its slug is dropped.

    Returns an empty string on any failure. A skills directory that cannot be
    read is not a reason to start without a system prompt.
    """
    try:
        from xdog.coding.core.slash_commands import skill_manager

        manager = skill_manager()
        summary = manager.skills_summary()
        # Bodies are the Agent's to place (`set_skills`); this section carries
        # only the standing note about them. Rendering them here as well was how
        # three packages ended up with three answers to the same question.
        fixed = [slug for slug in sorted(active)
                 if (skill := manager.load_skill(slug)) is not None and not skill.expires_after_turn]
        bodies = ["(active: " + ", ".join(fixed) + ")"] if fixed else []
    except Exception:
        logger.debug("could not build the skills section of the system prompt", exc_info=True)
        return ""

    sections = []
    if summary:
        sections.append(summary + "\nRun one with the /<slug> command, or ask the user to.")
    if bodies:
        sections.append(
            "# Active skills\n\n"
            "The user activated these; follow them.\n\n"
            "You cannot deactivate a skill — there is no tool for it, by design. "
            "Skills are often constraints rather than conveniences, and the party "
            "being constrained should not hold the release. If one looks finished "
            "or irrelevant, say so and suggest `/unload <name>`; the user decides.\n\n"
            + "\n\n".join(bodies)
        )
    return "\n\n".join(sections)
