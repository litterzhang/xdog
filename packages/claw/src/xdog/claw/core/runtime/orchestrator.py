"""Top-level orchestrator — message routing and lifecycle management."""
from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable, Coroutine
from contextlib import AbstractAsyncContextManager, nullcontext
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, TypeVar

from xdog.agent.async_work import run_owned, wait_owned
from xdog.agent.capabilities import CapabilitySet
from xdog.agent.core import ModelStreamFn
from xdog.agent.extensions.command_result import ExtensionCommandResult
from xdog.agent.mcp.completion import McpCompletion
from xdog.agent.mcp.context import McpDocument, McpEntry
from xdog.agent.session_input import SessionInput
from xdog.ai.types import ImageContent, Model
from xdog.claw.channels.base import Channel
from xdog.claw.config import ClawConfig
from xdog.claw.core.inputs.consumer import Admit, InputOutcome, InputService
from xdog.claw.core.inputs.models import InputEnvelope, InputScope, ReplyTarget
from xdog.claw.core.inputs.store import InputRecord, InputStore
from xdog.claw.core.outputs.delivery import OutputDelivery, Policy
from xdog.claw.core.outputs.store import OutputRecord, OutputStore
from xdog.claw.core.outputs.targets import ReplyReference
from xdog.claw.core.runtime.channel_adapter import ChannelReplies, ChannelReply, durable_reply
from xdog.claw.core.runtime.extension_controls import command_menu_handler, pending_messages_reader
from xdog.claw.core.runtime.group import GroupRuntime
from xdog.claw.core.runtime.group_controller import GroupController, GroupReservation, InputExecution
from xdog.claw.core.runtime.input_adapter import freeze_input, restore_input
from xdog.claw.core.runtime.session import TurnResult
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
        from xdog.claw.core.identity import IdentityResolver
        self.identities = IdentityResolver.from_config(config.identity_bindings)
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
        self._background_slots = asyncio.Semaphore(config.max_concurrent_agents)
        self._controllers: dict[str, GroupController] = {}
        self._acp_endpoints: dict[object, tuple[Path, Callable[[], bool]]] = {}
        self.application_capabilities = CapabilitySet("application")
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
        runtime.channel_profiles = lambda: tuple(profile for channel in self._channels
            if (profile := channel.account_profile(group.id)) is not None)
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

        def identity(message: GroupInput) -> Any:
            if not isinstance(message, UserInput) or isinstance(message, ExternalInput):
                raise ValueError('Chat channels must supply platform user messages, not system work')
            source = channel.input_source(message)
            # Native TUI uses its private, authenticated local application entry;
            # a channel merely naming itself tui must not impersonate that entry.
            bound = self.identities.channel(message.group_id, source)
            if bound.principal.role not in channel.input_roles:
                raise ValueError('Configured role exceeds the actual channel identity ceiling')
            return bound

        async def accept(message: GroupInput) -> None:
            await self.route_message(message, message_id=self._channel_message_id(message),
                wait_for_result=False,
                host_identity=identity(message), identity_binding="channel",
                reply_mode=channel.reply_mode,
                on_assistant_message=self._channel_replies.reply(channel, message))

        async def dispatch(message: GroupInput) -> None:
            await self.route_message(message, message_id=self._channel_message_id(message),
                reserved=self._channel_message_id(message) is not None,
                host_identity=identity(message), identity_binding="channel",
                reply_mode=channel.reply_mode,
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
            outputs = OutputDelivery(output_store, self._channel_replies.resolve, gate=self._output_identity_policy)
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
        for controller in self._controllers.values():
            controller.close_admission()
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

    def _bound_identity_current(self, group_id: str, raw: object, binding: object) -> bool:
        from xdog.claw.core.identity import IdentityResolver, InputIdentity
        if raw is None:
            return False  # Retain old data, never guess a missing execution identity.
        identity = InputIdentity.from_dict(raw)
        if binding == 'channel':
            return self.identities.current(group_id, identity)
        if binding == 'local':
            return identity == IdentityResolver.local(identity.source)
        if binding == 'system':
            return identity == IdentityResolver.system(identity.source)
        return False

    def _identity_current(self, record: InputRecord | None) -> bool:
        if record is None:
            return True  # Explicit trusted host composition, never restored intake.
        context = record.envelope.context
        return self._bound_identity_current(record.envelope.scope.group_id,
                                            context.get('identity'), context.get('identity_binding'))

    async def _output_identity_policy(self, row: OutputRecord) -> Policy:
        # Execution and transport settle independently. Revocation must also
        # fence a private answer generated before the binding was revoked.
        context = row.envelope.context
        raw = context.get('source_identity') if context.get('outbound_tool') == 'message' else context.get('identity')
        binding = (context.get('source_identity_binding') if context.get('outbound_tool') == 'message'
                   else context.get('identity_binding'))
        return 'ready' if self._bound_identity_current(row.envelope.scope.group_id, raw, binding) else 'pending'

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
        final_send: Callable[[str], Awaitable[None]] | None = None
        display = options.get('on_display_event')
        if call is not None and display is not None:
            def publish(event: Any) -> None:
                if not call.detached.is_set():
                    display(event)
            options['on_display_event'] = publish
        try:
            value = restore_input(record.envelope)
            if not self._identity_current(record):
                raise ValueError('Original host identity is missing or changed; explicit resubmission is required')
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
                mode = record.envelope.context.get('reply_mode', 'stream')
                if mode not in {'stream', 'final', 'none'}:
                    raise ValueError('Invalid saved channel reply mode')
                send = durable_reply(self._outputs, record, binding, atomic=mode == 'final')
                final_send = send if mode == 'final' else None
                options['on_assistant_message'] = None if mode in {'final', 'none'} else send
                async def send_error(error: str) -> None:
                    identity = value.identity
                    public = identity is not None and identity.principal.role in {'user', 'external_user'}
                    await send('暂时无法处理这条消息，请稍后再试。' if public else f'Error: {error}')
                on_error = send_error if mode != 'none' else None
            result = await self._execute_owned_input(value, admit=admit, message_record=record, **options)
            if result is None:
                # A host fence explicitly refused this unstarted submission.
                result = TurnResult(error='Host input admission was withdrawn')
            if not result.error and final_send is not None:
                final = result.response_text if result.input_handled else result.final_response
                if final:
                    await final_send(final)
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
        host_identity: Any = None, identity_binding: str = "local", reply_mode: str = "stream",
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
        envelope = freeze_input(message, workspace, message_id=identifier, admission_key=binding, reply=reply_target,
                                identity=host_identity, identity_binding=identity_binding,
                                reply_mode=reply_mode)
        service = await self.input_service()
        if identity_binding == 'channel':
            existing = await service.store.get(envelope.scope, identifier)
            if existing is not None:
                from xdog.claw.core.runtime.input_adapter import same_channel_submission
                if not same_channel_submission(existing.envelope, envelope):
                    raise ValueError('Accepted channel input content conflict')
                envelope = existing.envelope  # ACK retry cannot change original authorization or reply policy.
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
                        and not session.access.state.read_only and self._identity_current(message_record)
                        and message_tool is not None
                        and any(tool is message_tool for tool in actual_agent.active_tools))
            message_port = (OutboundMessages(message_record, self._channel_replies, self._outputs, message_valid)
                            if message_record is not None and self._outputs is not None else None)
            from xdog.claw.core.runtime.identity_scope import allows_tool
            from xdog.claw.core.runtime.xhs_posts import bind_reader
            post_tool = next((tool for tool in actual_agent.active_tools if tool.name == 'xhs_post'), None)
            def post_valid() -> bool:
                return (not self._inputs_stopping and not runtime._closing
                        and runtime._active_session is session and session.agent is actual_agent
                        and message_record is not None and self._identity_current(message_record)
                        and session._input_execution is not None
                        and session._input_execution.input_id == message_record.envelope.message.id
                        and post_tool is not None and any(tool is post_tool for tool in actual_agent.active_tools)
                        and allows_tool(session, 'xhs_post'))
            reader = bind_reader(self._channel_replies, message, post_valid) if post_tool is not None else None
            session.agent.set_tool_context({**session.agent.tool_context,
                                           '_reply_reference': reply_reference, '_message_port': message_port,
                                           '_xhs_post_reader': reader})
            previous_authority_guard = session._user_operation_current
            session._user_operation_current = lambda: self._identity_current(message_record)
            previous_menu = session.extensions.command_menu_handler
            menu_handler = command_menu_handler(session, command_menu) if command_menu is not None else None
            previous_pending = session.extensions.pending_messages
            session.extensions.command_menu_handler = menu_handler
            session.extensions.pending_messages = pending_messages_reader(session)
            try:
                # Channel commands use the same admitted Core input owner and
                # durable output path, never re-enter the group controller.
                human_command = (isinstance(message, UserInput) and not isinstance(message, ExternalInput)
                    and message.identity is not None and message.identity.human_authorized and not message.images
                    and message.content.startswith('/'))
                if human_command:
                    await session.initialize_extensions()
                    if not self._identity_current(message_record):
                        raise ValueError("Original command identity grant changed before dispatch")
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
                    message, input_id=message_record.envelope.message.id if message_record is not None else None,
                    identity_current=lambda: self._identity_current(message_record),
                    on_display_event=on_display_event,
                    on_text_delta=on_text_delta,
                    on_assistant_message=on_assistant_message,
                    child_ui=child_ui,
                )
                return replace(result, session_id=session.meta.session_id) if result is not None else None
            finally:
                session.agent.set_tool_context({**session.agent.tool_context,
                                               '_reply_reference': None, '_message_port': None,
                                               '_xhs_post_reader': None})
                session.extensions.pending_messages = previous_pending
                session._user_operation_current = previous_authority_guard
                if session.extensions.command_menu_handler is menu_handler:
                    session.extensions.command_menu_handler = previous_menu


    # -- Channel delivery ------------------------------------------------------


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


    def group_controller(self, group_id: str) -> GroupController:
        if group_id not in self._runtimes:
            raise ValueError('Unknown execution group')
        if group_id not in self._controllers:
            controller = GroupController(group_id, self._background_slots)
            if self._inputs_stopping:
                controller.close_admission()
            self._controllers[group_id] = controller
            def available() -> None:
                workspace = self.get_group_workspace(group_id)
                if self._inputs is not None and workspace is not None:
                    self._inputs.publish_available(InputScope(group_id, workspace))
            controller.subscribe_available(available)
        return self._controllers[group_id]

    def context_changing(self, group_id: str) -> bool:
        controller = self._controllers.get(group_id)
        return controller is not None and controller.context_changing

    def _execution_snapshot(self, scope: InputScope) -> dict[str, Any]:
        runtime = self._runtimes.get(scope.group_id)
        controller = self._controllers.get(scope.group_id)
        if runtime is None or runtime.workspace_dir != scope.workspace:
            return {'state': 'unavailable', 'user_pending': False, 'children_active': False,
                    'generation': controller.generation if controller is not None else ''}
        controller = self.group_controller(scope.group_id)
        session = runtime._active_session
        state = ('closing' if self._inputs_stopping or runtime._closing else 'running'
                 if controller.busy or controller.context_changing
                 or session is not None and not session.agent.is_idle else 'idle')
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
        return bool(controller is not None and (controller.busy or controller.context_changing)
                    or self._inputs is not None and workspace is not None
                    and self._inputs.has_work(InputScope(group_id, workspace)))

    def try_reserve_group(self, group_id: str) -> GroupReservation | None:
        if self._inputs_stopping or group_id not in self._runtimes or self.is_group_running(group_id):
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
        if (runtime is None or self.context_changing(group_id)
                or (runtime.get_new_session_id() or "") != expected_session):
            raise ValueError("Child session/group changed; refresh before retrying")
        if action in {"list", "presets"} and not expected_session:
            data: dict[str, Any] = ({"children": []} if action == "list" else
                    {"presets": runtime.child_presets.snapshot()})
            return {"session_id": "", "data": data}
        # Inspection/control must remain usable while the parent is waiting for a
        # child. Only start needs an idle group for prompt/tool snapshot rebuilding.
        if action == "start":
            async with self.group_controller(group_id).operation(
                validate=lambda: runtime.require_current(expected_session)):
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
                or self.context_changing(group_id)):
            raise ValueError('Child session/branch owner changed after control; inspect, no replay')
        return {"session_id": runtime.get_new_session_id() or "", "data": result}

    def answer_child_dialog(self, group_id: str, session_id: str, child: str, identifier: str,
                            token: str, value: Any) -> bool:
        runtime = self._runtimes.get(group_id)
        if (runtime is None or runtime._active_session is None or self.context_changing(group_id)
                or (runtime.get_new_session_id() or "") != session_id):
            return False
        return runtime._active_session.children.extensions.answer(child, identifier, token, value)


    def detach_child_ui(self, token: str) -> None:
        for runtime in self._runtimes.values():
            if runtime._active_session is not None:
                runtime._active_session.children.extensions.detach(token)

    async def manage_access(self, group_id: str, action: str, expected_session: str) -> dict[str, Any]:
        from xdog.claw.core.runtime.access_controls import manage_access
        runtime = self._runtimes.get(group_id)
        if runtime is None:
            raise ValueError('Unknown group')
        return await manage_access(runtime, self.group_controller(group_id), action, expected_session)

    async def compact_session(self, group_id: str, expected_session: str, instructions: str,
                              alive: Callable[[], bool]) -> dict[str, Any]:
        runtime = self._runtimes.get(group_id)
        if (runtime is None or (runtime.get_new_session_id() or "") != expected_session
                or self.context_changing(group_id)):
            raise ValueError("Compaction group/session changed; refresh before retrying")
        async with self.group_controller(group_id).operation(
                validate=lambda: runtime.require_current(expected_session)):
            session = await runtime.prepare_session(allow_reset=False)
            def owner_alive() -> bool:
                return (alive() and runtime._active_session is session
                        and not self.context_changing(group_id))
            return await session.compact(instructions, owner_alive)

    async def skills_control(self, group_id: str, expected_session: str, action: str, slug: str,
                             alive: Callable[[], bool] = lambda: True) -> dict[str, Any]:
        runtime = self._runtimes.get(group_id)
        if (runtime is None or expected_session and runtime.get_new_session_id() != expected_session
                or self.context_changing(group_id)):
            raise ValueError('Skill group/session owner changed')
        async with self.group_controller(group_id).operation(
                validate=lambda: runtime.require_current(expected_session)):
            session = await runtime.prepare_session(allow_reset=False)
            def owner_alive() -> bool:
                return (alive() and runtime._active_session is session
                        and not self.context_changing(group_id))
            return await session.skills_control(action, slug, owner_alive)

    async def reasoning(self, group_id: str, expected_session: str, level: str | None = None) -> dict[str, Any]:
        from xdog.claw.core.runtime.model_controls import reasoning
        runtime = self._runtimes.get(group_id)
        if runtime is None:
            raise ValueError('Unknown group')
        async with self.group_controller(group_id).operation(
                validate=lambda: runtime.require_current(expected_session)):
            return await reasoning(runtime, expected_session, level)

    async def extension_models(self, group_id: str, expected_session: str, selected: str = "") -> dict[str, Any]:
        from xdog.claw.core.runtime.model_controls import extension_models
        runtime = self._runtimes.get(group_id)
        if runtime is None:
            raise ValueError('Unknown group')
        async with self.group_controller(group_id).operation(
                validate=lambda: runtime.require_current(expected_session)):
            return await extension_models(runtime, expected_session, selected)


    async def manage_extensions(self, group_id: str, action: str, name: str, args: str,
                                expected_session: str,
                                on_dialog: Callable[[dict[str, Any]], None] | None = None, *,
                                provider_auth_alive: Callable[[], bool] | None = None,
                                command_menu: CommandMenuHost | None = None,
                                ) -> dict[str, Any]:
        from xdog.claw.core.runtime.extension_controls import manage_extensions
        runtime = self._runtimes.get(group_id)
        if runtime is None:
            raise ValueError('Unknown group')
        return await manage_extensions(runtime, self.group_controller(group_id),
            action, name, args, expected_session, on_dialog,
            provider_auth_alive=provider_auth_alive, command_menu=command_menu, input_service=self.input_service)

    async def complete_extension_argument(self, group_id: str, expected_session: str,
                                          command: str, prefix: str, identity: str,
                                          argument_input: dict[str, Any] | None = None) -> dict[str, Any]:
        from xdog.claw.core.runtime.extension_controls import complete_extension_argument
        runtime = self._runtimes.get(group_id)
        if runtime is None:
            raise ValueError('Unknown group')
        return await complete_extension_argument(runtime, self.group_controller(group_id),
            expected_session, command, prefix, identity, argument_input)

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
                or runtime._active_session is None or self.context_changing(group_id)):
            return None
        session = runtime._active_session
        if not session.extension_dialogs.contains(identifier) or session.agent.state.is_streaming:
            return None
        if operation == 'name':
            value = session._extension_session_name()
            return {'session_id': session_id, 'name': value['name']} if value is not None else None
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
        from xdog.claw.core.runtime.mcp_controls import manage_mcp
        runtime = self._runtimes.get(group_id)
        if runtime is None:
            raise ValueError('Unknown group')
        return await manage_mcp(runtime, self.group_controller(group_id), action, server)


    async def manage_mcp_auth(self, group_id: str, server: str, action: str, receipt: str,
                              session: str, lease: str, alive: Callable[[], bool]) -> dict[str, Any]:
        from xdog.claw.core.runtime.mcp_controls import manage_mcp_auth
        runtime = self._runtimes.get(group_id)
        if runtime is None:
            raise ValueError('Unknown group')
        return await manage_mcp_auth(runtime, self.group_controller(group_id),
                                     server, action, receipt, session, lease, alive)

    async def mcp_context(self, group_id: str, action: str, server: str, name: str,
                          arguments: dict[str, str]) -> tuple[McpEntry, ...] | McpDocument | McpCompletion:
        from xdog.claw.core.runtime.mcp_controls import mcp_context
        runtime = self._runtimes.get(group_id)
        if runtime is None:
            raise ValueError('Unknown group')
        return await mcp_context(runtime, action, server, name, arguments)

    async def navigation_entries(self, group_id: str, scope: str) -> list[dict[str, Any]]:
        from xdog.claw.core.runtime.context_controls import navigation_entries
        runtime = self._runtimes.get(group_id)
        if runtime is None:
            raise ValueError('Unknown group')
        return await navigation_entries(runtime, scope)

    async def navigate_session(self, group_id: str, action: str, session_id: str, branch_id: str,
                               index: int, expected_session: str) -> None:
        from xdog.claw.core.runtime.context_controls import navigate_session
        runtime = self._runtimes.get(group_id)
        if runtime is None:
            raise ValueError('Unknown group')
        return await navigate_session(runtime, self.group_controller(group_id),
                                      action, session_id, branch_id, index, expected_session)


    async def read_session_name(self, group_id: str, expected_session: str) -> dict[str, str]:
        from xdog.claw.core.runtime.context_controls import read_session_name
        runtime = self._runtimes.get(group_id)
        if runtime is None:
            raise ValueError('Unknown group')
        return await read_session_name(runtime, self.group_controller(group_id), expected_session)

    async def rename_session(self, group_id: str, expected_session: str, name: str, *,
                             expected_name: str | None = None, alive: Callable[[], bool] | None = None) -> str:
        from xdog.claw.core.runtime.context_controls import rename_session
        runtime = self._runtimes.get(group_id)
        if runtime is None:
            raise ValueError('Unknown group')
        return await rename_session(runtime, self.group_controller(group_id), expected_session, name,
                                    expected_name=expected_name, alive=alive)


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
        if self.context_changing(group_id):
            raise RuntimeError("Session context is changing; refresh after navigation")
        return runtime.capture_source(expected_session)

    def capture_saved_source(self, group_id: str, expected_session: str, target_session: str,
                             branch_id: str) -> SourceCapture:
        runtime = self._runtimes.get(group_id)
        if runtime is None or self.context_changing(group_id):
            raise ValueError('Saved source group/context unavailable')
        return runtime.capture_saved_source(expected_session, target_session, branch_id)

    def source_lease(self, group_id: str) -> tuple[str, int]:
        runtime = self._runtimes.get(group_id)
        if runtime is None or self.context_changing(group_id):
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
