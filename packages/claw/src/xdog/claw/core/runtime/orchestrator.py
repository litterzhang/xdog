"""Top-level orchestrator — message routing and lifecycle management."""
from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Awaitable, Callable
from dataclasses import replace
from functools import partial
from pathlib import Path
from typing import Any, cast

from xdog.agent.core import CatalogStreamFn, ModelStreamFn
from xdog.agent.extensions.command_result import ExtensionCommandResult
from xdog.agent.mcp.completion import McpCompletion, request_arguments
from xdog.agent.mcp.context import McpDocument, McpEntry
from xdog.ai.types import ImageContent, Model, ThinkingLevel
from xdog.claw.channels.base import Channel
from xdog.claw.config import ClawConfig
from xdog.claw.core.chunker import BlockChunker
from xdog.claw.core.planning.task_scheduler import TaskScheduler
from xdog.claw.core.queue import MessageQueue
from xdog.claw.core.runtime.group import GroupRuntime
from xdog.claw.core.runtime.session import AgentSession, TurnResult
from xdog.claw.core.runtime.transcript_source import SourceCapture
from xdog.claw.core.types import (
    Group,
    GroupInput,
    QueueMode,
    ScheduledTask,
    SystemInput,
    SystemInputKind,
    UserInput,
)

logger = logging.getLogger(__name__)


CommandMenuHost = tuple[Callable[[], bool], int]



class Orchestrator:
    """Coordinates channels, groups, sessions, and agent execution.

    All messages enter through ``route_message()``. Background work
    (scheduled tasks, goal manager) is driven by ``tick()``.
    """

    def __init__(
        self,
        config: ClawConfig,
        *,
        model: str | Model | None = None,
        stream_fn: Any = None,
        data_dir: Path | None = None,
    ) -> None:
        self._config = config
        # SDK callers may supply metadata; downstream sessions and alias routing
        # use model IDs exclusively, including when a group has no override.
        self._model = model.id if isinstance(model, Model) else model or ""
        self._stream_fn = stream_fn
        self._data_dir = Path(data_dir or config.data_dir)
        self._channels: list[Channel] = []
        self._runtimes: dict[str, GroupRuntime] = {}
        self._navigation_groups: set[str] = set()
        self._queue = MessageQueue(max_concurrent=config.max_concurrent_agents)
        tasks_file = Path(config.tasks_file).expanduser()
        if data_dir is not None and tasks_file == Path(config.data_dir) / "scheduled_tasks.json":
            tasks_file = self._data_dir / "scheduled_tasks.json"
        self._scheduler = TaskScheduler(tasks_file)
        self._scheduled_runs: dict[str, asyncio.Task[TurnResult | None]] = {}
        self._chunker = BlockChunker()

    # -- Group registration ----------------------------------------------------

    def register_group(self, group: Group) -> None:
        """Register a group and initialize its runtime."""
        group = replace(
            group,
            enabled_tools=self._config.enabled_tools if group.enabled_tools is None else group.enabled_tools,
            image_model=group.image_model or self._config.image_model,
        )
        runtime = GroupRuntime.create(
            group,
            self._data_dir,
            model=self._model,
            stream_fn=self._stream_fn,
            send_fn=self._send_to_channels, task_scheduler=self._scheduler, mcp_servers=self._config.mcp_servers,
            mcp_credential_backend=self._config.mcp_credential_backend,
            extensions=self._config.extensions, extension_dir=self._config.extension_dir,
            extension_settings=self._config.extension_settings,
            extension_allow_tools=self._config.extension_allow_tools, child_presets=self._config.child_presets,
        )
        # Bind route_fn so the goal system can send SystemInput
        # messages that the agent actually receives and acts on
        runtime.goal_manager._route_fn = self.route_message
        self._runtimes[group.id] = runtime

    def add_channel(self, channel: Channel) -> None:
        """Add a messaging channel."""
        self._channels.append(channel)

        async def _on_channel_message(message: GroupInput) -> None:
            result = await self.route_message(
                message,
                on_assistant_message=partial(self._send_response, message.group_id),
            )
            if result is None:
                return
            group_id = message.group_id
            if result.error:
                await self._send_to_channels(group_id, f"Error: {result.error}")

        channel.set_on_message(_on_channel_message)

    def auto_register_group(self, group_id: str) -> None:
        """Auto-register a group on first message (e.g. from WeChat users)."""
        if group_id in self._runtimes:
            return
        group = Group(id=group_id, name=group_id)
        logger.info("Auto-registering group: %s", group_id)
        self.register_group(group)

    # -- Message routing -------------------------------------------------------

    async def route_message(
        self,
        message: GroupInput,
        *,
        on_display_event: Any = None,
        on_text_delta: Any = None,
        on_assistant_message: Callable[[str], Awaitable[None]] | None = None,
        child_ui: tuple[str, Callable[[dict[str, Any]], None]] | None = None,
        reserved: bool = False,
        input_alive: Callable[[], bool] | None = None,
        command_menu: CommandMenuHost | None = None,
    ) -> TurnResult | None:
        """Single entry point for all messages."""
        group_id = message.group_id
        if group_id not in self._runtimes:
            if group_id.startswith("weixin:"):
                self.auto_register_group(group_id)
            else:
                logger.warning("Message for unknown group: %s", group_id)
                return None

        runtime = self._runtimes[group_id]
        group = runtime.group
        mode = group.config.queue_mode if group.config else QueueMode.COLLECT
        max_queued = group.config.max_queued_messages if group.config else 50
        debounce_ms = group.config.debounce_ms if group.config else 0

        scheduled = isinstance(message, SystemInput) and message.kind == SystemInputKind.SCHEDULER
        # Scheduled inputs must retain their own completion/delivery callbacks while waiting.
        # Never steer or fold them into another turn's fire-and-forget collected inputs.
        if self._queue.is_running(group_id) and not reserved and not scheduled:
            active = runtime._active_session
            if group_id in self._navigation_groups or active is None or not active.agent.state.is_streaming:
                mode = QueueMode.COLLECT  # No ready checkpoint owner: retain, never drop into a disposed session.
            async def _on_steer(msg: GroupInput) -> None:
                runtime.steer(msg.content)

            async def _on_follow_up(msg: GroupInput) -> None:
                runtime.follow_up(msg.content)

            await self._queue.enqueue(
                message, mode=mode, on_steer=_on_steer,
                on_follow_up=_on_follow_up, max_queued=max_queued,
            )
            return None

        result = await self._execute_with_queue(
            message,
            on_display_event=on_display_event,
            on_text_delta=on_text_delta,
            on_assistant_message=on_assistant_message,
            child_ui=child_ui,
            input_alive=input_alive,
            command_menu=command_menu,
        )

        queued = await self._queue.collect_with_debounce(group_id, debounce_ms)
        for qmsg in queued:
            qresult = await self._execute_with_queue(
                qmsg,
                on_assistant_message=partial(self._send_response, group_id),
            )
            if qresult and qresult.error:
                await self._send_to_channels(group_id, f"Error: {qresult.error}")

        return result

    async def _execute_with_queue(
        self,
        message: GroupInput,
        *,
        on_display_event: Any = None,
        on_text_delta: Any = None,
        on_assistant_message: Callable[[str], Awaitable[None]] | None = None,
        child_ui: tuple[str, Callable[[dict[str, Any]], None]] | None = None,
        input_alive: Callable[[], bool] | None = None,
        command_menu: CommandMenuHost | None = None,
    ) -> TurnResult | None:
        """Execute a message with concurrency control."""
        group_id = message.group_id
        runtime = self._runtimes.get(group_id)
        if not runtime:
            return None

        is_user = isinstance(message, UserInput)
        async with self._queue.acquire(group_id, is_user=is_user):
            if isinstance(message, SystemInput) and message.kind == SystemInputKind.SCHEDULER:
                task_id = message.metadata.get("task_id")
                if task_id:
                    current = self._scheduler.get_task(task_id)
                    if current is None or not current.enabled or current.status != "running":
                        return None  # Cancelled while waiting for the queue.
            if input_alive is not None and not input_alive():
                raise ValueError("Reviewed message owner changed before turn admission; no replay")
            session = runtime.get_or_create_session(allow_reset=input_alive is None)
            previous_menu = session.extensions.command_menu_handler
            menu_handler = self._command_menu_handler(session, command_menu) if command_menu is not None else None
            previous_pending = session.extensions.pending_messages
            session.extensions.command_menu_handler = menu_handler
            session.extensions.pending_messages = self._pending_messages_reader(session)
            try:
                result: TurnResult | None = await session.run_turn(
                    message,
                    on_display_event=on_display_event,
                    on_text_delta=on_text_delta,
                    on_assistant_message=on_assistant_message,
                    child_ui=child_ui,
                )
                return result
            finally:
                session.extensions.pending_messages = previous_pending
                if session.extensions.command_menu_handler is menu_handler:
                    session.extensions.command_menu_handler = previous_menu

    def _command_menu_handler(self, session: AgentSession, binding: CommandMenuHost
                              ) -> Callable[[str, dict[str, Any]], Awaitable[Any]]:
        runtime, session_id = session._runtime, session.meta.session_id
        def alive() -> bool:
            return (binding[0]() and runtime._active_session is session
                and runtime.get_new_session_id() == session_id)
        async def read(extension: str, dialog: dict[str, Any]) -> Any:
            from xdog.agent.extensions.command_catalog import invocation_scope
            identity = invocation_scope(dialog.get('command_menu'))
            if identity is None or identity['child_id'] or identity['session_id'] != session_id or not alive():
                return None
            revision = runtime._source_revision
            result = await session.extension_dialogs.ask(extension,
                {**dialog, 'command_menu_generation': binding[1]})
            return result if alive() and runtime._source_revision == revision else None
        return read

    def _pending_messages_reader(self, session: AgentSession) -> Callable[[], Awaitable[bool | None]]:
        loop, runtime, agent = asyncio.get_running_loop(), session._runtime, session.agent
        session_id = session.meta.session_id
        async def read() -> bool | None:
            from xdog.agent.supervision import settle_on
            async def inspect() -> bool | None:
                if (runtime._active_session is not session or session.agent is not agent
                        or runtime.get_new_session_id() != session_id or session.extensions._closed):
                    return None
                return bool(agent.pending_inputs()) or agent.has_queued_messages()
            return await settle_on(loop, inspect())
        return read



    # -- Channel delivery ------------------------------------------------------

    async def _send_to_channels(self, group_id: str, text: str) -> None:
        for channel in self._channels:
            if channel.accepts_group(group_id):
                await channel.send_message(group_id, text)

    async def _send_response(self, group_id: str, text: str, *, require_delivery: bool = False) -> None:
        channels = [channel for channel in self._channels if channel.accepts_group(group_id)]
        if require_delivery and not channels:
            raise RuntimeError(f"No delivery channel for group {group_id}")
        for chunk in self._chunker.chunk(text) or [text]:
            for channel in channels:
                await channel.send_chunk(group_id, chunk)

    # -- Group operations (public API for Gateway) -----------------------------

    def reset_group(self, group_id: str) -> bool:
        """Reset the active session. Returns True on success."""
        runtime = self._runtimes.get(group_id)
        if not runtime:
            return False
        runtime.reset_session()
        return True

    def abort_group(self, group_id: str) -> bool:
        """Abort the active session for a known group."""
        runtime = self._runtimes.get(group_id)
        if not runtime:
            return False
        runtime.abort()
        return True

    async def wait_group_idle(self, group_id: str) -> None:
        runtime = self._runtimes.get(group_id)
        if runtime:
            await runtime.wait_for_idle()

    def is_group_running(self, group_id: str) -> bool:
        return self._queue.is_running(group_id)

    def try_reserve_group(self, group_id: str) -> bool:
        return self._queue.try_reserve(group_id)

    def claim_group_reservation(self, group_id: str) -> None:
        self._queue.claim_reservation(group_id)

    def release_group_reservation(self, group_id: str) -> None:
        self._queue.release_reservation(group_id)

    async def manage_children(self, group_id: str, action: str, identifier: str, text: str, access: str,
                              expected_session: str,
                              child_ui: tuple[str, Callable[[dict[str, Any]], None]] | None = None,
                              config: dict[str, Any] | None = None, *,
                              images: tuple[ImageContent, ...] = (), return_owner: str = '',
                              return_alive: Callable[[], bool] = lambda: True) -> dict[str, Any]:
        if images and action not in {'start', 'steer', 'follow-up'}:
            raise ValueError('Child images belong only to start/steer/follow-up')
        runtime = self._runtimes.get(group_id)
        if (runtime is None or group_id in self._navigation_groups
                or (runtime.get_new_session_id() or "") != expected_session):
            raise ValueError("Child session/group changed; refresh before retrying")
        if action in {"list", "presets"} and not expected_session:
            data: dict[str, Any] = ({"children": []} if action == "list" else
                    {"presets": runtime.child_presets.snapshot()})
            return {"session_id": "", "data": data}
        # Inspection/control must remain usable while the parent is waiting for a
        # child. Only start needs an idle group for prompt/tool snapshot rebuilding.
        if action == "start":
            async with self._queue.acquire(group_id, is_user=True):
                session = runtime.get_or_create_session(allow_reset=False)
                session.children.extensions.default_ui = child_ui
                owner = session.children
                result = await session.manage_children(action, identifier, text, access, config=config, images=images,
                                                       return_owner=return_owner, return_alive=return_alive)
        else:
            session = runtime.get_or_create_session(allow_reset=False)
            owner = session.children
            result = await session.manage_children(action, identifier, text, access, config=config, images=images,
                                                       return_owner=return_owner, return_alive=return_alive)
        current_session = runtime._active_session
        if (current_session is None or current_session is not session or current_session.children is not owner
                or group_id in self._navigation_groups):
            raise ValueError('Child session/branch owner changed after control; inspect, no replay')
        return {"session_id": runtime.get_new_session_id() or "", "data": result}

    def answer_child_dialog(self, group_id: str, session_id: str, child: str, identifier: str,
                            token: str, value: Any) -> bool:
        runtime = self._runtimes.get(group_id)
        if (runtime is None or runtime._active_session is None or group_id in self._navigation_groups
                or (runtime.get_new_session_id() or "") != session_id):
            return False
        return runtime._active_session.children.extensions.answer(child, identifier, token, value)


    def detach_child_ui(self, token: str) -> None:
        for runtime in self._runtimes.values():
            if runtime._active_session is not None:
                runtime._active_session.children.extensions.detach(token)

    async def manage_access(self, group_id: str, action: str, expected_session: str) -> dict[str, Any]:
        from xdog.agent.access import AccessState
        from xdog.agent.extensions.runtime import _owned_disk
        runtime = self._runtimes.get(group_id)
        if runtime is None:
            raise ValueError("Unknown group")
        if (runtime.get_new_session_id() or "") != expected_session:
            raise ValueError("Access session changed; refresh before retrying")
        if action == "status":
            state = (runtime._active_session.access.state if runtime._active_session is not None else
                     AccessState.from_dict(runtime.transcript_store.get_access(expected_session))
                     if expected_session else AccessState())
        elif action in {"restrict", "release"}:
            async with self._queue.acquire(group_id, is_user=True):
                if (runtime.get_new_session_id() or "") != expected_session:
                    raise ValueError("Access session changed; refresh before retrying")
                session = runtime.get_or_create_session(allow_reset=False)
                session._children_idle()
                state = await _owned_disk(lambda: session.access.set_read_only(action == "restrict"))
                runtime._source_revision += 1
        else:
            raise ValueError("Unknown access action")
        return {"session_id": runtime.get_new_session_id() or "", "access": state.to_dict()}

    async def compact_session(self, group_id: str, expected_session: str, instructions: str,
                              alive: Callable[[], bool]) -> dict[str, Any]:
        runtime = self._runtimes.get(group_id)
        if (runtime is None or (runtime.get_new_session_id() or "") != expected_session
                or group_id in self._navigation_groups):
            raise ValueError("Compaction group/session changed; refresh before retrying")
        session = cast(AgentSession, runtime.get_or_create_session(allow_reset=False))
        def owner_alive() -> bool:
            return (alive() and runtime._active_session is session
                    and group_id not in self._navigation_groups)
        return await session.compact(instructions, owner_alive)

    async def skills_control(self, group_id: str, expected_session: str, action: str, slug: str,
                             alive: Callable[[], bool] = lambda: True) -> dict[str, Any]:
        runtime = self._runtimes.get(group_id)
        if (runtime is None or expected_session and runtime.get_new_session_id() != expected_session
                or group_id in self._navigation_groups):
            raise ValueError('Skill group/session owner changed')
        session = cast(AgentSession, runtime.get_or_create_session(allow_reset=False))
        def owner_alive() -> bool:
            return (alive() and runtime._active_session is session
                    and group_id not in self._navigation_groups)
        return await session.skills_control(action, slug, owner_alive)

    async def reasoning(self, group_id: str, expected_session: str, level: str | None = None) -> dict[str, Any]:
        runtime = self._runtimes.get(group_id)
        if runtime is None or (runtime.get_new_session_id() or "") != expected_session:
            raise ValueError("Reasoning session changed; refresh before changing settings")
        session = cast(AgentSession, runtime.get_or_create_session(allow_reset=False))
        # An initially empty owner may explicitly bootstrap its first session.
        owner_session = session.meta.session_id
        await session.initialize_extensions()
        if session.agent.state.is_streaming:
            raise ValueError("Reasoning changes require an idle agent")
        if level is not None:
            session._children_idle()
        model_id = session.agent.state.model
        stream = session.agent.stream_fn
        model = stream.resolve_model(model_id) if isinstance(stream, ModelStreamFn) else None
        levels = ("minimal", "low", "medium", "high", "xhigh")
        available = (model.supported_efforts if model is not None and model.supported_efforts is not None
                     else levels if model is not None and model.reasoning else ())
        available = tuple(item for item in available if item in levels) if model is not None and model.reasoning else ()
        if level is not None:
            if level != "off" and level not in available:
                raise ValueError("Thinking level not advertised; choose a supported level or off")
            task = asyncio.current_task()
            def owner_alive() -> bool:
                return (runtime._active_session is session and runtime.get_new_session_id() == owner_session
                        and session.agent.state.model == model_id and not session.agent.state.is_streaming)
            def alive() -> bool:
                return task is not None and not task.cancelling() and owner_alive()
            committed = [False]
            def persist() -> None:
                runtime.transcript_store.save_reasoning(owner_session, model_id, level, alive)
                committed[0] = True
            def apply() -> None:
                session.agent.set_options(replace(session.agent.options,
                    thinking=None if level == "off" else cast(ThinkingLevel, level)))
                runtime._source_revision += 1
            from xdog.agent.extensions.runtime import _owned_disk
            try:
                await _owned_disk(persist)
            except asyncio.CancelledError:
                # A lost acknowledgement does not roll back an already admitted
                # atomic commit. Keep live/persisted options aligned on this owner;
                # do not replay the write or apply it to another session/model.
                if committed[0] and owner_alive():
                    apply()
                raise
            if not owner_alive():
                raise ValueError("Reasoning owner changed before applying settings")
            apply()
        return {"session_id": owner_session, "model": model_id,
                "thinking": session.agent.options.thinking or "off",
                "levels": ["off", *available],
                "efforts_reported": model is not None and model.supported_efforts is not None}

    async def extension_models(self, group_id: str, expected_session: str, selected: str = "") -> dict[str, Any]:
        runtime = self._runtimes.get(group_id)
        if runtime is None or (runtime.get_new_session_id() or "") != expected_session:
            raise ValueError("Extension model session changed; refresh before selecting")
        session = cast(AgentSession, runtime.get_or_create_session(allow_reset=False))
        # An initially empty owner may explicitly bootstrap its first session.
        owner_session = session.meta.session_id
        await session.initialize_extensions()
        stream = session.agent.stream_fn
        models = stream.models() if isinstance(stream, CatalogStreamFn) else session.extensions.provider_models()
        if len(models) > 576 or len({model.id for model in models}) != len(models):
            raise ValueError("Invalid/duplicate/oversized configured model catalog")
        for model in models:
            if (not model.id or len(model.id.encode()) > 256 or not isinstance(model.name, str)
                    or len(model.name.encode()) > 512 or any(ord(char) < 33 or ord(char) == 127 for char in model.id)):
                raise ValueError("Invalid configured model catalog identity")
        if selected:
            session._children_idle()
            if session.agent.state.is_streaming:
                raise ValueError("Model selection requires an idle session")
            resolved = stream.resolve_model(selected) if isinstance(stream, ModelStreamFn) else None
            if (resolved is None or resolved.model_type != "chat" or not resolved.supports_streaming
                    or resolved.context_window <= 0):
                raise ValueError("Model is not an available streaming chat model on configured adapter; no fallback")
            model = resolved
            selected = model.id  # Persist and route the canonical identity, not a short lookup alias.
            target_thinking = session.agent.options.thinking
            if selected != session.agent.state.model:
                saved = await asyncio.to_thread(runtime.transcript_store.get_reasoning,
                                                session.meta.session_id, selected)
                target_thinking = (None if saved == "off" else cast(ThinkingLevel, saved) if saved is not None
                                   else runtime.agent_config.options.thinking)
            if not model.reasoning:
                target_thinking = None
            if (target_thinking is not None and model.supported_efforts is not None
                    and target_thinking not in model.supported_efforts):
                raise ValueError("Model does not advertise current thinking level; choose off before switching")
            previous_model, owner_task = session.agent.state.model, asyncio.current_task()
            def owner_alive() -> bool:
                return (runtime._active_session is session and runtime.get_new_session_id() == owner_session
                        and session.agent.state.model == previous_model and not session.agent.state.is_streaming)
            def admit() -> bool:
                return owner_task is not None and not owner_task.cancelling() and owner_alive()
            committed = [False]
            def persist() -> None:
                runtime.transcript_store.save_model(session.meta.session_id, selected, admit)
                committed[0] = True
            def apply() -> None:
                session.agent.set_model(selected)
                session.agent.set_options(replace(session.agent.options, thinking=target_thinking))
                session.agent.set_model_capabilities(context_window=model.context_window,
                                                     max_prompt_tokens=model.max_prompt_tokens,
                                                     supports_tool_calls=model.supports_tool_calls)
                runtime.model, runtime.context_window = selected, model.context_window
                runtime.max_prompt_tokens = model.max_prompt_tokens
                runtime._source_revision += 1
            from xdog.agent.extensions.runtime import _owned_disk
            try:
                await _owned_disk(persist)
            except asyncio.CancelledError:
                if committed[0] and owner_alive():
                    apply()  # Lost acknowledgement is not a rollback/replay of the admitted atomic setting.
                raise
            if not owner_alive():
                raise ValueError("Model session changed before applying stored selection")
            apply()
        active_model = (stream.resolve_model(session.agent.state.model) if isinstance(stream, ModelStreamFn) else None)
        context_window = (active_model.context_window if active_model is not None else
                          0 if session.agent.state.model.startswith("ext-") else session.agent.context_window)
        return {"session_id": runtime.get_new_session_id() or "", "model": session.agent.state.model,
                "model_context_window": context_window, "thinking": session.agent.options.thinking or "off",
                "models": [{"id": model.id, "name": model.name, "context_window": model.context_window,
                            "max_prompt_tokens": model.max_prompt_tokens, "max_tokens": model.max_tokens,
                            "reasoning": model.reasoning, "supports_tool_calls": model.supports_tool_calls}
                           for model in models]}

    async def _manage_provider_auth(self, session: AgentSession, arguments: str,
                                    publish: Callable[[dict[str, Any]], None] | None,
                                    host_alive: Callable[[], bool] | None) -> dict[str, Any]:
        import json

        from xdog.agent.extensions.provider_oauth_host import ProviderAuthHost
        if publish is None or host_alive is None or not host_alive():
            raise ValueError('Provider authentication needs an explicit original native viewer')
        await session.initialize_extensions()
        runtime, agent, sid = session._runtime, session.agent, session.meta.session_id
        revision = runtime._source_revision
        def alive() -> bool:
            return (host_alive() and runtime._active_session is session and session.agent is agent
                    and runtime.get_new_session_id() == sid and runtime._source_revision == revision
                    and not agent.is_streaming and not session.children.supervisor.active and not session._compacting
                    and runtime.group.id not in self._navigation_groups)
        async def ask(extension: str, dialog: dict[str, Any]) -> Any:
            if not alive() or not (host.owns(dialog) or host.owns_menu(dialog)):
                raise ValueError('Foreign provider authentication view')
            return await session.extension_dialogs.ask(extension, dialog)
        host = ProviderAuthHost(session.extensions, ask, alive, native_storage=True)
        previous = session.extension_dialogs.publish
        session.extension_dialogs.publish = publish
        try:
            result = await host(arguments)
            if not alive():
                raise ValueError('Provider authentication owner changed; no replay')
            await session.initialize_extensions()
            model = session.extensions.provider_model(session.agent.state.model)
            return {'session_id': sid, 'snapshot': session.extensions.snapshot(), 'output': json.dumps(result),
                    'notices': [], 'model': session.agent.state.model,
                    'model_context_window': model.context_window if model is not None else session.agent.context_window}
        finally:
            host.revoke()
            session.extension_dialogs.publish = previous
            await session._sync_extension_tools()

    async def manage_extensions(self, group_id: str, action: str, name: str, args: str,
                                expected_session: str,
                                on_dialog: Callable[[dict[str, Any]], None] | None = None, *,
                                provider_auth_alive: Callable[[], bool] | None = None,
                                command_menu: CommandMenuHost | None = None,
                                ) -> dict[str, Any]:
        """Execute trusted extension commands with basic dialogs and read-only host queries."""
        runtime = self._runtimes.get(group_id)
        if runtime is None or (runtime.get_new_session_id() or "") != expected_session:
            raise ValueError("Extension session/group changed; refresh before retrying")
        async with self._queue.acquire(group_id, is_user=True):
            session = runtime.get_or_create_session(allow_reset=False)
            session._children_idle()
            if action == "auth":
                return await self._manage_provider_auth(session, args, on_dialog, provider_auth_alive)
            notices: list[str] = []
            owner_session = session.meta.session_id
            original_agent, source_revision = session.agent, runtime._source_revision
            host_loop = asyncio.get_running_loop()
            def owned() -> bool:
                current = runtime._active_session
                return (current is not None and current is session and current.agent is original_agent
                    and (runtime.get_new_session_id() or "") == owner_session
                    and runtime._source_revision == source_revision and group_id not in self._navigation_groups)
            def publish(event: dict[str, Any]) -> None:
                if event.get("type") == "extension_notice" and len(notices) < 8 and owned():
                    notices.append(str(event.get("text", ""))[:4000])
            async def catalog(extension: str, request: dict[str, Any]) -> dict[str, Any] | None:
                from xdog.agent.extensions.catalog import page
                from xdog.agent.extensions.runtime import _owned_disk
                if (action != "command" or not name.lower().startswith("ext-" + extension.lower() + "-")
                        or not owner_session or not owned()):
                    return None
                scope = ("sessions" if request["scope"] == "sessions" else
                         "tree|" + (request["session_id"] or owner_session) + "|" + request["branch_id"])
                rows = await _owned_disk(runtime.navigation_entries, scope)
                result = await _owned_disk(page, rows, request, owner_session)
                return cast(dict[str, Any], result) if owned() else None
            async def host_dialog(extension: str, dialog: dict[str, Any]) -> Any:
                from xdog.agent.extensions.dialogs import validate_sdk_dialog
                from xdog.agent.supervision import settle_on
                validate_sdk_dialog(dialog)  # Retired writes cannot borrow this read-only bridge.
                if not owned() or not name.lower().startswith("ext-" + extension.lower() + "-"):
                    return None
                if dialog.get('kind') == 'session' and dialog.get('operation') == 'active_tools':
                    from xdog.agent.extensions.active_tools import snapshot
                    async def inspect() -> dict[str, Any] | None:
                        return snapshot(session.agent, owner_session) if owned() else None
                    return await settle_on(host_loop, inspect())
                result = await session.extension_dialogs.ask(extension, dialog)
                return result if owned() else None
            previous_publish, previous_dialog_publish = session.extensions.publish, session.extension_dialogs.publish
            previous_dialog = session.extensions.dialog_handler
            previous_catalog = session.extensions.catalog_handler
            previous_menu = session.extensions.command_menu_handler
            previous_pending = session.extensions.pending_messages
            menu_handler = (self._command_menu_handler(session, command_menu)
                            if command_menu is not None and on_dialog is not None else None)
            session.extensions.publish = publish
            session.extension_dialogs.publish = on_dialog or (lambda event: None)
            session.extensions.dialog_handler = host_dialog if on_dialog is not None else None
            session.extensions.dialog_cancel = session.extension_dialogs.cancel_extension
            session.extensions.catalog_handler = catalog if action == "command" and on_dialog is not None else None
            session.extensions.command_menu_handler = menu_handler
            session.extensions.pending_messages = self._pending_messages_reader(session)
            try:
                await session.initialize_extensions()
                if action == "command":
                    output = await session.extensions.command(name, args)
                else:
                    await session.extensions.manage(action, name)
                    output = ""
                await session.initialize_extensions()
                active_model = session.extensions.provider_model(session.agent.state.model)
                context_window = (active_model.context_window if active_model is not None else
                                  0 if session.agent.state.model.startswith("ext-") else session.agent.context_window)
                payload: dict[str, Any] = {"session_id": runtime.get_new_session_id() or "",
                    "snapshot": session.extensions.snapshot(), "output": output, "notices": notices,
                    "model": session.agent.state.model, "model_context_window": context_window}
                if isinstance(output, ExtensionCommandResult):
                    payload["output"] = output.summary
                    payload["command_content"] = output.to_dict()
                return payload
            finally:
                session.extensions.pending_messages = previous_pending
                if session.extensions.command_menu_handler is menu_handler:
                    session.extensions.command_menu_handler = previous_menu
                if session.extensions.catalog_handler is catalog:
                    session.extensions.catalog_handler = previous_catalog
                if session.extensions.dialog_handler is host_dialog or on_dialog is None:
                    session.extensions.dialog_handler = previous_dialog
                if session.extensions.publish is publish:
                    session.extensions.publish = previous_publish
                session.extension_dialogs.publish = previous_dialog_publish
                await session._sync_extension_tools()

    async def complete_extension_argument(self, group_id: str, expected_session: str,
                                          command: str, prefix: str, identity: str,
                                          argument_input: dict[str, Any] | None = None) -> dict[str, Any]:
        runtime = self._runtimes.get(group_id)
        if (runtime is None or runtime.get_new_session_id() != expected_session
                or runtime._active_session is None or runtime._active_session.meta.session_id != expected_session):
            raise ValueError('Argument completion owner changed; no activation')
        async with self._queue.acquire(group_id, is_user=True):
            session = runtime._active_session
            session._children_idle()
            if session.agent.is_streaming:
                raise ValueError('Argument completion requires an idle current owner')
            try:
                result = await session.complete_command(command, prefix, identity, argument_input=argument_input)
                if runtime._active_session is not session or runtime.get_new_session_id() != expected_session:
                    raise ValueError('Argument completion owner changed')
                return {**result, 'snapshot': session.extensions.snapshot()}
            finally:
                await session._sync_extension_tools()

    def extension_failure_snapshot(self, group_id: str, expected_session: str) -> dict[str, Any] | None:
        """Descriptive failure data only; never bootstrap/switch a viewer or activate a worker."""
        runtime = self._runtimes.get(group_id)
        if (runtime is None or not expected_session or runtime.get_new_session_id() != expected_session
                or runtime._active_session is None or runtime._active_session.meta.session_id != expected_session
                or runtime._active_session.extensions._closed):
            return None
        return {'version': 1, 'session_id': expected_session, 'snapshot': runtime._active_session.extensions.snapshot()}

    def inspect_extension_session(self, group_id: str, session_id: str, identifier: str,
                                  operation: str) -> dict[str, Any] | None:
        """Read actual reserved command owner state, not a viewer's stale model/usage cache."""
        runtime = self._runtimes.get(group_id)
        if (runtime is None or (runtime.get_new_session_id() or "") != session_id
                or runtime._active_session is None or group_id in self._navigation_groups):
            return None
        session = runtime._active_session
        if not session.extension_dialogs.contains(identifier) or session.agent.state.is_streaming:
            return None
        if operation == 'name':
            value = session._extension_session_name()
            return {'session_id': session_id, 'name': value['name']} if value is not None else None
        from xdog.agent.core import ModelStreamFn
        from xdog.agent.extensions.inspection import snapshot
        from xdog.ai.types import Model
        stream = session.agent.stream_fn
        try:
            selected = stream.resolve_model(session.agent.state.model) if isinstance(stream, ModelStreamFn) else None
            return snapshot(session.agent, session_id, operation, selected if isinstance(selected, Model) else None,
                            context_window=runtime.context_window, max_prompt_tokens=runtime.max_prompt_tokens)
        except (ValueError, TypeError):
            return None

    def answer_extension_dialog(self, group_id: str, session_id: str, identifier: str, value: Any) -> bool:
        runtime = self._runtimes.get(group_id)
        if runtime is None or (runtime.get_new_session_id() or "") != session_id or runtime._active_session is None:
            return False
        return runtime._active_session.extension_dialogs.resolve(identifier, value)


    async def manage_mcp(self, group_id: str, action: str, server: str) -> tuple[dict[str, Any], ...]:
        runtime = self._runtimes.get(group_id)
        if runtime is None:
            raise ValueError("Unknown group")
        if action in {"list", "status"}:
            return runtime.mcp.status()
        self._navigation_groups.add(group_id)
        try:
            async with self._queue.acquire(group_id, is_user=True):
                return await runtime.manage_mcp(action, server)
        finally:
            self._navigation_groups.discard(group_id)

    async def manage_mcp_auth(self, group_id: str, server: str, action: str, receipt: str,
                              session: str, lease: str, alive: Callable[[], bool]) -> dict[str, Any]:
        runtime = self._runtimes.get(group_id)
        if runtime is None or (runtime.get_new_session_id() or "") != session:
            raise ValueError("Authorization session changed; prepare a fresh view")
        if action in {"status", "cancel"}:
            return await runtime.mcp.manage_auth(server, action, receipt, lease=lease, alive=alive)
        async with self._queue.acquire(group_id, is_user=True):
            if runtime._active_session is not None:
                runtime._active_session._children_idle()
            result = await runtime.mcp.manage_auth(server, action, receipt, lease=lease, alive=alive)
            if action == "forget" and runtime._active_session is not None:
                runtime._active_session.agent.set_tools((*runtime.tools,
                    *runtime._active_session.extensions.tools(), *runtime._active_session.children.supervisor.tools()))
                await runtime._active_session._persist_tool_retirement()
            return result

    async def mcp_context(self, group_id: str, action: str, server: str, name: str,
                          arguments: dict[str, str]) -> tuple[McpEntry, ...] | McpDocument | McpCompletion:
        runtime = self._runtimes.get(group_id)
        if runtime is None:
            raise ValueError("Unknown group")
        if action in {"resources", "templates", "prompts"}:
            kind = {"resources": "resource", "templates": "template", "prompts": "prompt"}[action]
            return await runtime.mcp.catalog(server, kind)
        if action == "read":
            return await runtime.mcp.resource(server, name)
        if action == "prompt":
            return await runtime.mcp.prompt(server, name, arguments)
        if action in {"complete_prompt", "complete_template", "complete_resource"}:
            argument, value, context = request_arguments(arguments)
            return await runtime.mcp.complete(server, action.removeprefix("complete_"), name, argument, value, context)
        raise ValueError("Unknown MCP context action")

    async def navigation_entries(self, group_id: str, scope: str) -> list[dict[str, Any]]:
        runtime = self._runtimes.get(group_id)
        if runtime is None:
            raise ValueError("Unknown group")
        return await asyncio.to_thread(runtime.navigation_entries, scope)

    async def navigate_session(self, group_id: str, action: str, session_id: str, branch_id: str,
                               index: int, expected_session: str) -> None:
        runtime = self._runtimes.get(group_id)
        if runtime is None:
            raise ValueError("Unknown group")
        self._navigation_groups.add(group_id)
        try:
            async with self._queue.acquire(group_id, is_user=True):
                canceled = threading.Event()
                task = asyncio.create_task(asyncio.to_thread(
                    runtime.navigate_session, action, session_id, branch_id, index, expected_session,
                    lambda: not canceled.is_set(), asyncio.get_running_loop(),
                ))
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    canceled.set()
                    # Cancel unadmitted stages but settle admitted disk/publication
                    # work before releasing group ownership, through repeated cancel.
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
        finally:
            self._navigation_groups.discard(group_id)

    async def read_session_name(self, group_id: str, expected_session: str) -> dict[str, str]:
        runtime = self._runtimes.get(group_id)
        if runtime is None:
            raise ValueError('Unknown group')
        # Metadata reads do not reserve the group, instantiate a session, read
        # history, or block the gateway loop waiting for a disk owner's lock.
        async with asyncio.timeout(10):
            while True:
                if group_id not in self._navigation_groups:
                    value = runtime.transcript_store.read_session_name(group_id, expected_session)
                    if value is not None:
                        return value
                await asyncio.sleep(0.005)

    async def rename_session(self, group_id: str, expected_session: str, name: str, *,
                             expected_name: str | None = None, alive: Callable[[], bool] | None = None) -> str:
        runtime = self._runtimes.get(group_id)
        if runtime is None:
            raise ValueError('Unknown group')
        self._navigation_groups.add(group_id)
        try:
            async with self._queue.acquire(group_id, is_user=True):
                canceled = threading.Event()
                task = asyncio.create_task(asyncio.to_thread(
                    runtime.rename_session, expected_session, name,
                    lambda: not canceled.is_set() and (alive is None or alive()),
                    **({'expected_name': expected_name} if expected_name is not None else {})))
                # Once disk admission happens, settle publication before releasing group ownership.
                while not task.done():
                    try:
                        await asyncio.shield(task)
                    except asyncio.CancelledError:
                        canceled.set()
                return task.result()
        finally:
            self._navigation_groups.discard(group_id)

    def control_input(self, group_id: str, action: str, input_id: str, content: str = "",
                      delivery: str = "", direction: int = 0, images: tuple[ImageContent, ...] = (),
                      *, images_validated: bool = False) -> bool:
        runtime = self._runtimes.get(group_id)
        if runtime is None or not self.is_group_running(group_id):
            return False
        return runtime.control_input(action, input_id, content, delivery, direction, images,
                                     images_validated=images_validated)

    def validate_images(self, group_id: str, images: tuple[ImageContent, ...]) -> None:
        runtime = self._runtimes.get(group_id)
        if runtime is None:
            raise ValueError("Unknown group")
        runtime.validate_images(images)

    def pending_inputs(self, group_id: str, prefix: str) -> tuple[Any, ...]:
        runtime = self._runtimes.get(group_id)
        return runtime.pending_inputs(prefix) if runtime is not None else ()

    def drain_inputs(self, group_id: str, prefix: str) -> tuple[Any, ...]:
        runtime = self._runtimes.get(group_id)
        return runtime.drain_inputs(prefix) if runtime is not None else ()

    def get_group_ids(self) -> list[str]:
        return list(self._runtimes.keys())

    def get_session_info(self, group_id: str) -> dict[str, Any]:
        """Session metadata + history for a group (delegates to runtime)."""
        runtime = self._runtimes.get(group_id)
        if not runtime:
            return {}
        return runtime.get_session_info()

    def capture_source(self, group_id: str, expected_session: str) -> SourceCapture:
        runtime = self._runtimes.get(group_id)
        if runtime is None:
            raise ValueError("Unknown group")
        if group_id in self._navigation_groups:
            raise RuntimeError("Session context is changing; refresh after navigation")
        return runtime.capture_source(expected_session)

    def capture_saved_source(self, group_id: str, expected_session: str, target_session: str,
                             branch_id: str) -> SourceCapture:
        runtime = self._runtimes.get(group_id)
        if runtime is None or group_id in self._navigation_groups:
            raise ValueError('Saved source group/context unavailable')
        return runtime.capture_saved_source(expected_session, target_session, branch_id)

    def source_lease(self, group_id: str) -> tuple[str, int]:
        runtime = self._runtimes.get(group_id)
        if runtime is None or group_id in self._navigation_groups:
            return "", -1
        return runtime.get_new_session_id() or "", runtime._source_revision

    def get_new_session_id(self, group_id: str) -> str | None:
        """Current session ID for a group (for reset acknowledgment)."""
        runtime = self._runtimes.get(group_id)
        if not runtime:
            return None
        return runtime.get_new_session_id()

    def pop_goal_notifications(self, group_id: str) -> list[Any]:
        """Pop pending goal notifications for a group."""
        runtime = self._runtimes.get(group_id)
        if not runtime:
            return []
        return runtime.pop_goal_notifications()

    @property
    def scheduler(self) -> TaskScheduler:
        return self._scheduler

    # -- Background tick -------------------------------------------------------

    async def tick(self) -> None:
        """Run one scheduler + goal manager cycle."""
        for task in self._scheduler.get_due_tasks():
            if task.id not in self._scheduled_runs:
                run = asyncio.create_task(self.run_scheduled_task(task), name=f"scheduled:{task.id}")
                self._scheduled_runs[task.id] = run
                run.add_done_callback(partial(self._scheduled_done, task.id))

        # Goal manager tick — process queued verifications and re-plans
        for group_id in self.get_group_ids():
            runtime = self._runtimes.get(group_id)
            if runtime and (runtime.goal_manager.has_active_goals()
                            or runtime.goal_manager.has_pending_work()):
                try:
                    await runtime.goal_manager.tick(
                        tools=runtime.tools,
                        tool_ctx={
                            "group_id": group_id,
                            "workspace_dir": str(runtime.workspace_dir) if runtime.workspace_dir else "",
                            "data_dir": str(runtime.data_dir),
                            "_task_scheduler": self._scheduler,
                        },
                    )
                except Exception:
                    logger.exception("Goal manager tick failed for group %s", group_id)

    def _scheduled_done(self, task_id: str, run: asyncio.Task[TurnResult | None]) -> None:
        self._scheduled_runs.pop(task_id, None)
        if not run.cancelled() and run.exception() is not None:
            logger.error("Scheduled task %s failed: %s", task_id, run.exception())

    async def run_scheduled_task(self, task: ScheduledTask) -> TurnResult | None:
        """Run through the normal queue; success requires both execution and delivery.

        Failed one-offs are terminal, not retried automatically (tools may have had
        side effects). Recurring tasks advance to a future occurrence after an attempt.
        """
        admitted = self._scheduler.start_run(task.id)
        if admitted is None:
            return None
        delivered = False
        delivery_error: str | None = None

        async def report(text: str) -> None:
            nonlocal delivered, delivery_error
            try:
                await self._send_response(task.group_id, text, require_delivery=True)
                delivered = True
            except Exception as exc:
                # Keep draining the agent even if delivery fails; don't orphan its producer.
                delivery_error = str(exc) or type(exc).__name__
                logger.exception("Scheduled task %s report delivery failed", task.id)

        try:
            message = SystemInput(group_id=task.group_id, content=task.prompt,
                                  kind=SystemInputKind.SCHEDULER, metadata={"task_id": task.id})
            result = await self.route_message(message, on_assistant_message=report)
            if result is None and self._scheduler.get_task(task.id) is None:
                return None  # A cancelled queued task has no execution outcome to report.
            error = result.error if result is not None else "Task not executed (unknown group or cancelled)"
            if error:
                await report(f"Scheduled task {task.id} failed: {error}")
            error = error or delivery_error or (None if delivered else "Agent produced no report")
            self._scheduler.finish_run(task.id, error=error, delivered=delivered and delivery_error is None)
            if error:
                logger.error("Scheduled task %s failed: %s", task.id, error)
                return replace(result, error=error) if result is not None else TurnResult(error=error)
            return result
        except asyncio.CancelledError:
            self._scheduler.finish_run(task.id, error="Scheduled execution cancelled; side effects may have occurred",
                                       delivered=delivered and delivery_error is None)
            raise
        except Exception as exc:
            self._scheduler.finish_run(task.id, error=str(exc), delivered=delivered and delivery_error is None)
            logger.exception("Scheduled task %s failed", task.id)
            return TurnResult(error=str(exc))

    # -- Lifecycle -------------------------------------------------------------

    async def start(self) -> None:
        for channel in self._channels:
            await channel.connect()

    async def stop(self) -> None:
        scheduled = list(self._scheduled_runs.values())
        for run in scheduled:
            run.cancel()
        await asyncio.gather(*scheduled, return_exceptions=True)
        # Stop ingress and settle channel-owned turns before closing their resources.
        disconnected = await asyncio.gather(
            *(channel.disconnect() for channel in self._channels), return_exceptions=True,
        )
        children = [runtime._active_session.children.supervisor for runtime in self._runtimes.values()
                    if runtime._active_session is not None]
        await asyncio.gather(*(owner.aclose() for owner in children), return_exceptions=True)
        owners = [owner for runtime in self._runtimes.values() for owner in runtime._retired_extensions]
        owners.extend(runtime._active_session.extensions for runtime in self._runtimes.values()
                      if runtime._active_session is not None)
        await asyncio.gather(*(owner.aclose() for owner in owners), return_exceptions=True)
        mcp_closed = await asyncio.gather(
            *(runtime.mcp.aclose() for runtime in self._runtimes.values()), return_exceptions=True,
        )
        memory_closed = await asyncio.gather(
            *(runtime.memory.close() for runtime in self._runtimes.values() if runtime.memory is not None),
            return_exceptions=True,
        )
        for result in (*disconnected, *mcp_closed, *memory_closed):
            if isinstance(result, BaseException):
                raise result
