"""Native presentation adapter; the session controller owns every runnable input."""
from __future__ import annotations

import asyncio
import sys
import threading
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from xdog.agent import (
    AgentEndEvent,
    MessageEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    ToolExecutionEndEvent,
    ToolExecutionUpdateEvent,
    TurnEndEvent,
)
from xdog.agent.session_input import SessionInput
from xdog.ai.types import AssistantMessage, TextContent
from xdog.coding.core.permissions import PermissionRequest
from xdog.coding.core.sessions.batches import TurnBatch
from xdog.coding.core.sessions.models import SessionEvent, Submission, TurnResult
from xdog.coding.modes.interactive.session_presenter import SessionHistory, capture_history
from xdog.coding.modes.interactive.session_presenter import SessionPresenter as SessionPresenter
from xdog.coding.modes.interactive.session_presenter import external_input_text as external_input_text


@dataclass(frozen=True)
class _AgentUiProjection:
    sequence: int
    payload: dict[str, Any]


def _snapshot_agent_event(data: object) -> object | None:
    # AgentToolResult is intentionally mutable in the tool API. Only its UI
    # projection crosses EventQueue; arbitrary tool details are not UI state.
    if isinstance(data, ToolExecutionEndEvent):
        content = data.result.content if data.result is not None else ()
        text = '\n'.join(part.text for part in content if isinstance(part, TextContent))
        return _AgentUiProjection(data.sequence, {'type': 'tool_result', 'id': data.tool_call_id,
            'name': data.tool_name, 'result': text, 'content': content,
            'is_error': data.is_error or text.startswith('Error:')})
    if isinstance(data, ToolExecutionUpdateEvent):
        content = data.partial_result.content if data.partial_result is not None else ()
        text = ''.join(part.text for part in content if isinstance(part, TextContent))
        return _AgentUiProjection(data.sequence, {'type': 'tool_update', 'id': data.tool_call_id,
                                                  'name': data.tool_name, 'text': text}) if text else None
    if isinstance(data, TurnEndEvent):
        return _AgentUiProjection(data.sequence, {'type': 'turn_footer_update'})
    if isinstance(data, (MessageStartEvent, MessageUpdateEvent, MessageEndEvent)):
        return data if isinstance(data.message, AssistantMessage) else None
    if isinstance(data, AgentEndEvent):
        final = data.messages[-1] if data.messages else None
        return replace(data, messages=(final,) if isinstance(final, AssistantMessage) else ())
    return data


@dataclass
class _NativeInput:
    text: str
    images: tuple[Any, ...]
    origin: str
    echo: bool
    queue_id: str
    started: bool = False


class NativeSessionEvents:
    def __init__(self, ui: Any) -> None:
        self.ui = ui
        self.runtime: Any = None
        self.local: dict[str, _NativeInput] = {}
        self.receipts: dict[str, Any] = {}
        self.holds: set[str] = set()
        self.lock = threading.RLock()
        self.unsubscribe: Any = None
        self.cancelled: set[str] = set()
        self.display_batch: str | None = None
        self.display_members: set[str] = set()
        self.completed_batches: set[tuple[str, str]] = set()
        self.queue_sequence = 0
        self._presenter: SessionPresenter | None = None
        self.completed: set[tuple[str, str]] = set()
        self.stream_sequences: dict[str, int] = {}
        self.initial_history: SessionHistory | None = None

    @property
    def presenter(self) -> SessionPresenter:
        if self._presenter is None:
            self._presenter = SessionPresenter(self.ui._chat_log)
        return self._presenter

    def start(self) -> None:
        if self.runtime is not None:
            return
        self.runtime = self.ui._session.enable_runtime(bound=True)
        self.runtime.start(own_thread=True, setup_pending=True)
        controller = self.runtime.controller
        controller.before_batch = self.prepare
        controller.after_batch = self.finished
        self.unsubscribe = controller.subscribe(self.receive)
        self.ui._session.permissions.set_request_handler(self.ui._on_permission_request)
        self.initial_history = controller._async(lambda: capture_history(self.ui._session)).result(timeout=10)
        if sys.version_info < (3, 15):
            controller._async(self.runtime.open_endpoint).result(timeout=10)
        controller._async(self.runtime.start_inbox_watch).result(timeout=10)

    def reserve_producer(self, name: str) -> Callable[[], None]:
        """Reserve non-turn callbacks before launching a worker; return its settlement receipt."""
        if self.runtime is None:
            return lambda: None
        cancel = self.ui._terminal_worker.cancel if name == 'terminal' else self.ui._session.children.abort
        release: Callable[[], None] = self.runtime.controller.reserve_producer(name, cancel=cancel)
        return release

    def human_input(self) -> None:
        if self.runtime is not None:
            self.runtime.controller.human_input()

    async def prepare(self, batch: TurnBatch) -> None:
        ui = self.ui
        ui._worker_loop_ref = asyncio.get_running_loop()
        ui._worker_task = asyncio.current_task()
        ui._bind_extension_dialogs()
        captured = ui._session.session_id
        ui._session.extensions.publish = lambda event: ui._event_queue.put({**event, 'extension_session': captured})
        self.receipts[batch.id] = ui._after_run_compaction.begin()
        ui._session.permissions.set_request_handler(lambda request: self.receive(SessionEvent(
            batch.session_id, batch.generation, '', 'permission_request', request, batch.id, batch.request_ids)))

    async def finished(self, batch: TurnBatch, result: TurnResult) -> None:
        ui = self.ui
        producer = self.receipts.pop(batch.id, None)
        if producer is not None:
            success = result.status == 'succeeded'
            ui._after_run_compaction.finish(producer, success)
            ui._after_run_compaction.worker_finished(producer.generation, success)
        undelivered = [dict(id=item.id, content=item.content) for item in ui._session.agent.drain_inputs()]
        if undelivered:
            ui._event_queue.put({'type': 'inputs_settled', 'inputs': undelivered})
        ui._session.agent.drain_handled_inputs()
        ui._session._expire_turn_scoped_skills()
        ui._worker_loop_ref = None
        ui._worker_task = None
        # Only an actual reviewed follow-up holds automatic work. Rendering a
        # completed turn must never be a scheduling acknowledgement.
        if any(getattr(helper.queue, '_pending', None) is not None for helper in
               (ui._after_run_compaction, ui._after_run_tools, ui._after_run_name)):
            token = 'native-' + batch.id
            self.runtime.controller.hold_producer(token)
            with self.lock:
                self.holds.add(token)

    def receive(self, event: SessionEvent) -> None:
        if event.kind == 'agent_event':
            data = _snapshot_agent_event(event.data)
            if data is None:
                return
            event = replace(event, data=data)
        if event.kind == 'input_running':
            with self.lock:
                item = self.local.get(event.request_id)
                if item is not None:
                    item.started = True
        self.ui._event_queue.put({'type': 'session_event', 'event': event})

    def enqueue(self, text: str, images: tuple[Any, ...], origin: str, *, echo: bool = True) -> Submission:
        """Submit immediately, including while another turn is executing."""
        self.start()
        identifier = uuid.uuid4().hex
        self.queue_sequence += 1
        item = _NativeInput(text, images, origin, echo, f'q{self.queue_sequence}')
        with self.lock:
            self.local[identifier] = item
        value = SessionInput(identifier, 'human' if origin == 'user' else 'automatic', 'coding TUI',
                             text if text.strip() else '[Image/context input]', (), {})
        ticket: Submission = self.runtime.controller.submit(
            value, peer_id='native', images=self.ui._image_parts(images), input_origin=origin, raw_text=text)
        def admitted(done: Any) -> None:
            try:
                done.result()
            except BaseException as exc:
                self.ui._event_queue.put({'type': 'native_admission_failed', 'id': identifier, 'error': str(exc)})
        ticket.accepted.add_done_callback(admitted)
        self.refresh_queue()
        return ticket

    async def submit(self, text: str, images: tuple[Any, ...], origin: str) -> TurnResult:
        ticket = self.enqueue(text, images, origin)
        try:
            await asyncio.wrap_future(ticket.accepted)
            return await asyncio.wrap_future(ticket.result)
        except asyncio.CancelledError:
            await asyncio.wrap_future(self.runtime.controller.cancel(ticket.id, peer_id='native', wait=True))
            raise

    def refresh_queue(self) -> None:
        """Compatibility view for composer/queue rendering; never dequeued for execution."""
        ui = self.ui
        with self.lock, ui._queue_lock:
            pending = [item for item in self.local.values() if not item.started]
            ui._pending_messages = deque((item.text, item.echo) for item in pending)
            ui._pending_ids.ids = deque(item.queue_id for item in pending)
            ui._local_images = {item.queue_id: item.images for item in pending}
        ui._update_message_queue()

    def change(self, key: str, action: str, content: str, direction: int) -> bool:
        with self.lock:
            identifier = next((identifier for identifier, item in self.local.items()
                               if item.queue_id == key), None)
        if identifier is None or self.runtime is None:
            return False
        future = self.runtime.controller.change_pending(identifier, peer_id='native', action=action,
                                                        content=content, direction=direction)
        def changed(done: Any) -> None:
            try:
                success = done.result()
                error = 'Input already selected/consumed or at queue boundary'
            except BaseException as exc:
                success, error = False, str(exc)
            self.ui._event_queue.put({'type': 'native_queue_ack', 'id': identifier, 'action': action,
                                      'content': content, 'success': success, 'error': error})
        future.add_done_callback(changed)
        return True

    def cancel_local(self) -> None:
        if self.runtime is None:
            return
        with self.lock:
            identifiers = tuple(self.local)
            self.cancelled.update(identifiers)
        for identifier in identifiers:
            self.runtime.controller.cancel(identifier, peer_id='native')
        if self.runtime.controller.current_request not in {None, *identifiers}:
            # Escape is a trusted local session abort, unlike a remote peer's
            # scoped cancellation. It also owns pre-turn work before Agent.prompt.
            self.runtime.controller.abort()

    def poll(self) -> bool:
        if self.runtime is None:
            return False
        ui = self.ui
        draft = bool(ui._editor.get_text().strip() or ui._composer.items)
        self.runtime.controller.set_human_pending(draft)
        producer_busy = (self.runtime.controller.current_request is not None or ui._command is not None
            or ui._child_image_starting or ui._session.children.supervisor.active
            or ui._session.permissions.has_pending_requests or ui._session._compacting
            or ui._session._auth_changing or ui._session._context_changes
            or bool(getattr(ui._terminal_worker, 'busy', False)))
        reviewed = any(getattr(helper.queue, '_pending', None) is not None for helper in
                       (ui._after_run_compaction, ui._after_run_tools, ui._after_run_name))
        if not producer_busy and not reviewed:
            with self.lock:
                tokens, self.holds = self.holds, set()
            for token in tokens:
                self.runtime.controller.release_producer(token)
        return False

    def display(self, event: SessionEvent) -> None:
        if (self.runtime is None or event.generation != self.runtime.controller.generation
                or event.session_id != self.ui._session.session_id):
            return
        if event.kind == 'inbox_error':
            self.ui._chat_log.add_system('Inbox refresh unavailable: ' + str(event.data))
            return
        ui = self.ui
        if event.kind == 'permission_request' and isinstance(event.data, PermissionRequest):
            request = event.data

            def alive() -> bool:
                active = self.runtime.controller._batch
                return (active is not None and active.id == event.batch_id
                    and active.generation == event.generation
                    and ui._session.permissions.has_pending(request.id))

            if alive():
                ui._show_permission_request(request, alive=alive)
            return
        key = event.generation, event.request_id
        if key in self.completed or (event.kind == 'agent_event'
                and (event.generation, event.batch_id) in self.completed_batches):
            return
        self.presenter.apply(event)
        item = self.local.get(event.request_id)
        if event.kind == 'input_running':
            if self.display_batch != event.batch_id:
                self.display_batch = event.batch_id
                self.display_members = set(event.request_ids or (event.request_id,))
            ui._worker_active = True
            ui._cancel_requested = False
            ui._dispatch_images = item.images if item else ()
            ui._dispatch_input_origin = item.origin if item else 'session_input'
            ui._active_images = ui._dispatch_images
            ui._active_input_origin = ui._dispatch_input_origin
            if item:
                item.started = True
                if item.echo:
                    ui._chat_log.add_user(item.text + (f'\n[Images: {len(item.images)}]' if item.images else ''))
            self.refresh_queue()
            ui._set_busy(True)
        elif event.kind == 'agent_event':
            if self.display_batch != event.batch_id or not self.display_members - self.cancelled:
                return
            sequence = getattr(event.data, 'sequence', 0)
            if sequence:
                if sequence <= self.stream_sequences.get(event.batch_id, 0):
                    return
                self.stream_sequences[event.batch_id] = sequence
            # Projection and consumption happen on the terminal thread in FIFO
            # order. A fast successor cannot invalidate its predecessor's text.
            if isinstance(event.data, _AgentUiProjection):
                ui._handle_ui_event({**event.data.payload, 'stamp': ui._active_stamp})
            else:
                ui._turn_event_sink = ui._handle_ui_event
                try:
                    ui._dispatch_agent_event(event.data)
                finally:
                    ui._turn_event_sink = None
        elif event.kind == 'input_edited' and item is not None and isinstance(event.data, SessionInput):
            item.text = event.data.instruction
            self.refresh_queue()
        elif event.kind == 'input_moved' and item is not None and isinstance(event.data, int):
            with self.lock:
                keys = list(self.local)
                pending = [key for key in keys if not self.local[key].started]
                index = pending.index(event.request_id)
                target = index + int(event.data)
                if 0 <= target < len(pending):
                    a, b = keys.index(event.request_id), keys.index(pending[target])
                    keys[a], keys[b] = keys[b], keys[a]
                    self.local = {key: self.local[key] for key in keys}
            self.refresh_queue()
        elif event.kind == 'input_completed' and isinstance(event.data, TurnResult):
            self.completed.add(key)
            with self.lock:
                self.local.pop(event.request_id, None)
                self.cancelled.discard(event.request_id)
            if self.display_batch == event.batch_id:
                self.display_members.discard(event.request_id)
            if self.display_batch == event.batch_id and not self.display_members:
                self.completed_batches.add((event.generation, event.batch_id))
                self.stream_sequences.pop(event.batch_id, None)
                ui._worker_active = any(value.origin == 'user' for value in self.local.values())
                ui._active_images = ()
                ui._set_busy(ui._worker_active)
                ui._finalize_streaming_assistant()
                ui._update_footer()
                ui._update_header()
                self.display_batch = None
            if event.data.status == 'failed' and item is not None:
                ui._chat_log.add_system('Session task failed: ' + event.data.error)
            if (item and item.images and item.origin == 'user'
                    and (event.data.status == 'failed' or event.data.status == 'cancelled' and item.started)):
                ui._composer.restore(item.images)
                ui._chat_log.add_system('Images kept for explicit retry; input may already be in context')
            self.refresh_queue()

    def admission_failed(self, identifier: str, error: str) -> None:
        item = self.local.pop(identifier, None)
        if item is not None:
            self.ui._composer.restore(item.images)
            self.ui._preserve_input(item.text, 'Input not admitted: ' + error)
        self.refresh_queue()
        if not self.local and self.display_batch is None:
            self.ui._worker_active = False
            self.ui._set_busy(False)

    def close(self) -> None:
        if self.unsubscribe is not None:
            self.unsubscribe()
            self.unsubscribe = None
        if self.runtime is not None:
            self.runtime.close().result(timeout=15)
            thread = self.runtime.controller._thread
            if thread is not None and thread is not threading.current_thread():
                thread.join(timeout=5)
                if thread.is_alive():
                    raise RuntimeError('Session owner thread did not stop after resource settlement')
