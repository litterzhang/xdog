"""Application-owned durable consumption; an injected host executes existing Agent turns."""
from __future__ import annotations

import asyncio
import copy
import logging
import uuid
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager, nullcontext
from dataclasses import dataclass
from typing import Any, Protocol

from xdog.agent.async_work import wait_owned
from xdog.claw.core.inputs.models import InputEnvelope, InputScope
from xdog.claw.core.inputs.receipt_projection import project, summarize_error
from xdog.claw.core.inputs.store import InputRecord, InputStore

logger = logging.getLogger(__name__)
_TERMINAL = frozenset({'succeeded', 'failed', 'interrupted', 'cancelled'})
class Admit(Protocol):
    async def __call__(self, session_id: str = '') -> bool: ...


@dataclass(frozen=True)
class InputOutcome:
    response: str = ''
    error: str = ''
    session_id: str = ''


Execute = Callable[[InputRecord, Admit], Awaitable[InputOutcome]]


class InputService:
    """One consumer per scope, sharing the host's original execution exclusion.

    The executor must invoke admit inside that exclusion before calling Agent.
    No protocol, UI, observer, Goal state or model/tool loop belongs here.
    """

    def __init__(self, store: InputStore, execute: Execute, *,
                 on_settled: Callable[[InputScope], None] | None = None, paused: bool = False,
                 execution_scope: Callable[[InputRecord], AbstractAsyncContextManager[None]] | None = None,
                 on_admitted: Callable[[InputRecord, str], None] | None = None,
                 snapshot: Callable[[InputScope], dict[str, Any]] | None = None,
                 acp_target: Callable[[InputScope], Awaitable[dict[str, Any] | None]] | None = None) -> None:
        self.store, self.execute = store, execute
        try:
            self._loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None
        self.on_settled = on_settled
        self.execution_scope, self.on_admitted = execution_scope, on_admitted
        self._owner_snapshot, self._acp_target = snapshot, acp_target
        self._generation = uuid.uuid4().hex
        self._execution_listeners: dict[InputScope, list[Callable[[dict[str, Any]], None]]] = {}
        self._human_pending: set[tuple[InputScope, str]] = set()
        self._settled_events: set[tuple[InputScope, str, str, str]] = set()
        self._paused = paused
        self._signals: dict[InputScope, asyncio.Event] = {}
        self._tasks: dict[InputScope, asyncio.Task[None]] = {}
        self._waiters: dict[tuple[InputScope, str], set[asyncio.Future[None]]] = {}
        self._faults: dict[InputScope, Exception] = {}
        self._pending: set[tuple[InputScope, str]] = set()
        self._active: set[InputScope] = set()
        self._completed: set[Callable[[InputRecord], None]] = set()
        self._closing = False
        self._close_task: asyncio.Task[None] | None = None

    @property
    def accepting(self) -> bool:
        """Whether this owner still accepts input; terminal hints may outlive intake."""
        return not self._closing

    def subscribe_completed(self, callback: Callable[[InputRecord], None]) -> Callable[[], None]:
        self._completed.add(callback)
        return lambda: self._completed.discard(callback)

    def execution_snapshot(self, scope: InputScope) -> dict[str, Any]:
        snapshot = (dict(self._owner_snapshot(scope)) if self._owner_snapshot is not None else
                    {'state': 'idle' if scope in self._signals else 'unavailable',
                     'user_pending': False, 'children_active': False, 'generation': self._generation})
        if self._closing:
            snapshot['state'] = 'closing'
        elif scope in self._faults:
            snapshot['state'] = 'unavailable'
        elif scope in self._active and snapshot['state'] != 'unavailable':
            snapshot['state'] = 'running'
        snapshot['user_pending'] = snapshot['user_pending'] or any(owner == scope for owner, _ in self._human_pending)
        return snapshot

    def subscribe_events(self, scope: InputScope, callback: Callable[[dict[str, Any]], None]) -> Callable[[], None]:
        callbacks = self._execution_listeners.setdefault(scope, [])
        callbacks.append(callback)
        self.publish_available(scope)
        def cancel() -> None:
            if callback in callbacks:
                callbacks.remove(callback)
            if not callbacks:
                self._execution_listeners.pop(scope, None)
        return cancel

    def _execution_event(self, scope: InputScope, kind: str, *, execution_id: str = '', status: str = '',
                         automatic: bool | None = None) -> None:
        try:
            same_loop = asyncio.get_running_loop() is self._loop
        except RuntimeError:
            same_loop = False
        if self._loop is None:
            self._loop = asyncio.get_running_loop()
            same_loop = True
        if not same_loop:
            if not self._loop.is_closed():
                self._loop.call_soon_threadsafe(lambda: self._execution_event(scope, kind,
                    execution_id=execution_id, status=status, automatic=automatic))
            return
        snapshot = self.execution_snapshot(scope)
        value: dict[str, Any] = {'type': kind, 'execution_id': execution_id, 'status': status,
                                 'generation': snapshot['generation'], 'snapshot': snapshot}
        if automatic is not None:
            value['automatic'] = automatic
        for callback in tuple(self._execution_listeners.get(scope, ())):
            try:
                callback(copy.deepcopy(value))
            except Exception:
                logger.warning('Committed execution listener failed', exc_info=True)

    def publish_available(self, scope: InputScope) -> None:
        self._execution_event(scope, 'owner.retiring' if self._closing else 'owner.available')

    async def acp_target(self, scope: InputScope) -> dict[str, Any] | None:
        if self._closing or self._acp_target is None:
            return None
        return await self._acp_target(scope)

    def wake(self, scope: InputScope) -> None:
        if self._closing or scope in self._faults:
            return
        signal = self._signals.setdefault(scope, asyncio.Event())
        signal.set()
        if not self._paused and scope not in self._tasks:
            task = asyncio.create_task(self._consume(scope), name=f'claw-input:{scope.group_id}')
            self._tasks[scope] = task
            task.add_done_callback(lambda done: self._done(scope, done))

    def resume(self) -> None:
        if self._closing:
            raise RuntimeError('Application input service is closing')
        self._paused = False
        for scope in tuple(self._signals):
            self.wake(scope)

    def _done(self, scope: InputScope, task: asyncio.Task[None]) -> None:
        if self._tasks.get(scope) is task:
            del self._tasks[scope]
        if not task.cancelled():
            failure = task.exception()
            if failure is not None:
                self._faults[scope] = (failure if isinstance(failure, Exception)
                                       else RuntimeError('Input owner settlement failed'))
                self._notify(scope)
        if not self._closing and scope not in self._faults and task.cancelled():
            # Execution cancellation settles its own receipt; it does not own
            # separately accepted following messages or authorize a retry.
            self.wake(scope)

    def has_work(self, scope: InputScope) -> bool:
        return (scope in self._active or scope in self._faults
                or any(owner == scope for owner, _identifier in self._pending))

    async def activate(self, scope: InputScope, *, wake: bool = True) -> None:
        await self.store.bind_scope(scope)
        for record in await self.store.records(scope):
            if record.status not in _TERMINAL:
                self._pending.add((scope, record.envelope.message.id))
                if record.envelope.human_authorized:
                    self._human_pending.add((scope, record.envelope.message.id))
        if wake:
            self.wake(scope)

    async def submit(self, envelope: InputEnvelope, *, max_pending: int | None = None) -> tuple[InputRecord, bool]:
        if self._closing:
            raise RuntimeError('Application input service is closing')
        if envelope.scope in self._faults:
            error = self._faults[envelope.scope]
            raise RuntimeError('Application input owner is unavailable; inspect receipts') from error
        try:
            result = await self.store.enqueue(envelope, max_pending=max_pending)
            if result[0].status not in _TERMINAL:
                self._pending.add((envelope.scope, envelope.message.id))
                if result[0].envelope.human_authorized:
                    self._human_pending.add((envelope.scope, envelope.message.id))
            return result
        finally:
            # An ACK can be lost after a real commit; the application, not the
            # awaiting connection, owns subsequent processing.
            self.wake(envelope.scope)

    async def wait(self, scope: InputScope, message_id: str) -> InputRecord:
        key = scope, message_id
        signal: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        waiters = self._waiters.setdefault(key, set())
        waiters.add(signal)
        try:
            while True:
                record = await self.store.get(scope, message_id)
                if record is None:
                    raise ValueError('Unknown accepted input in this scope')
                if record.status in _TERMINAL:
                    return record
                if scope in self._faults:
                    raise RuntimeError('Application input consumer failed; inspect receipts') from self._faults[scope]
                if self._closing:
                    raise RuntimeError('Application stopped before input completed; inspect durable receipt')
                await asyncio.shield(signal)
                signal = asyncio.get_running_loop().create_future()
                waiters.add(signal)
        finally:
            for pending in tuple(waiters):
                if pending.done():
                    waiters.discard(pending)
            waiters.discard(signal)
            if not signal.done():
                signal.cancel()
            if not waiters:
                self._waiters.pop(key, None)

    async def withdraw(self, scope: InputScope, message_id: str) -> InputRecord:
        async def commit() -> InputRecord:
            record = await self.store.cancel(scope, message_id)
            self._pending.discard((scope, message_id))
            self._notify(scope, message_id)
            self._publish_terminal(record)
            return record
        # A lost protocol/caller ACK cannot leave an actually cancelled input
        # occupying application capacity or suppressing the next idle operation.
        return await wait_owned(asyncio.create_task(commit()))

    def _publish_terminal(self, record: InputRecord) -> None:
        if record.status not in _TERMINAL:
            return
        scope, identifier = record.envelope.scope, record.envelope.message.id
        self._human_pending.discard((scope, identifier))
        key = scope, identifier, record.attempt_id, record.status
        if key not in self._settled_events:
            self._settled_events.add(key)
            self._execution_event(scope, 'execution.settled', execution_id=record.attempt_id or identifier,
                                  status=record.status, automatic=not record.envelope.human_authorized)
        if self.on_settled is not None:
            try:
                self.on_settled(record.envelope.scope)
            except Exception:
                logger.warning('Input result committed; optional settled hint failed', exc_info=True)
        for callback in tuple(self._completed):
            try:
                callback(record)
            except Exception:
                logger.warning('Committed input display listener failed', exc_info=True)

    def _notify(self, scope: InputScope, identifier: str | None = None) -> None:
        for (owner, message_id), waiters in tuple(self._waiters.items()):
            if owner == scope and (identifier is None or identifier == message_id):
                for waiter in tuple(waiters):
                    if not waiter.done():
                        waiter.set_result(None)

    async def _admit(self, record: InputRecord, session_id: str = '') -> InputRecord | None:
        try:
            return await self.store.start(record, session_id=session_id)
        except ValueError:
            current = await self.store.get(record.envelope.scope, record.envelope.message.id)
            if current is not None and current.status == 'cancelled' and current.attempt_id == record.attempt_id:
                return None
            raise

    async def _execute(self, record: InputRecord) -> InputRecord:
        admitted = False
        async def admit(session_id: str = '') -> bool:
            nonlocal admitted
            if admitted:
                raise ValueError('Execution attempt already passed admission')
            started = await self._admit(record, session_id)
            admitted = started is not None
            if started is not None and self.on_admitted is not None:
                self.on_admitted(started, session_id)
            return admitted
        try:
            outcome = await self.execute(record, admit)
            if not isinstance(outcome, InputOutcome):
                raise TypeError('Executor returned no canonical input outcome')
            error = outcome.error
            if not admitted:
                error = error or 'Executor returned without Agent admission'
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            error = summarize_error(exc)
            outcome = InputOutcome(error=error)
        current = await self.store.get(record.envelope.scope, record.envelope.message.id)
        if current is None:
            raise ValueError('Accepted input disappeared during execution')
        if current.status == 'cancelled':
            return current
        if not admitted:
            return await self.store.reject(record, error)
        session_id = outcome.session_id or current.session_id
        response, receipt_error = project(outcome.response, error, session_id)
        return await self.store.finish(record, 'failed' if error else 'succeeded', response=response,
                                       error=receipt_error, session_id=session_id)

    async def _settle(self, scope: InputScope, record: InputRecord | None,
                      claim: asyncio.Task[InputRecord | None] | None) -> None:
        if record is None and claim is not None:
            record = await wait_owned(claim)
        if record is None:
            return
        current = await self.store.get(scope, record.envelope.message.id)
        if current is None:
            raise ValueError('Claimed receipt disappeared during owner settlement')
        if current.status == 'claimed' and not self._closing:
            await self.store.reject(current, 'Execution cancelled before admission; explicit retry required')
        elif current.status == 'claimed':
            try:
                await self.store.release(current)
            except ValueError:
                latest = await self.store.get(scope, current.envelope.message.id)
                if (latest is None or latest.status != 'cancelled'
                        or latest.attempt_id != current.attempt_id):
                    raise
                # An explicit withdrawal won this same unstarted attempt.
                # Storage errors and a different/new attempt still fail closed.
        elif current.status == 'running':
            await self.store.finish(current, 'interrupted', error='Execution interrupted; inspect prior effects')
        # A terminal commit can finish while cancellation loses its result. Its
        # actual receipt wins; never overwrite success with interrupted.
        if not self._closing:
            self._pending.discard((scope, current.envelope.message.id))
        self._notify(scope, current.envelope.message.id)
        settled = await self.store.get(scope, current.envelope.message.id)
        if settled is not None:
            self._publish_terminal(settled)

    async def _consume(self, scope: InputScope) -> None:
        record: InputRecord | None = None
        claim: asyncio.Task[InputRecord | None] | None = None
        try:
            while not self._closing:
                self._signals[scope].clear()
                while not self._closing:
                    claim = asyncio.create_task(self.store.claim(scope))
                    record = await asyncio.shield(claim)
                    if record is None:
                        break
                    self._active.add(scope)
                    owner = self.execution_scope(record) if self.execution_scope is not None else nullcontext()
                    async with owner:
                        try:
                            completed = await self._execute(record)
                        except asyncio.CancelledError:
                            # The original producer owns its durable interrupt
                            # commit too, not just the model/tool invocation.
                            await wait_owned(asyncio.create_task(self._settle(scope, record, claim)))
                            record, claim = None, None
                            raise
                    self._pending.discard((scope, record.envelope.message.id))
                    self._active.discard(scope)
                    self._notify(scope, record.envelope.message.id)
                    self._publish_terminal(completed)
                    self.publish_available(scope)
                    record, claim = None, None
                if self._closing:
                    break
                await self._signals[scope].wait()
        except asyncio.CancelledError:
            await wait_owned(asyncio.create_task(self._settle(scope, record, claim)))
            raise
        except Exception as exc:
            self._faults[scope] = exc
            logger.exception('Application input owner stopped for %s', scope.group_id)
        finally:
            self._active.discard(scope)
            self._notify(scope)
            self.publish_available(scope)

    def request_stop(self) -> None:
        """Stop admission/signal real execution now; close joins before releasing IO."""
        self._closing = True
        for scope in tuple(self._execution_listeners):
            self.publish_available(scope)
        for task in tuple(self._tasks.values()):
            if not task.done() and not task.cancelling():
                task.cancel()

    async def close(self) -> None:
        if self._close_task is None:
            self.request_stop()
            self._close_task = asyncio.create_task(self._close())
        await wait_owned(self._close_task)

    async def _close(self) -> None:
        tasks = tuple(self._tasks.values())
        # request_stop already signalled each original producer. Cancelling it
        # again here could interrupt its actual tool/Agent cleanup and release
        # group ownership before those effects have settled.
        try:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, BaseException) and not isinstance(result, asyncio.CancelledError):
                    raise result
        finally:
            for scope in self._signals:
                self._notify(scope)
            await self.store.close()
