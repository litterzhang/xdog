"""Top-level orchestrator — message routing and lifecycle management."""
from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from collections.abc import Awaitable, Callable, Coroutine
from contextlib import AbstractAsyncContextManager, nullcontext
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, TypeVar, cast

from xdog.agent.async_work import run_owned, wait_owned
from xdog.agent.capabilities import CapabilitySet
from xdog.agent.core import CatalogStreamFn, ModelStreamFn
from xdog.agent.extensions.command_result import ExtensionCommandResult
from xdog.agent.mcp.completion import McpCompletion, request_arguments
from xdog.agent.mcp.context import McpDocument, McpEntry
from xdog.agent.session_input import SessionInput
from xdog.ai.types import ImageContent, Model, ThinkingLevel
from xdog.claw.channels.base import Channel
from xdog.claw.config import ClawConfig
from xdog.claw.core.chunker import BlockChunker
from xdog.claw.core.inputs.consumer import Admit, InputOutcome, InputService
from xdog.claw.core.inputs.models import InputEnvelope, InputScope, ReplyTarget
from xdog.claw.core.inputs.store import InputRecord, InputStore
from xdog.claw.core.outputs.delivery import OutputDelivery
from xdog.claw.core.outputs.store import OutputStore
from xdog.claw.core.outputs.targets import ReplyReference
from xdog.claw.core.runtime.channel_adapter import ChannelReplies, ChannelReply, durable_reply
from xdog.claw.core.runtime.group import GroupRuntime
from xdog.claw.core.runtime.group_controller import GroupController, GroupReservation, InputExecution
from xdog.claw.core.runtime.input_adapter import freeze_input, restore_input
from xdog.claw.core.runtime.session import AgentSession, TurnResult
from xdog.claw.core.runtime.transcript_source import SourceCapture
from xdog.claw.core.types import (
    ExternalInput,
    Group,
    GroupInput,
    UserInput,
)

logger = logging.getLogger(__name__)


T = TypeVar('T')
CommandMenuHost = tuple[Callable[[], bool], int]



@dataclass(frozen=True)
class _InputCall:
    envelope: InputEnvelope
    options: dict[str, Any]
    result: asyncio.Future[TurnResult]
    detached: asyncio.Event


class Orchestrator:
    """Coordinates channels, groups, sessions, and agent execution.

    All messages enter through ``route_message()``. Background work
    (callbacks and feature notices) retains the same execution owner.
    """

    def __init__(
        self,
        config: ClawConfig,
        *,
        model: str | Model | None = None,
        stream_fn: Any = None,
        data_dir: Path | None = None,
        defer_inputs: bool = False,
    ) -> None:
        self._config = config
        # Application startup barrier, independent of installed capabilities.
        self._defer_inputs = defer_inputs
        # Application bootstrap/tests may supply metadata; sessions and alias routing
        # use model IDs exclusively, including when a group has no override.
        self._model = model.id if isinstance(model, Model) else model or ""
        self._stream_fn = stream_fn
        self._data_dir = Path(data_dir or config.data_dir)
        self._channels: list[Channel] = []
        self._channel_replies = ChannelReplies()
        self._outputs: OutputDelivery | None = None
        self._output_resume: asyncio.Task[None] | None = None
        self._output_recovery_requested = False
        self._runtimes: dict[str, GroupRuntime] = {}
        self._group_start_lock = asyncio.Lock()
        self._group_initializers: set[asyncio.Task[None]] = set()
        self._stop_task: asyncio.Task[None] | None = None
        self._navigation_groups: set[str] = set()
        self._background_slots = asyncio.Semaphore(config.max_concurrent_agents)
        self._controllers: dict[str, GroupController] = {}
        self._acp_endpoints: dict[object, tuple[Path, Callable[[], bool]]] = {}
        self.application_capabilities = CapabilitySet("application")
        self._chunker = BlockChunker()
        self._inputs: InputService | None = None
        self._input_lock = asyncio.Lock()
        self._input_start_task: asyncio.Task[InputService] | None = None
        self._input_calls: dict[tuple[InputScope, str], _InputCall] = {}
        self._inputs_stopping = False
        self._input_listeners: set[Callable[[InputScope], None]] = set()

    # -- Group registration ----------------------------------------------------

    def _build_group(self, group: Group) -> GroupRuntime:
        """Acquire still-unpublished resources; never run an Agent or mutate the registry."""
        group = replace(
            group,
            enabled_tools=self._config.enabled_tools if group.enabled_tools is None else group.enabled_tools,
            image_model=group.image_model or self._config.image_model,
        )
        async def delivery(reference: str) -> dict[str, Any]:
            workspace = self.get_group_workspace(group.id)
            if workspace is None:
                raise ValueError('Original output group is unavailable')
            outputs = await self.output_service()
            return await outputs.store.delivery_for_reference(InputScope(group.id, workspace), reference)
        runtime = GroupRuntime.create(
            group,
            self._data_dir,
            model=self._model,
            stream_fn=self._stream_fn,
            message_delivery=delivery, application_capabilities=self.application_capabilities,
            mcp_servers=self._config.mcp_servers,
            mcp_credential_backend=self._config.mcp_credential_backend,
            extensions=self._config.extensions, extension_dir=self._config.extension_dir,
            extension_settings=self._config.extension_settings,
            child_presets=self._config.child_presets,
        )
        runtime.input_address = lambda: self.input_address(group.id, runtime.workspace_dir)
        return runtime

    def register_group(self, group: Group) -> None:
        """Explicit synchronous composition (bootstrap has not yet published this app)."""
        self._runtimes[group.id] = self._build_group(group)

    async def _ensure_dynamic_group(self, group_id: str) -> None:
        if (not group_id.startswith('weixin:') or not group_id.strip() or len(group_id.encode()) > 256
                or any(value in group_id for value in ('/', '\\', '\x00'))):
            raise ValueError('Invalid dynamic group identity; no path-derived scope is allowed')
        cancelled = asyncio.Event()

        async def initialize() -> None:
            async with self._group_start_lock:
                if group_id in self._runtimes:
                    return
                if self._inputs_stopping or cancelled.is_set():
                    return
                runtime = await run_owned(self._build_group, Group(id=group_id, name=group_id))
                if self._inputs_stopping or cancelled.is_set():
                    # Resource factory has no active session execution owner yet.
                    closures = [runtime.mcp.aclose()]
                    if runtime.memory is not None:
                        closures.append(runtime.memory.close())
                    results = await asyncio.gather(*closures, return_exceptions=True)
                    for result in results:
                        if isinstance(result, BaseException):
                            raise result
                    return
                self._runtimes[group_id] = runtime  # Actual owner loop publishes exactly once.

        task = asyncio.create_task(initialize(), name='claw-group-start')
        self._group_initializers.add(task)
        task.add_done_callback(self._group_initializers.discard)
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled.set()
            await wait_owned(task)
            raise
        if group_id not in self._runtimes:
            raise RuntimeError('Group initialization stopped before publication')

    def add_channel(self, channel: Channel) -> None:
        """Add a messaging channel."""
        self._channels.append(channel)
        self._channel_replies.register(channel)

        async def accept(message: GroupInput) -> None:
            await self.route_message(message, message_id=self._channel_message_id(message),
                wait_for_result=False,
                on_assistant_message=self._channel_replies.reply(channel, message))

        async def dispatch(message: GroupInput) -> None:
            await self.route_message(message, message_id=self._channel_message_id(message),
                reserved=self._channel_message_id(message) is not None,
                on_assistant_message=self._channel_replies.reply(channel, message))

        channel.set_on_accept(accept)
        channel.set_on_message(dispatch)

    @staticmethod
    def _channel_message_id(message: GroupInput) -> str | None:
        identifier = message.metadata.get('channel_message_id')
        return identifier if isinstance(identifier, str) and identifier else None

    def auto_register_group(self, group_id: str) -> None:
        """Auto-register a group on first message (e.g. from WeChat users)."""
        if group_id in self._runtimes:
            return
        group = Group(id=group_id, name=group_id)
        logger.info("Auto-registering group: %s", group_id)
        self.register_group(group)

    # -- Message routing -------------------------------------------------------

    async def input_service(self) -> InputService:
        """One owned initialization; a detached caller cannot publish half a service."""
        async with self._input_lock:
            if self._inputs_stopping:
                raise RuntimeError('Claw application inputs are stopping')
            if self._input_start_task is None:
                self._input_start_task = asyncio.create_task(self._initialize_inputs(), name='claw-input-start')
            opening = self._input_start_task
        return await wait_owned(opening)

    async def output_service(self) -> OutputDelivery:
        await self.input_service()
        if self._outputs is None:
            raise RuntimeError('Application output receipts are unavailable')
        return self._outputs

    async def _initialize_inputs(self) -> InputService:
        store = await InputStore.open(self._data_dir / 'inputs' / 'inbox.sqlite3')
        service = InputService(store, self._execute_input, on_settled=self._input_settled,
                               paused=self._defer_inputs, execution_scope=self._input_execution_scope,
                               on_admitted=self._input_admitted, snapshot=self._execution_snapshot,
                               acp_target=self._capability_acp_target)
        service.subscribe_completed(self._retire_withdrawn_call)
        outputs: OutputDelivery | None = None
        try:
            output_store = await OutputStore.open(self._data_dir / 'outputs' / 'journal.sqlite3')
            outputs = OutputDelivery(output_store, self._channel_replies.resolve)
            scopes = [InputScope(group, workspace) for group in tuple(self._runtimes)
                      if (workspace := self.get_group_workspace(group)) is not None]
            for scope in scopes:
                await service.activate(scope, wake=False)
            if self._inputs_stopping:
                raise RuntimeError('Application stopped during input initialization')
            self._outputs = outputs
            self._inputs = service
            # Transport recovery is owned and independent of input acceptance.
            self.channels_ready()
            for scope in scopes:
                service.wake(scope)
            return service
        except BaseException:
            await service.close()
            if outputs is not None:
                await outputs.close()
            raise

    def _retire_withdrawn_call(self, record: InputRecord) -> None:
        if record.status != 'cancelled':
            return
        key = record.envelope.scope, record.envelope.message.id
        call = self._input_calls.get(key)
        if call is None or call.envelope != record.envelope:
            return
        # Cancelled unstarted input cannot acquire a new retry attempt. This
        # retires only its current matching live display binding, never the
        # controller/producer or another input's active execution.
        if self._input_calls.get(key) is call:
            self._input_calls.pop(key, None)
        if not call.result.done():
            call.result.set_result(TurnResult(error='aborted', session_id=record.session_id, canceled=True))

    def channels_ready(self) -> None:
        """Advisory transport readiness; coalesced and owned, never awaits a sender.

        Channels registered after application startup must announce their actual
        successful connection. Original output routes and send receipts remain
        authoritative; missing peers and outcome-unknown are never retargeted.
        """
        if self._inputs_stopping or self._outputs is None:
            return
        self._output_recovery_requested = True
        if self._output_resume is not None and not self._output_resume.done():
            return
        outputs = self._outputs

        async def recover() -> None:
            while self._output_recovery_requested and not self._inputs_stopping:
                self._output_recovery_requested = False
                await outputs.resume()

        self._output_resume = asyncio.create_task(recover(), name='claw-output-resume')
        self._output_resume.add_done_callback(self._output_recovered)

    @staticmethod
    def _output_recovered(task: asyncio.Task[None]) -> None:
        if not task.cancelled() and task.exception() is not None:
            logger.error('Application output recovery failed; inspect durable receipts', exc_info=task.exception())

    def request_stop(self) -> None:
        """Fence intake and signal owned execution before transport cleanup waits."""
        self._inputs_stopping = True
        if self._inputs is not None:
            self._inputs.request_stop()
        elif self._input_start_task is not None and not self._input_start_task.done():
            self._input_start_task.cancel()
        for runtime in self._runtimes.values():
            if runtime._active_session is not None:
                runtime._active_session.children.runtime.abort()

    @property
    def data_dir(self) -> Path:
        return self._data_dir

    def subscribe_inputs(self, callback: Callable[[InputScope], None]) -> Callable[[], None]:
        self._input_listeners.add(callback)
        return lambda: self._input_listeners.discard(callback)

    def _input_settled(self, scope: InputScope) -> None:
        for callback in tuple(self._input_listeners):
            try:
                callback(scope)
            except Exception:
                logger.exception('Optional application input listener failed')

    async def accept_external(self, scope: InputScope, message: SessionInput) -> tuple[InputRecord, bool]:
        if self.get_group_workspace(scope.group_id) != scope.workspace:
            raise ValueError('External input target/workspace changed')
        envelope = freeze_input(ExternalInput(group_id=scope.group_id, content='', sender='acp', channel='',
            metadata={'input_origin': 'acp'}, canonical=message), scope.workspace, message_id=message.id)
        service = await self.input_service()
        existing = await service.store.get(scope, message.id)
        if existing is not None:
            if (existing.envelope.message != envelope.message or existing.envelope.scope != envelope.scope
                    or existing.envelope.images != envelope.images or existing.envelope.human_authorized
                    or existing.envelope.context.get('kind') != 'external'):
                raise ValueError("Accepted external input content conflict")
            # ACK loss/restart cannot reselect a destination from a later live source.
            envelope = existing.envelope
        maximum = self._runtimes[scope.group_id].group.config.max_queued_messages
        return await service.submit(envelope, max_pending=maximum)

    async def _execute_input(self, record: InputRecord, admit: Admit) -> InputOutcome:
        scope, identifier = record.envelope.scope, record.envelope.message.id
        key = scope, identifier
        call = self._input_calls.get(key)
        if call is not None and call.envelope != record.envelope:
            call = None  # An unaccepted conflicting submission cannot borrow a stored input.
        if self.get_group_workspace(scope.group_id) != scope.workspace:
            raise ValueError('Input workspace binding changed; no automatic reroute')
        if record.envelope.admission_key and call is None:
            raise ValueError('Original live admission binding unavailable; inspect before retry')
        options = dict(call.options) if call is not None else {}
        options.pop('on_execution_started', None)
        reference = options.pop('reply_reference', None)
        on_error: Callable[[str], Awaitable[None]] | None = None
        display = options.get('on_display_event')
        if call is not None and display is not None:
            def publish(event: Any) -> None:
                if not call.detached.is_set():
                    display(event)
            options['on_display_event'] = publish
        try:
            value = restore_input(record.envelope)
            reply = options.get('on_assistant_message')
            recovered = (call is None and isinstance(value, UserInput) and not isinstance(value, ExternalInput)
                         and record.envelope.reply is not None)
            if reference is None and isinstance(value, UserInput) and not isinstance(value, ExternalInput):
                if isinstance(reply, ChannelReply):
                    reference = self.capture_reply(reply.channel, restore_input(record.envelope))
                elif recovered:
                    original = restore_input(record.envelope)
                    if not isinstance(original, UserInput):
                        raise ValueError('Saved reply input kind changed')
                    reference = ReplyReference(record.envelope.scope, 'saved-original-channel', original)
            if reference is not None and reference.scope != record.envelope.scope:
                raise ValueError('Reply reference no longer matches the input workspace')
            options['reply_reference'] = reference
            if isinstance(reply, ChannelReply) or recovered:
                if self._outputs is None:
                    raise RuntimeError('Application output receipts were not initialized')
                binding = reply.binding if isinstance(reply, ChannelReply) else 'saved-original-channel'
                send = durable_reply(self._outputs, record, binding)
                options['on_assistant_message'] = send
                async def send_error(error: str) -> None:
                    await send(f'Error: {error}')
                on_error = send_error
            result = await self._execute_owned_input(value, admit=admit, message_record=record, **options)
            if result is None:
                # A host fence explicitly refused this unstarted submission.
                result = TurnResult(error='Host input admission was withdrawn')
            if result.error and on_error is not None:
                await on_error(result.error)
            if call is not None and not call.result.done():
                call.result.set_result(result)
            return InputOutcome(response=result.response_text, error=result.error or '', session_id=result.session_id)
        finally:
            if self._input_calls.get(key) is call:
                self._input_calls.pop(key, None)

    async def route_message(
        self, message: GroupInput, *, on_display_event: Any = None, on_text_delta: Any = None,
        on_assistant_message: Callable[[str], Awaitable[None]] | None = None,
        child_ui: tuple[str, Callable[[dict[str, Any]], None]] | None = None,
        reserved: bool = False, input_alive: Callable[[], bool] | None = None,
        before_admission: Callable[[], Awaitable[bool]] | None = None,
        command_menu: CommandMenuHost | None = None, message_id: str | None = None,
        on_accepted: Callable[[], None] | None = None, reply_target: ReplyTarget | None = None,
        wait_for_result: bool = True,
        on_execution_started: Callable[[InputExecution], None] | None = None,
        reply_reference: ReplyReference | None = None,
    ) -> TurnResult | None:
        """Persist ordinary input before ACK; busy never implicitly means steering."""
        group_id = message.group_id
        if group_id not in self._runtimes:
            if group_id.startswith('weixin:'):
                await self._ensure_dynamic_group(group_id)
            else:
                if not wait_for_result:
                    raise ValueError(f'Cannot accept input for unknown group: {group_id}')
                logger.warning('Message for unknown group: %s', group_id)
                return None
        workspace = self.get_group_workspace(group_id)
        if workspace is None:
            raise ValueError('Group input workspace is unavailable')
        busy = self.is_group_running(group_id)
        identifier = message_id or (message.canonical.id if isinstance(message, ExternalInput)
                                    else 'input-' + uuid.uuid4().hex)
        binding = 'live-' + uuid.uuid4().hex if input_alive is not None or before_admission is not None else ''
        envelope = freeze_input(message, workspace, message_id=identifier, admission_key=binding, reply=reply_target)
        service = await self.input_service()
        key = envelope.scope, identifier
        call = self._input_calls.get(key)
        added = call is None
        if call is None:
            call = _InputCall(envelope, {'on_display_event': on_display_event, 'on_text_delta': on_text_delta,
                'on_assistant_message': on_assistant_message, 'child_ui': child_ui, 'input_alive': input_alive,
                'before_admission': before_admission, 'command_menu': command_menu,
                'on_execution_started': on_execution_started, 'reply_reference': reply_reference},
                asyncio.get_running_loop().create_future(), asyncio.Event())
            self._input_calls[key] = call
        try:
            record, _ = await service.submit(
                envelope, max_pending=self._runtimes[group_id].group.config.max_queued_messages)
            if on_accepted is not None:
                on_accepted()
            if not wait_for_result:
                if record.status in {'succeeded', 'failed', 'cancelled', 'interrupted'} and added:
                    if self._input_calls.get(key) is call:
                        self._input_calls.pop(key, None)
                return None
            if busy and not reserved:
                return None
            receipt = await service.wait(envelope.scope, identifier)
            if receipt.status in {'cancelled', 'interrupted'}:
                result = TurnResult(error='aborted' if receipt.status == 'cancelled' else receipt.error,
                                    session_id=receipt.session_id, canceled=True)
                if not call.result.done():
                    call.result.set_result(result)
                if self._input_calls.get(key) is call:
                    self._input_calls.pop(key, None)
                return result
            if call.result.done():
                return replace(call.result.result(), response_text=receipt.response, error=receipt.error or None)
            result = TurnResult(response_text=receipt.response, error=receipt.error or None,
                                session_id=receipt.session_id, canceled=receipt.status in {'cancelled', 'interrupted'})
            call.result.set_result(result)
            if self._input_calls.get(key) is call:
                self._input_calls.pop(key, None)
            return result
        except asyncio.CancelledError:
            call.detached.set()  # Accepted input survives a lost waiter/connection.
            raise
        except Exception:
            if added and self._input_calls.get(key) is call:
                self._input_calls.pop(key, None)
            raise

    async def _execute_owned_input(
        self,
        message: GroupInput,
        *,
        on_display_event: Any = None,
        on_text_delta: Any = None,
        on_assistant_message: Callable[[str], Awaitable[None]] | None = None,
        child_ui: tuple[str, Callable[[dict[str, Any]], None]] | None = None,
        input_alive: Callable[[], bool] | None = None,
        before_admission: Callable[[], Awaitable[bool]] | None = None,
        command_menu: CommandMenuHost | None = None,
        admit: Admit | None = None, reply_reference: ReplyReference | None = None,
        message_record: InputRecord | None = None,
    ) -> TurnResult | None:
        """Execute a message with concurrency control."""
        group_id = message.group_id
        runtime = self._runtimes.get(group_id)
        if not runtime:
            return None

        is_user = isinstance(message, UserInput) and not isinstance(message, ExternalInput)
        async with self.group_controller(group_id).acquire(is_user=is_user):
            if input_alive is not None and not input_alive():
                raise ValueError("Reviewed message owner changed before turn admission; no replay")
            if before_admission is not None and not await before_admission():
                return None  # Host cancellation won before actual Agent admission.
            if runtime._active_session is not None:
                runtime._active_session.require_history_ready()  # Do not auto-reset around an uncertain executed tail.
            session = await runtime.prepare_session(allow_reset=input_alive is None)
            session.require_history_ready()
            if admit is not None and not await admit(session.meta.session_id):
                return None
            await runtime.initialize_capabilities(await self.input_service())
            from xdog.claw.core.runtime.outbound_messages import OutboundMessages
            actual_agent = session.agent
            message_tool = next((tool for tool in runtime.tools if tool.name == 'message'), None)
            def message_valid() -> bool:
                return (not self._inputs_stopping and not runtime._closing
                        and runtime._active_session is session and session.agent is actual_agent
                        and message_record is not None
                        and runtime.workspace_dir == message_record.envelope.scope.workspace
                        and runtime.group.id == message_record.envelope.scope.group_id
                        and not session.access.state.read_only
                        and message_tool is not None
                        and any(tool is message_tool for tool in actual_agent.active_tools))
            message_port = (OutboundMessages(message_record, self._channel_replies, self._outputs, message_valid)
                            if message_record is not None and self._outputs is not None else None)
            session.agent.set_tool_context({**session.agent.tool_context,
                                           '_reply_reference': reply_reference, '_message_port': message_port})
            previous_menu = session.extensions.command_menu_handler
            menu_handler = self._command_menu_handler(session, command_menu) if command_menu is not None else None
            previous_pending = session.extensions.pending_messages
            session.extensions.command_menu_handler = menu_handler
            session.extensions.pending_messages = self._pending_messages_reader(session)
            try:
                # Channel commands use the same admitted Core input owner and
                # durable output path, never re-enter the group controller.
                human_command = (isinstance(message, UserInput) and not isinstance(message, ExternalInput)
                    and message.metadata.get('input_origin', 'user') == 'user' and not message.images
                    and message.content.startswith('/'))
                if human_command:
                    await session.initialize_extensions()
                    parts = message.content[1:].split(maxsplit=1)
                    if parts and session.extensions.is_native_command(parts[0]):
                        if (session.agent.is_streaming or session._compacting
                                or session._tool_selection_changing or session._tool_selection_retiring):
                            raise RuntimeError('Native command requires an idle parent and settled context')
                        previous_authority = session._user_operation_authorized
                        session._user_operation_authorized = True
                        try:
                            output = await session.extensions.command(parts[0], parts[1] if len(parts) > 1 else '')
                        finally:
                            session._user_operation_authorized = previous_authority
                            await session._persist_tool_retirement()
                        text = output.summary if isinstance(output, ExtensionCommandResult) else str(output)
                        if on_assistant_message is not None:
                            await on_assistant_message(text)
                        return TurnResult(response_text=text, input_handled=True, session_id=session.meta.session_id)
                result: TurnResult | None = await session.run_turn(
                    message,
                    on_display_event=on_display_event,
                    on_text_delta=on_text_delta,
                    on_assistant_message=on_assistant_message,
                    child_ui=child_ui,
                )
                return replace(result, session_id=session.meta.session_id) if result is not None else None
            finally:
                session.agent.set_tool_context({**session.agent.tool_context,
                                               '_reply_reference': None, '_message_port': None})
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
            from xdog.agent.cross_loop import settle_on
            async def inspect() -> bool | None:
                if (runtime._active_session is not session or session.agent is not agent
                        or runtime.get_new_session_id() != session_id or session.extensions._closed):
                    return None
                return bool(agent.pending_inputs()) or agent.has_queued_messages()
            return await settle_on(loop, inspect())
        return read



    # -- Channel delivery ------------------------------------------------------

    async def _send_response(self, group_id: str, text: str, *, require_delivery: bool = False) -> None:
        channels = [channel for channel in self._channels if channel.accepts_group(group_id)]
        if require_delivery and not channels:
            raise RuntimeError(f"No delivery channel for group {group_id}")
        for chunk in self._chunker.chunk(text) or [text]:
            for channel in channels:
                await channel.send_chunk(group_id, chunk)

    def register_reply_channel(self, channel: Channel) -> str:
        return self._channel_replies.register(channel)

    def unregister_reply_channel(self, binding: str) -> None:
        self._channel_replies.unregister(binding)

    def capture_reply(self, channel: Channel, original: GroupInput) -> ReplyReference:
        if not isinstance(original, UserInput):
            raise ValueError('A reply reference requires the original input route')
        workspace = self.get_group_workspace(original.group_id)
        if workspace is None:
            raise ValueError('Unknown reply workspace')
        return self._channel_replies.capture(InputScope(original.group_id, workspace), channel, original)

    # -- Group operations (internal gateway boundary) -----------------------------

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

    async def abort_native_input(self, group_id: str, message_id: str,
                                 binding: Callable[[InputExecution], None],
                                 expected_execution: InputExecution | None = None) -> bool:
        """Withdraw an original pending call or cancel its exact actual producer."""
        runtime = self._runtimes.get(group_id)
        workspace = self.get_group_workspace(group_id)
        if runtime is None or workspace is None:
            return False
        scope, controller = InputScope(group_id, workspace), self.group_controller(group_id)
        if expected_execution is not None:
            if (expected_execution.message_id != message_id
                    or runtime.get_new_session_id() != expected_execution.session_id
                    or not controller.owns_input(message_id, expected_execution.attempt_id)):
                return False
            runtime.abort()  # Preserve the existing original Agent/child cancellation semantics.
            return controller.cancel_input(message_id, expected_execution.attempt_id)
        key = scope, message_id
        call = self._input_calls.get(key)
        if call is None or call.options.get('on_execution_started') is not binding:
            return False  # A duplicate result waiter cannot borrow the original caller's controls.
        inputs = await self.input_service()
        try:
            await inputs.withdraw(scope, message_id)
            return True
        except ValueError:
            # Admission may have won the same explicit pending withdrawal. The
            # original live binding must still match before selecting its attempt.
            if self._input_calls.get(key) is not call:
                return False
            execution = controller.execution_for(message_id)
            if execution is not None:
                runtime.abort()
                return controller.cancel_input(message_id, execution.attempt_id)
            if call.detached.is_set() and await inputs.store.get(scope, message_id) is None:
                if self._input_calls.get(key) is call:
                    self._input_calls.pop(key, None)
            return False

    async def wait_group_idle(self, group_id: str) -> None:
        runtime = self._runtimes.get(group_id)
        if runtime:
            await runtime.wait_for_idle()

    def group_controller(self, group_id: str) -> GroupController:
        if group_id not in self._runtimes:
            raise ValueError('Unknown execution group')
        if group_id not in self._controllers:
            controller = GroupController(group_id, self._background_slots)
            self._controllers[group_id] = controller
            def available() -> None:
                workspace = self.get_group_workspace(group_id)
                if self._inputs is not None and workspace is not None:
                    self._inputs.publish_available(InputScope(group_id, workspace))
            controller.subscribe_available(available)
        return self._controllers[group_id]

    def _execution_snapshot(self, scope: InputScope) -> dict[str, Any]:
        runtime = self._runtimes.get(scope.group_id)
        controller = self._controllers.get(scope.group_id)
        if runtime is None or runtime.workspace_dir != scope.workspace:
            return {'state': 'unavailable', 'user_pending': False, 'children_active': False,
                    'generation': controller.generation if controller is not None else ''}
        controller = self.group_controller(scope.group_id)
        session = runtime._active_session
        state = ('closing' if self._inputs_stopping or runtime._closing else 'running'
                 if controller.busy or session is not None and not session.agent.is_idle else 'idle')
        return {'state': state, 'user_pending': controller.user_pending,
                'children_active': bool(session is not None and session.children.runtime.active),
                'generation': controller.generation}

    def register_acp_endpoint(self, owner: object, endpoint: Path, *,
                              valid: Callable[[], bool] = lambda: True) -> Callable[[], None]:
        """Actual private server publication, never an endpoint named by feature data."""
        binding = endpoint, valid
        self._acp_endpoints[owner] = binding
        def changed() -> None:
            if self._inputs is not None:
                for runtime in tuple(self._runtimes.values()):
                    self._inputs.publish_available(InputScope(runtime.group.id, runtime.workspace_dir))
        changed()
        def cancel() -> None:
            if self._acp_endpoints.get(owner) is binding:
                self._acp_endpoints.pop(owner)
                changed()
        return cancel

    def input_address(self, owner: str, workspace: Path) -> dict[str, Any] | None:
        """Existing private receiver facts; no feature-specific routing policy."""
        if (self._inputs_stopping or self.get_group_workspace(owner) != workspace
                or len(self._acp_endpoints) != 1):
            return None
        endpoint, valid = next(iter(self._acp_endpoints.values()))
        if not valid():
            return None
        return {'endpoint': str(endpoint), 'session_id': 'group:' + owner, 'cwd': str(workspace)}

    async def _capability_acp_target(self, scope: InputScope) -> dict[str, Any] | None:
        if (self._inputs_stopping or self.get_group_workspace(scope.group_id) != scope.workspace
                or len(self._acp_endpoints) != 1):
            return None
        owner, binding = next(iter(self._acp_endpoints.items()))
        endpoint, valid = binding
        if not valid():
            return None
        inputs = await self.input_service()
        namespace = await inputs.store.namespace(scope)
        if (self._inputs_stopping or self.get_group_workspace(scope.group_id) != scope.workspace
                or len(self._acp_endpoints) != 1 or self._acp_endpoints.get(owner) is not binding or not valid()):
            return None
        return {'endpoint': str(endpoint), 'session_id': 'group:' + scope.group_id, 'cwd': str(scope.workspace),
                'inbox_id': namespace, 'branch_id': ''}

    def _input_execution_scope(self, record: InputRecord) -> AbstractAsyncContextManager[None]:
        return self.group_controller(record.envelope.scope.group_id).acquire(
            is_user=record.envelope.context.get('kind') == 'user',
            input_key=(record.envelope.message.id, record.attempt_id))

    def _input_admitted(self, record: InputRecord, session_id: str) -> None:
        self.group_controller(record.envelope.scope.group_id).confirm_input(
            record.envelope.message.id, record.attempt_id, session_id)
        call = self._input_calls.get((record.envelope.scope, record.envelope.message.id))
        if call is not None and call.envelope == record.envelope:
            notify = call.options.get('on_execution_started')
            if notify is not None:
                try:
                    notify(InputExecution(record.envelope.message.id, record.attempt_id, session_id))
                except Exception:
                    logger.warning('Execution admitted; optional original view notification failed', exc_info=True)

    def is_group_running(self, group_id: str) -> bool:
        controller = self._controllers.get(group_id)
        workspace = self.get_group_workspace(group_id)
        return bool(controller is not None and controller.busy or self._inputs is not None and workspace is not None
                    and self._inputs.has_work(InputScope(group_id, workspace)))

    def try_reserve_group(self, group_id: str) -> GroupReservation | None:
        if group_id not in self._runtimes or self.is_group_running(group_id):
            return None
        return self.group_controller(group_id).reserve()

    def release_group_reservation(self, reservation: GroupReservation | None) -> None:
        if reservation is not None:
            if not isinstance(reservation, GroupReservation):
                raise ValueError('Releasing a group requires the original reservation, not a group name')
            controller = self._controllers.get(reservation.group_id)
            if controller is not None:
                controller.release(reservation)

    def operation_scope(self, reservation: GroupReservation | None) -> AbstractAsyncContextManager[None]:
        return (nullcontext() if reservation is None else
                self.group_controller(reservation.group_id).acquire(is_user=True, reservation=reservation))

    def start_group_operation(self, reservation: GroupReservation | None, coroutine: Coroutine[Any, Any, T]
                              ) -> asyncio.Task[T]:
        if reservation is None:
            return asyncio.create_task(coroutine)
        return self.group_controller(reservation.group_id).start(reservation, coroutine)

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
            async with self.group_controller(group_id).acquire(is_user=True):
                session = await runtime.prepare_session(allow_reset=False)
                session.children.extensions.default_ui = child_ui
                owner = session.children
                result = await session.manage_children(action, identifier, text, access, config=config, images=images,
                                                       return_owner=return_owner, return_alive=return_alive)
        else:
            session = await runtime.prepare_session(allow_reset=False)
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
        runtime = self._runtimes.get(group_id)
        if runtime is None:
            raise ValueError("Unknown group")
        if (runtime.get_new_session_id() or "") != expected_session:
            raise ValueError("Access session changed; refresh before retrying")
        if action == "status":
            if runtime._active_session is not None:
                state = runtime._active_session.access.state
            elif expected_session:
                generation = runtime._source_revision
                raw = await run_owned(runtime.transcript_store.get_access, expected_session)
                if (runtime._source_revision != generation
                        or (runtime.get_new_session_id() or '') != expected_session):
                    raise ValueError('Access status owner changed; refresh the current group')
                state = AccessState.from_dict(raw)
            else:
                state = AccessState()
        elif action in {"restrict", "release"}:
            async with self.group_controller(group_id).acquire(is_user=True):
                if (runtime.get_new_session_id() or "") != expected_session:
                    raise ValueError("Access session changed; refresh before retrying")
                session = await runtime.prepare_session(allow_reset=False)
                session._children_idle()
                state = await run_owned(lambda: session.access.set_read_only(action == "restrict"))
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
        session = await runtime.prepare_session(allow_reset=False)
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
        session = await runtime.prepare_session(allow_reset=False)
        def owner_alive() -> bool:
            return (alive() and runtime._active_session is session
                    and group_id not in self._navigation_groups)
        return await session.skills_control(action, slug, owner_alive)

    async def reasoning(self, group_id: str, expected_session: str, level: str | None = None) -> dict[str, Any]:
        runtime = self._runtimes.get(group_id)
        if runtime is None or (runtime.get_new_session_id() or "") != expected_session:
            raise ValueError("Reasoning session changed; refresh before changing settings")
        session = await runtime.prepare_session(allow_reset=False)
        # An initially empty owner may explicitly bootstrap its first session.
        owner_session = session.meta.session_id
        await session.initialize_extensions()
        if session.agent.state.is_streaming:
            raise ValueError("Reasoning changes require an idle agent")
        if level is not None:
            session._children_idle()
        model_id = session.agent.state.model
        stream = session.agent.stream_fn
        model = await run_owned(stream.resolve_model, model_id) if isinstance(stream, ModelStreamFn) else None
        if (runtime._active_session is not session or runtime.get_new_session_id() != owner_session
                or session.agent.state.model != model_id or session.agent.stream_fn is not stream
                or session.agent.state.is_streaming):
            raise ValueError("Reasoning owner changed during metadata read")
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
                session._model_metadata = replace(session._model_metadata, metadata=model)
                runtime._source_revision += 1
            try:
                await run_owned(persist)
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
        session = await runtime.prepare_session(allow_reset=False)
        # An initially empty owner may explicitly bootstrap its first session.
        owner_session = session.meta.session_id
        await session.initialize_extensions()
        stream = session.agent.stream_fn
        original_model = session.agent.state.model

        def owner_current() -> bool:
            return (runtime._active_session is session and runtime.get_new_session_id() == owner_session
                    and session.agent.state.model == original_model and session.agent.stream_fn is stream)

        models = (await run_owned(stream.models) if isinstance(stream, CatalogStreamFn)
                  else session.extensions.provider_models())
        if not owner_current():
            raise ValueError("Model catalog owner changed during metadata read")
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
            resolved = await run_owned(stream.resolve_model, selected) if isinstance(stream, ModelStreamFn) else None
            if not owner_current() or session.agent.state.is_streaming:
                raise ValueError("Model selection owner changed during metadata read")
            if (resolved is None or resolved.model_type != "chat" or not resolved.supports_streaming
                    or resolved.context_window <= 0):
                raise ValueError("Model is not an available streaming chat model on configured adapter; no fallback")
            model = resolved
            selected = model.id  # Persist and route the canonical identity, not a short lookup alias.
            target_thinking = session.agent.options.thinking
            if selected != session.agent.state.model:
                saved = await run_owned(runtime.transcript_store.get_reasoning,
                                                session.meta.session_id, selected)
                if not owner_current() or session.agent.state.is_streaming:
                    raise ValueError("Model selection owner changed during saved reasoning read")
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
                session._model_metadata = replace(session._model_metadata, model_id=selected,
                    metadata=model, supports_tool_calls=model.supports_tool_calls)
                runtime.model, runtime.context_window = selected, model.context_window
                runtime.max_prompt_tokens = model.max_prompt_tokens
                runtime._source_revision += 1
            try:
                await run_owned(persist)
            except asyncio.CancelledError:
                if committed[0] and owner_alive():
                    apply()  # Lost acknowledgement is not a rollback/replay of the admitted atomic setting.
                raise
            if not owner_alive():
                raise ValueError("Model session changed before applying stored selection")
            apply()
        active_id = session.agent.state.model
        active_model = await run_owned(stream.resolve_model, active_id) if isinstance(stream, ModelStreamFn) else None
        if (runtime._active_session is not session or runtime.get_new_session_id() != owner_session
                or session.agent.state.model != active_id or session.agent.stream_fn is not stream):
            raise ValueError("Active model owner changed during metadata read")
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
                    and not agent.is_streaming and not session.children.runtime.active and not session._compacting
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
            await session._persist_tool_retirement()

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
        async with self.group_controller(group_id).acquire(is_user=True):
            session = await runtime.prepare_session(allow_reset=False)
            if action == 'command' and session.extensions.is_native_command(name):
                if (session.agent.is_streaming or session._compacting
                        or session._tool_selection_changing or session._tool_selection_retiring):
                    raise RuntimeError('Native command requires an idle parent and settled context')
            else:
                session._children_idle()
            await session.initialize_extensions()
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
                if (action != "command" or session.extensions.command_owner(name) != extension
                        or not owner_session or not owned()):
                    return None
                scope = ("sessions" if request["scope"] == "sessions" else
                         "tree|" + (request["session_id"] or owner_session) + "|" + request["branch_id"])
                rows = await run_owned(runtime.navigation_entries, scope)
                result = await run_owned(page, rows, request, owner_session)
                return result if owned() else None
            async def host_dialog(extension: str, dialog: dict[str, Any]) -> Any:
                from xdog.agent.cross_loop import settle_on
                from xdog.agent.extensions.dialogs import validate_sdk_dialog
                validate_sdk_dialog(dialog)  # Retired writes cannot borrow this read-only bridge.
                if not owned() or session.extensions.command_owner(name) != extension:
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
            previous_authority = session._user_operation_authorized
            try:
                await runtime.initialize_capabilities(await self.input_service())
                await session.initialize_extensions()
                if action == "command":
                    session._user_operation_authorized = True
                    try:
                        output = await session.extensions.command(name, args)
                    finally:
                        session._user_operation_authorized = previous_authority
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
                session._user_operation_authorized = previous_authority
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
                await session._persist_tool_retirement()

    async def complete_extension_argument(self, group_id: str, expected_session: str,
                                          command: str, prefix: str, identity: str,
                                          argument_input: dict[str, Any] | None = None) -> dict[str, Any]:
        runtime = self._runtimes.get(group_id)
        if (runtime is None or runtime.get_new_session_id() != expected_session
                or runtime._active_session is None or runtime._active_session.meta.session_id != expected_session):
            raise ValueError('Argument completion owner changed; no activation')
        async with self.group_controller(group_id).acquire(is_user=True):
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
                await session._persist_tool_retirement()

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
            async with self.group_controller(group_id).acquire(is_user=True):
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
        async with self.group_controller(group_id).acquire(is_user=True):
            if runtime._active_session is not None:
                runtime._active_session._children_idle()
            result = await runtime.mcp.manage_auth(server, action, receipt, lease=lease, alive=alive)
            if action == "forget":
                if runtime._active_session is not None:
                    runtime._active_session.agent.sync_mcp_tools()
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
        return await run_owned(runtime.navigation_entries, scope)

    async def navigate_session(self, group_id: str, action: str, session_id: str, branch_id: str,
                               index: int, expected_session: str) -> None:
        runtime = self._runtimes.get(group_id)
        if runtime is None:
            raise ValueError("Unknown group")
        if action != 'new':
            raise ValueError('Claw history is read-only; historical route activation is not supported')
        if session_id or branch_id or index != -1:
            raise ValueError('New context cannot select historical data')
        self._navigation_groups.add(group_id)
        try:
            async with self.group_controller(group_id).acquire(is_user=True):
                await runtime.reset_context(expected_session)
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
            async with self.group_controller(group_id).acquire(is_user=True):
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
                      *, images_validated: bool = False, expected_execution: InputExecution | None = None) -> bool:
        runtime = self._runtimes.get(group_id)
        if runtime is None or not self.is_group_running(group_id):
            return False
        if expected_execution is not None and (
                not self.group_controller(group_id).owns_input(expected_execution.message_id,
                                                              expected_execution.attempt_id)
                or runtime.get_new_session_id() != expected_execution.session_id):
            return False
        return runtime.control_input(action, input_id, content, delivery, direction, images,
                                     images_validated=images_validated)

    def validate_images(self, group_id: str, images: tuple[ImageContent, ...]) -> None:
        runtime = self._runtimes.get(group_id)
        if runtime is None:
            raise ValueError("Unknown group")
        runtime.validate_images(images)

    def pending_inputs(self, group_id: str, prefix: str, *, expected_execution: InputExecution | None = None
                       ) -> tuple[Any, ...]:
        runtime = self._runtimes.get(group_id)
        if expected_execution is not None and (runtime is None
                or runtime.get_new_session_id() != expected_execution.session_id
                or not self.group_controller(group_id).owns_input(expected_execution.message_id,
                                                                 expected_execution.attempt_id)):
            return ()
        return runtime.pending_inputs(prefix) if runtime is not None else ()

    def drain_inputs(self, group_id: str, prefix: str) -> tuple[Any, ...]:
        runtime = self._runtimes.get(group_id)
        return runtime.drain_inputs(prefix) if runtime is not None else ()

    def get_group_ids(self) -> list[str]:
        return list(self._runtimes.keys())

    def get_group_workspace(self, group_id: str) -> Path | None:
        """Return the already canonical registered root without filesystem IO or an Agent."""
        runtime = self._runtimes.get(group_id)
        return runtime.workspace_dir if runtime is not None else None

    def get_session_info(self, group_id: str) -> dict[str, Any]:
        """Session metadata + history for a group (delegates to runtime)."""
        runtime = self._runtimes.get(group_id)
        if not runtime:
            return {}
        return runtime.get_session_info()

    async def read_session_info(self, group_id: str) -> dict[str, Any]:
        runtime = self._runtimes.get(group_id)
        return await runtime.read_session_info() if runtime is not None else {}

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

    async def refresh_application_capabilities(self) -> None:
        """Publish installed service tools only while actual execution is quiescent."""
        for runtime in self._runtimes.values():
            session = runtime._active_session
            if session is None:
                continue
            if session.agent.state.is_streaming:
                raise RuntimeError("Application capability publication requires an idle owner")
            runtime.publish_tools(session)
            session._restore_active_tools()
            await session._persist_tool_retirement()
        self.channels_ready()

    # -- Lifecycle -------------------------------------------------------------

    async def start(self) -> None:
        inputs = await self.input_service()
        for runtime in tuple(self._runtimes.values()):
            if self._inputs_stopping:
                return
            await runtime.initialize_capabilities(inputs)
            if self._inputs_stopping:
                return
            await runtime.capabilities.flush()
        for channel in self._channels:
            if self._inputs_stopping:
                return
            await channel.connect()
            self.channels_ready()

    async def stop(self) -> None:
        """One cleanup result; repeated cancellation cannot skip later resources."""
        self.request_stop()
        if self._stop_task is None:
            self._stop_task = asyncio.create_task(self._stop(), name="claw-application-close")
        await wait_owned(self._stop_task)

    async def _stop(self) -> None:
        group_closed = await asyncio.gather(*tuple(self._group_initializers), return_exceptions=True)
        if self._input_start_task is not None:
            await asyncio.gather(self._input_start_task, return_exceptions=True)
        # Settle capability publication before closing either its input port or its lease.
        # start() fences later group admissions once request_stop() has run.
        capability_started = await asyncio.gather(
            *(runtime.settle_capabilities() for runtime in self._runtimes.values()), return_exceptions=True)
        # Stop ingress and settle channel-owned turns before closing their resources.
        disconnected = await asyncio.gather(
            *(channel.disconnect() for channel in self._channels), return_exceptions=True,
        )
        application_closed = await asyncio.gather(self.application_capabilities.aclose(), return_exceptions=True)
        input_closed = await asyncio.gather(self._inputs.close(), return_exceptions=True) if self._inputs else []
        output_closed = await asyncio.gather(self._outputs.close(), return_exceptions=True) if self._outputs else []
        if self._output_resume is not None:
            await asyncio.gather(self._output_resume, return_exceptions=True)
        runtime_closed = await asyncio.gather(
            *(runtime.aclose() for runtime in self._runtimes.values()), return_exceptions=True)
        errors = [result for result in (*group_closed, *capability_started, *disconnected,
            *application_closed, *input_closed, *output_closed, *runtime_closed) if isinstance(result, BaseException)]
        if len(errors) == 1:
            raise errors[0]
        if errors:
            raise BaseExceptionGroup("Application resource cleanup failed", errors)
