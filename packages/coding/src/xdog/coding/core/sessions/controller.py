"""Single session execution owner with thread-safe submission and correlated events."""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from collections import deque
from collections.abc import Awaitable, Callable
from concurrent.futures import Future
from contextvars import ContextVar
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, TypeVar

from xdog.agent.session_input import MAX_TRANSPORT_OVERHEAD, SessionInput, decode_input, input_data
from xdog.ai.types import ImageContent
from xdog.coding.config import validate_max_messages_per_turn
from xdog.coding.core.sessions.batches import (
    MAX_BATCH_BYTES,
    TurnBatch,
    TurnInput,
    available_input_tokens,
    input_size,
    member_tokens,
)
from xdog.coding.core.sessions.models import InputRecord, SessionEvent, Submission, TurnResult
from xdog.coding.core.sessions.store import InputStore

if TYPE_CHECKING:
    from xdog.coding.core.agent_session import AgentSession
    from xdog.coding.core.sessions.inbox_runtime import InboxCoordinator

logger = logging.getLogger(__name__)
T = TypeVar("T")
MAX_PENDING = 128
_OWNER: ContextVar[tuple[object, str] | None] = ContextVar("coding_session_owner", default=None)


@dataclass
class _Job:
    value: SessionInput
    peer: str
    ticket: Submission
    branch: str
    generation: str
    finishing: bool = False
    cancelled: bool = False
    images: tuple[ImageContent, ...] = ()
    input_origin: str | None = None
    raw_text: str | None = None
    batch_id: str = ""


class SessionController:
    """Bind once to an async mode's loop, or own a loop thread for synchronous hosts."""

    def __init__(
        self,
        session: AgentSession,
        *,
        bound: bool,
        max_messages_per_turn: int | None = None,
        execute_batch: Callable[[TurnBatch], Awaitable[TurnResult]] | None = None,
    ) -> None:
        self.session = session
        self.bound = bound
        self.store = InputStore(session)
        self.generation = uuid.uuid4().hex
        self.branch = session.session_data.active_branch
        self._message_limit_override = (validate_max_messages_per_turn(max_messages_per_turn)
                                        if max_messages_per_turn is not None else None)
        self._execute_batch = execute_batch or self._default_execute_batch
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._stop: asyncio.Event | None = None
        self._start_lock = threading.Lock()
        self._jobs: dict[str, _Job] = {}
        self._controls: deque[tuple[Callable[[], Awaitable[Any]], str, Future[Any]]] = deque()
        self._admissions: set[asyncio.Task[None]] = set()
        self._admitting_jobs: dict[str, _Job] = {}
        self._listeners: list[Callable[[SessionEvent], None]] = []
        self._active: asyncio.Task[None] | None = None
        self._current: _Job | None = None
        self._revoked: set[str] = set()
        self._batch: TurnBatch | None = None
        self._batch_jobs: tuple[_Job, ...] = ()
        self._human_pending = False
        self._queue_changes = 0
        self._producers: set[str] = set()
        self._producer_receipts: dict[str, tuple[Future[None], Callable[[], None] | None]] = {}
        self._settlements: set[Future[None]] = set()
        self._owner_token: str | None = None
        self._control_future: Future[Any] | None = None
        self.on_control_settled: Callable[[], None] | None = None
        self._close_hook: Callable[[], Awaitable[None]] | None = None
        self.inbox: InboxCoordinator | None = None
        self._inputs_ready = True
        self.before_batch: Callable[[TurnBatch], Awaitable[None]] | None = None
        self.after_batch: Callable[[TurnBatch, TurnResult], Awaitable[None]] | None = None
        self._closing = False
        self._closed: Future[None] = Future()
        self._broken: BaseException | None = None
        self._readiness_timer: asyncio.TimerHandle | None = None

    def start(self, *, own_thread: bool = False) -> None:
        with self._start_lock:
            if self._loop is not None:
                return
            if self._closing:
                raise RuntimeError("Session controller is closed")
            try:
                if own_thread:
                    raise RuntimeError("Synchronous host owns a dedicated loop")
                self._loop = asyncio.get_running_loop()
                self._ready.set()
            except RuntimeError:
                self._thread = threading.Thread(target=self._thread_main, name="xdog-session-owner", daemon=True)
                self._thread.start()
                self._ready.wait()

    @property
    def max_messages_per_turn(self) -> int:
        return (self._message_limit_override if self._message_limit_override is not None else
                validate_max_messages_per_turn(self.session.settings.max_messages_per_turn))

    @max_messages_per_turn.setter
    def max_messages_per_turn(self, value: int) -> None:
        self._message_limit_override = validate_max_messages_per_turn(value)

    @property
    def busy(self) -> bool:
        return self._active is not None or bool(self._producers)

    @property
    def owns_current_task(self) -> bool:
        # asyncio.to_thread copies context. A trusted synchronous mutation can
        # retain the original reservation without opening a second control queue.
        scope = _OWNER.get()
        return scope is not None and scope[0] is self and scope[1] == self._owner_token

    @property
    def current_request(self) -> str | None:
        return self._current.value.id if self._current is not None else None

    def hold_producer(self, token: str) -> None:
        if self.owns_current_task:
            self._producers.add(token)
        else:
            self._post(lambda: self._producers.add(token))

    def release_producer(self, token: str) -> None:
        def release() -> None:
            self._producers.discard(token)
            self._pump()
        self._post(release)

    def reserve_producer(self, name: str, *, cancel: Callable[[], None] | None = None) -> Callable[[], None]:
        """Track callback settlement across loops, including during owner shutdown."""
        if self._closing:
            raise RuntimeError("Session controller is closed")
        token = name + '-' + uuid.uuid4().hex
        receipt: Future[None] = Future()
        def reserve() -> None:
            self._producers.add(token)
            self._producer_receipts[token] = receipt, cancel
        self._post(reserve)
        def release() -> None:
            if receipt.done():
                return
            def settled() -> None:
                self._producers.discard(token)
                self._producer_receipts.pop(token, None)
                self._settle(receipt, None)
                self._pump()
            self._post(settled)
        return release

    def settle_before_release(self, operation: Awaitable[None]) -> None:
        if not self.owns_current_task:
            raise RuntimeError("Settlement must be registered by the session owner")
        self._settlements.add(self._async(lambda: operation))

    async def _settle_operations(self) -> None:
        error: BaseException | None = None
        cancelled = False
        while self._settlements:
            tasks, self._settlements = self._settlements, set()
            receipt = asyncio.gather(*(asyncio.wrap_future(task) for task in tasks), return_exceptions=True)
            while not receipt.done():
                try:
                    await asyncio.shield(receipt)
                except asyncio.CancelledError:
                    cancelled = True
            for result in receipt.result():
                if isinstance(result, BaseException) and error is None:
                    error = result
        if cancelled:
            raise asyncio.CancelledError
        if error is not None:
            raise error

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._serve())
        except BaseException as exc:
            self._settle(self._closed, error=exc)
        finally:
            self._settle(self._closed, None)

    async def _serve(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._stop = asyncio.Event()
        self._ready.set()
        await self._stop.wait()

    def _post(self, callback: Callable[[], None]) -> None:
        self.start()
        assert self._loop is not None
        self._loop.call_soon_threadsafe(callback)

    @staticmethod
    def _settle(future: Future[T], value: T | None = None, *, error: BaseException | None = None) -> None:
        if future.done():
            return
        # Completion observers receive data, never the producer's execution authority.
        observer_scope = _OWNER.set(None)
        try:
            if error is not None:
                future.set_exception(error)
            else:
                future.set_result(value)  # type: ignore[arg-type]
        finally:
            _OWNER.reset(observer_scope)

    def subscribe(self, listener: Callable[[SessionEvent], None]) -> Callable[[], None]:
        self._listeners.append(listener)

        def unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsubscribe

    def _emit(self, kind: str, job: _Job, data: object) -> None:
        event = SessionEvent(self.session.session_id, job.generation, job.value.id, kind, data, job.batch_id,
                             self._batch.request_ids if self._in_active(job) and self._batch is not None else ())
        self._notify(event)

    def publish(self, kind: str, data: object) -> None:
        """Display-only state; never an executable submission or a permission grant."""
        self._notify(SessionEvent(self.session.session_id, self.generation, '', kind, data))

    def _notify(self, event: SessionEvent) -> None:
        observer_scope = _OWNER.set(None)
        try:
            for listener in tuple(self._listeners):
                try:
                    listener(event)
                except Exception:
                    logger.exception('Session event consumer failed')
        finally:
            _OWNER.reset(observer_scope)

    async def _default_execute_batch(self, batch: TurnBatch) -> TurnResult:
        from xdog.coding.core.sessions.turns import run_batch

        return await run_batch(self.session, batch, emit=lambda event: self._notify(SessionEvent(
            batch.session_id, batch.generation, '', 'agent_event', event, batch.id, batch.request_ids)))

    def _in_active(self, job: _Job) -> bool:
        return any(member is job for member in self._batch_jobs)

    def input_byte_limit(self, item: TurnInput) -> int:
        linked = (self.inbox is not None and self.inbox.is_submission(item.value.id)
                  and item.value.origin == 'acp' and not item.images and item.raw_text is None
                  and item.input_origin in {None, 'session_input'})
        return MAX_BATCH_BYTES + (MAX_TRANSPORT_OVERHEAD if linked else 0)

    def submit(self, value: SessionInput, *, peer_id: str, images: tuple[ImageContent, ...] = (),
               input_origin: str | None = None, raw_text: str | None = None) -> Submission:
        ticket = Submission(value.id, Future(), Future())
        try:
            if self._closing:
                raise RuntimeError("Session controller is closed")
            snapshot = decode_input(input_data(value))
            if any(not isinstance(image, ImageContent) for image in images):
                raise ValueError('Expected typed image attachments')
            if raw_text is not None and not isinstance(raw_text, str):
                raise ValueError('Expected message text')
            prepared = TurnInput(snapshot, images=tuple(images), input_origin=input_origin, raw_text=raw_text)
            count = input_size(prepared)
            if count > self.input_byte_limit(prepared):
                raise ValueError('Input exceeds per-turn byte capacity')
            job = _Job(snapshot, peer_id, ticket, self.branch, self.generation,
                       images=prepared.images, input_origin=input_origin, raw_text=raw_text)

            def spawn() -> None:
                task = asyncio.create_task(self._accept(job))
                self._admissions.add(task)
                task.add_done_callback(self._admissions.discard)

            self._post(spawn)
        except Exception as exc:
            self._settle(ticket.accepted, error=exc)
            self._settle(ticket.result, error=exc)
        return ticket

    async def _accept(self, job: _Job) -> None:
        try:
            if self._closing or job.peer in self._revoked:
                raise RuntimeError("Session connection is closed")
            if self._broken is not None:
                raise RuntimeError("Session persistence is unavailable") from self._broken
            if len(self._jobs) + len(self._admissions) > MAX_PENDING:
                raise ValueError("Session queue exceeds capacity")
            if job.value.id in self._jobs or job.value.id in self._admitting_jobs:
                raise ValueError("Duplicate request identity")
            record = InputRecord(job.value, job.branch, job.generation, "queued")
            self._admitting_jobs[job.value.id] = job
            await self.store.accept(record)
            self._jobs[job.value.id] = job
            self._emit("input_" + record.status, job, record)
            self._settle(job.ticket.accepted, None)
            if self._closing or job.peer in self._revoked or job.cancelled:
                await self._finish(job, TurnResult("cancelled", None))
            elif job.generation != self.generation:
                await self._finish(job, TurnResult("discarded", None))
            self._pump()
        except BaseException as exc:
            self._settle(job.ticket.accepted, error=exc)
            self._settle(job.ticket.result, error=exc)
        finally:
            if self._admitting_jobs.get(job.value.id) is job:
                self._admitting_jobs.pop(job.value.id, None)

    def _pump(self) -> None:
        if self._closing or self._active is not None or self._queue_changes or self._producer_receipts:
            return
        while self._controls:
            operation, name, future = self._controls.popleft()
            if not future.cancelled():
                self._control_future = future
                self._active = asyncio.create_task(self._run_control(operation, future))
                return
        if not self._inputs_ready:
            return
        jobs = [job for job in self._jobs.values() if not job.finishing]
        human = next((job for job in jobs if job.value.origin == "human"), None)
        if human is None and (self._human_pending or self._producers):
            return
        resource_busy = (not self.session.agent.is_idle or self.session.children.supervisor.active
            or self.session.permissions.has_pending_requests or self.session._compacting
            or self.session._auth_changing or self.session._context_changes
            or self.session._tool_selection_retiring or self.session._tool_selection_restore_pending)
        if jobs and resource_busy:
            if self._readiness_timer is None:
                assert self._loop is not None
                def retry() -> None:
                    self._readiness_timer = None
                    self._pump()
                self._readiness_timer = self._loop.call_later(0.05, retry)
            return
        ordered = [job for job in jobs if job.value.origin == 'human']
        ordered.extend(job for job in jobs if job.value.origin != 'human'
                       and not (self._human_pending or self._producers))
        message_limit = self.max_messages_per_turn
        selected: list[_Job] = []
        inputs: list[TurnInput] = []
        size = 0
        tokens = 0
        images = 0
        try:
            token_limit = available_input_tokens(self.session)
        except (ValueError, TypeError, AttributeError):
            token_limit = None  # Actual prompt preparation will report malformed/unsupported context.
        vision = self.session.model_info.vision_limits if self.session.model_info is not None else None
        image_limit = min(4, vision.max_prompt_images or 4) if vision is not None else 4
        for job in ordered:
            if selected and (job.branch != selected[0].branch or job.generation != selected[0].generation):
                break
            item = TurnInput(job.value, images=job.images, input_origin=job.input_origin, raw_text=job.raw_text)
            count = input_size(item)
            continuation = item.input_origin == 'custom_context'
            cost = 0 if continuation else member_tokens(item)
            if selected and (continuation or inputs[0].input_origin == 'custom_context'
                             or size + count > MAX_BATCH_BYTES
                             or token_limit is not None and tokens + cost > token_limit
                             or images + len(item.images) > image_limit):
                break
            selected.append(job)
            inputs.append(item)
            size += count
            tokens += cost
            images += len(item.images)
            if len(selected) >= message_limit or continuation:
                break
        if selected:
            batch = TurnBatch(uuid.uuid4().hex, self.session.session_id, selected[0].branch,
                              selected[0].generation, tuple(inputs))
            self._batch, self._batch_jobs = batch, tuple(selected)
            for member in selected:
                member.batch_id = batch.id
            self._current = selected[0]
            self._active = asyncio.create_task(self._run_batch(batch, tuple(selected)))

    async def _run_control(self, operation: Callable[[], Awaitable[Any]], future: Future[Any]) -> None:
        self._owner_token = uuid.uuid4().hex
        token = _OWNER.set((self, self._owner_token))
        try:
            result = await operation()
            await self._settle_operations()
            if self.on_control_settled is not None:
                self.on_control_settled()
            self._settle(future, result)
        except BaseException as exc:
            try:
                await self._settle_operations()
            except BaseException:
                logger.exception("Session control settlement failed")
            if self.on_control_settled is not None:
                self.on_control_settled()
            self._settle(future, error=exc)
        finally:
            self._control_future = None
            self._owner_token = None
            _OWNER.reset(token)
            self._active = None
            self._pump()

    async def _run_batch(self, batch: TurnBatch, jobs: tuple[_Job, ...]) -> None:
        self._owner_token = uuid.uuid4().hex
        token = _OWNER.set((self, self._owner_token))
        outcome = TurnResult('cancelled', None, batch_id=batch.id)
        prepared = False
        try:
            if self._broken is not None:
                raise RuntimeError('Session persistence is unavailable') from self._broken
            if batch.generation != self.generation:
                outcome = TurnResult('discarded', None, batch_id=batch.id)
            else:
                if self.inbox is not None:
                    await self.inbox.starting(batch)
                await self.store.transition_batch(batch.request_ids, 'running', batch_id=batch.id)
                prepared = True
                if self.before_batch is not None:
                    await self.before_batch(batch)
                for job in jobs:
                    self._emit('input_running', job, job.value)
                outcome = replace(await self._execute_batch(batch), batch_id=batch.id)
        except asyncio.CancelledError:
            if not self.session.agent.is_idle:
                await self.session.agent.cancel_and_wait()
        except Exception as exc:
            outcome = TurnResult('failed', None, str(exc), exc, batch_id=batch.id)
        finally:
            try:
                try:
                    if prepared and self.after_batch is not None:
                        await self.after_batch(batch, outcome)
                except asyncio.CancelledError:
                    if outcome.status != 'failed':
                        outcome = replace(outcome, status='cancelled', error='Batch finalization cancelled')
                except Exception as exc:
                    outcome = TurnResult('failed', outcome.response, str(exc), exc, batch_id=batch.id)
                try:
                    await self._settle_operations()
                except asyncio.CancelledError:
                    if outcome.status != 'failed':
                        outcome = replace(outcome, status='cancelled', error='Batch settlement cancelled')
                except Exception as exc:
                    outcome = TurnResult('failed', outcome.response, str(exc), exc, batch_id=batch.id)
                await self._finish_jobs(jobs, outcome, batch)
            finally:
                self._current = None
                self._batch, self._batch_jobs = None, ()
                self._owner_token = None
                _OWNER.reset(token)
                self._active = None
                self._pump()

    async def _finish(self, job: _Job, result: TurnResult) -> None:
        await self._finish_jobs((job,), result)

    async def _finish_jobs(self, jobs: tuple[_Job, ...], result: TurnResult, batch: TurnBatch | None = None) -> None:
        finishing = tuple(job for job in jobs if not job.finishing)
        if not finishing:
            return
        for job in finishing:
            job.finishing = True
        try:
            if self.inbox is not None:
                envelope = batch or TurnBatch('', self.session.session_id, finishing[0].branch,
                    finishing[0].generation, tuple(TurnInput(job.value) for job in finishing))
                await self.inbox.finished(envelope, result)
        except BaseException as exc:
            self._broken = exc
            result = replace(result, status='failed', error='Inbox persistence failed: ' + str(exc), cause=exc)
        try:
            await self.store.transition_batch(tuple(job.value.id for job in finishing), result.status,
                                              batch_id=result.batch_id, error=result.error)
        except BaseException as exc:
            self._broken = exc
            result = replace(result, status='failed', error='Session persistence failed: ' + str(exc), cause=exc)
        # All durable receipts settle before any member observes a completed result.
        for job in finishing:
            self._jobs.pop(job.value.id, None)
            self._emit('input_completed', job, replace(result, cause=None))
        for job in finishing:
            self._settle(job.ticket.result, result)

    def control(self, operation: Callable[[], Awaitable[T]], *, name: str) -> Future[T]:
        future: Future[T] = Future()

        def enqueue() -> None:
            if self._closing:
                self._settle(future, error=RuntimeError("Session controller is closed"))
                return
            self._controls.append((operation, name, future))
            self._pump()

        self._post(enqueue)
        def cancelled(done: Future[T]) -> None:
            if done.cancelled() and self._loop is not None and not self._loop.is_closed():
                def stop() -> None:
                    if self._control_future is done and self._active is not None:
                        self._active.cancel()
                self._loop.call_soon_threadsafe(stop)
        future.add_done_callback(cancelled)
        return future

    def cancel_control(self, future: Future[Any]) -> None:
        """Request cancellation without completing the caller's settlement receipt."""
        def stop() -> None:
            if self._control_future is future and self._active is not None:
                if not self._active.cancelling():
                    self._active.cancel()
            else:
                for item in tuple(self._controls):
                    if item[2] is future:
                        self._controls.remove(item)
                        self._settle(future, error=asyncio.CancelledError())
                        break
        self._post(stop)

    def human_input(self) -> None:
        def grant() -> None:
            self._pump()

        self._post(grant)

    def set_human_pending(self, pending: bool) -> None:
        def change() -> None:
            self._human_pending = pending
            self._pump()

        self._post(change)

    def _async(self, operation: Callable[[], Awaitable[T]]) -> Future[T]:
        self.start()
        assert self._loop is not None

        async def run() -> T:
            return await operation()

        return asyncio.run_coroutine_threadsafe(run(), self._loop)

    def change_pending(self, request_id: str, *, peer_id: str, action: str,
                       content: str = "", direction: int = 0) -> Future[bool]:
        """Change an unselected human submission on the owner loop, not a UI mirror."""
        async def run() -> bool:
            job = self._jobs.get(request_id)
            if (job is None or job.peer != peer_id or self._in_active(job) or job.finishing
                    or job.value.origin != "human"):
                return False
            self._queue_changes += 1
            try:
                if action == "remove":
                    await self._finish(job, TurnResult("cancelled", None))
                elif action == "edit":
                    changed = decode_input(input_data(replace(job.value, instruction=content)))
                    if not await self.store.edit_pending(changed) or self._jobs.get(request_id) is not job:
                        return False
                    job.value = changed
                    job.raw_text = content
                    self._emit("input_edited", job, changed)
                elif action == "move" and direction in {-1, 1}:
                    pending = [item for item in self._jobs.values() if not self._in_active(item)
                               and not item.finishing and item.value.origin == "human" and item.peer == peer_id]
                    position = pending.index(job)
                    target = position + direction
                    if not 0 <= target < len(pending):
                        return False
                    keys = list(self._jobs)
                    a, b = keys.index(request_id), keys.index(pending[target].value.id)
                    keys[a], keys[b] = keys[b], keys[a]
                    self._jobs = {key: self._jobs[key] for key in keys}
                    self._emit("input_moved", job, direction)
                else:
                    return False
                return True
            finally:
                self._queue_changes -= 1
                self._pump()
        return self._async(run)

    def cancel(self, request_id: str, *, peer_id: str, wait: bool = False) -> Future[bool]:
        async def run() -> bool:
            job = self._jobs.get(request_id)
            if job is None:
                job = self._admitting_jobs.get(request_id)
                if job is None or job.peer != peer_id:
                    return False
                job.cancelled = True
                if wait:
                    await asyncio.gather(asyncio.wrap_future(job.ticket.result), return_exceptions=True)
                return True
            if job.peer != peer_id:
                return False
            if self._in_active(job) and self._active is not None:
                if len(self._batch_jobs) > 1:
                    self._settle(job.ticket.result, TurnResult('cancelled', None,
                        'Waiter detached; shared batch execution continues', batch_id=job.batch_id,
                        execution_continues=True))
                    return True
                task = self._active
                if not task.cancelling():
                    task.cancel()
                if wait:
                    await asyncio.gather(task, return_exceptions=True)
            else:
                await self._finish(job, TurnResult("cancelled", None))
            return True

        return self._async(run)

    def disconnect(self, peer_id: str) -> Future[None]:
        async def run() -> None:
            self._revoked.add(peer_id)
            for job in tuple(self._jobs.values()):
                if job.peer != peer_id:
                    continue
                if self._in_active(job) and self._active is not None:
                    if len(self._batch_jobs) > 1:
                        self._settle(job.ticket.result, TurnResult('cancelled', None,
                            'Peer detached; shared batch execution continues', batch_id=job.batch_id,
                            execution_continues=True))
                    else:
                        if not self._active.cancelling():
                            self._active.cancel()
                        await asyncio.gather(self._active, return_exceptions=True)
                else:
                    await self._finish(job, TurnResult('cancelled', None))
            admissions = [asyncio.wrap_future(job.ticket.result) for job in self._admitting_jobs.values()
                          if job.peer == peer_id]
            if admissions:
                await asyncio.gather(*admissions, return_exceptions=True)
        return self._async(run)

    def rotate(self, branch_id: str) -> Future[None]:
        # Publish the fence before scheduling cleanup so a successor cannot start
        # on the already-mutated SessionData with the old generation.
        self.branch, self.generation = branch_id, uuid.uuid4().hex
        async def run() -> None:
            for job in tuple(self._jobs.values()):
                if not self._in_active(job) and job.generation != self.generation:
                    await self._finish(job, TurnResult("discarded", None))
        return self._async(run)

    def abort(self, *, pending_ids: tuple[str, ...] = (), peer_id: str = '') -> Future[None]:
        """Trusted local abort: settle the producer, optionally withdraw local queued work.

        This is deliberately separate from a remote peer detaching one waiter.
        The pump stays fenced until queued withdrawals and actual cleanup finish.
        """
        async def run() -> None:
            self._queue_changes += 1
            try:
                for key in pending_ids:
                    admitted = self._admitting_jobs.get(key)
                    if admitted is not None and admitted.peer == peer_id:
                        admitted.cancelled = True
                    job = self._jobs.get(key)
                    if job is not None and job.peer == peer_id and not self._in_active(job):
                        await self._finish(job, TurnResult('cancelled', None))
                if self._active is not None:
                    task = self._active
                    if not task.cancelling():
                        task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            finally:
                self._queue_changes -= 1
                self._pump()
        return self._async(run)

    def close(self) -> Future[None]:
        if self._closing:
            return self._closed
        self.start()
        self._closing = True
        self._async(self._shutdown)
        return self._closed

    async def _shutdown(self) -> None:
        if self._readiness_timer is not None:
            self._readiness_timer.cancel()
            self._readiness_timer = None
        try:
            if self._admissions:
                await asyncio.gather(*tuple(self._admissions), return_exceptions=True)
            if self._active is not None:
                task = self._active
                if not task.cancelling():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            for job in tuple(self._jobs.values()):
                await self._finish(job, TurnResult("cancelled", None))
            while self._controls:
                _, _, future = self._controls.popleft()
                self._settle(future, error=RuntimeError("Session controller closed"))
            await self._settle_operations()
            receipts = tuple(self._producer_receipts.values())
            for _, cancel in receipts:
                if cancel is not None:
                    cancel()
            if receipts:
                await asyncio.gather(*(asyncio.wrap_future(receipt) for receipt, _ in receipts))
            if self._close_hook is not None:
                await self._close_hook()
        except BaseException as exc:
            self._settle(self._closed, error=exc)
        finally:
            if self._stop is not None:
                self._stop.set()
            else:
                self._settle(self._closed, None)
