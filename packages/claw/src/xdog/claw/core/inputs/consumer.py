"""Application-owned durable consumption; an injected host executes existing Agent turns."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from xdog.agent.async_work import wait_owned
from xdog.claw.core.inputs.models import InputEnvelope, InputScope
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

    def __init__(self, store: InputStore, execute: Execute) -> None:
        self.store, self.execute = store, execute
        self._signals: dict[InputScope, asyncio.Event] = {}
        self._tasks: dict[InputScope, asyncio.Task[None]] = {}
        self._waiters: dict[tuple[InputScope, str], set[asyncio.Future[None]]] = {}
        self._faults: dict[InputScope, Exception] = {}
        self._closing = False
        self._close_task: asyncio.Task[None] | None = None

    def wake(self, scope: InputScope) -> None:
        if self._closing or scope in self._faults:
            return
        signal = self._signals.setdefault(scope, asyncio.Event())
        signal.set()
        if scope not in self._tasks:
            task = asyncio.create_task(self._consume(scope), name=f'claw-input:{scope.group_id}')
            self._tasks[scope] = task
            task.add_done_callback(lambda done: self._done(scope, done))

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

    async def submit(self, envelope: InputEnvelope) -> tuple[InputRecord, bool]:
        if self._closing:
            raise RuntimeError('Application input service is closing')
        if envelope.scope in self._faults:
            error = self._faults[envelope.scope]
            raise RuntimeError('Application input owner is unavailable; inspect receipts') from error
        try:
            return await self.store.enqueue(envelope)
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
        record = await self.store.cancel(scope, message_id)
        self._notify(scope, message_id)
        return record

    def _notify(self, scope: InputScope, identifier: str | None = None) -> None:
        for (owner, message_id), waiters in tuple(self._waiters.items()):
            if owner == scope and (identifier is None or identifier == message_id):
                for waiter in tuple(waiters):
                    if not waiter.done():
                        waiter.set_result(None)

    async def _admit(self, record: InputRecord, session_id: str = '') -> bool:
        try:
            await self.store.start(record, session_id=session_id)
            return True
        except ValueError:
            current = await self.store.get(record.envelope.scope, record.envelope.message.id)
            if current is not None and current.status == 'cancelled' and current.attempt_id == record.attempt_id:
                return False
            raise

    async def _execute(self, record: InputRecord) -> None:
        admitted = False
        async def admit(session_id: str = '') -> bool:
            nonlocal admitted
            if admitted:
                raise ValueError('Execution attempt already passed admission')
            admitted = await self._admit(record, session_id)
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
            error = str(exc) or type(exc).__name__
            outcome = InputOutcome(error=error)
        current = await self.store.get(record.envelope.scope, record.envelope.message.id)
        if current is None:
            raise ValueError('Accepted input disappeared during execution')
        if current.status == 'cancelled':
            return
        if not admitted:
            await self.store.reject(record, error)
        else:
            await self.store.finish(record, 'failed' if error else 'succeeded', response=outcome.response,
                                    error=error, session_id=outcome.session_id)

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
            await self.store.finish(current, 'interrupted', error='Application stopped; inspect prior effects')
        # A terminal commit can finish while cancellation loses its result. Its
        # actual receipt wins; never overwrite success with interrupted.
        self._notify(scope, current.envelope.message.id)

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
                    await self._execute(record)
                    self._notify(scope, record.envelope.message.id)
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
            self._notify(scope)

    async def close(self) -> None:
        if self._close_task is None:
            self._closing = True
            self._close_task = asyncio.create_task(self._close())
        await wait_owned(self._close_task)

    async def _close(self) -> None:
        tasks = tuple(self._tasks.values())
        for task in tasks:
            task.cancel()
        try:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, BaseException) and not isinstance(result, asyncio.CancelledError):
                    raise result
        finally:
            for scope in self._signals:
                self._notify(scope)
            await self.store.close()
