"""Native presentation adapter; the session controller owns every runnable input."""
from __future__ import annotations

import asyncio
import sys
import threading
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from xdog.agent.session_input import SessionInput
from xdog.coding.core.sessions.models import InputRecord, SessionEvent, Submission, TurnResult


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
        self.display_request: str | None = None
        self.queue_sequence = 0

    def start(self) -> None:
        if self.runtime is not None:
            return
        self.runtime = self.ui._session.enable_runtime(bound=True)
        self.runtime.start(own_thread=True)
        controller = self.runtime.controller
        controller.before_turn = self.prepare
        controller.after_turn = self.finished
        self.unsubscribe = controller.subscribe(self.receive)
        self.ui._session.permissions.set_request_handler(self.ui._on_permission_request)
        if sys.version_info < (3, 15):
            controller._async(self.runtime.open_endpoint).result(timeout=10)

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

    async def prepare(self, value: SessionInput) -> None:
        ui = self.ui
        ui._worker_loop_ref = asyncio.get_running_loop()
        ui._worker_task = asyncio.current_task()
        ui._bind_extension_dialogs()
        captured = ui._session.session_id
        ui._session.extensions.publish = lambda event: ui._event_queue.put({**event, 'extension_session': captured})
        self.receipts[value.id] = ui._after_run_compaction.begin()
        # Capture the originating turn, not a later global display stamp.
        stamp = ui._active_stamp
        ui._session.permissions.set_request_handler(
            lambda request: ui._put_turn_event('permission_request', stamp=stamp, request=request))

    async def finished(self, value: SessionInput, result: TurnResult) -> None:
        ui = self.ui
        producer = self.receipts.pop(value.id, None)
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
            token = 'native-' + value.id
            self.runtime.controller.hold_producer(token)
            with self.lock:
                self.holds.add(token)

    def receive(self, event: SessionEvent) -> None:
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
        if self.runtime is None or event.generation != self.runtime.controller.generation:
            return
        ui = self.ui
        item = self.local.get(event.request_id)
        if event.kind in {'input_queued', 'input_paused'} and isinstance(event.data, InputRecord):
            if item is None:
                value = event.data.input
                ui._chat_log.add_system(f'External input · {value.source} (self-reported, untrusted)\n'
                                        f'{value.instruction}\nStatus: {event.data.status}')
        elif event.kind == 'input_running':
            self.display_request = event.request_id
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
        elif event.kind == 'agent_event' and event.request_id not in self.cancelled:
            # Projection and consumption happen on the terminal thread in FIFO
            # order. A fast successor cannot invalidate its predecessor's text.
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
            with self.lock:
                self.local.pop(event.request_id, None)
                self.cancelled.discard(event.request_id)
            if self.display_request == event.request_id:
                ui._worker_active = any(value.origin == 'user' for value in self.local.values())
                ui._active_images = ()
                ui._set_busy(ui._worker_active)
                ui._finalize_streaming_assistant()
                ui._update_footer()
                ui._update_header()
                self.display_request = None
            if event.data.status == 'failed':
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
        if not self.local and self.display_request is None:
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
