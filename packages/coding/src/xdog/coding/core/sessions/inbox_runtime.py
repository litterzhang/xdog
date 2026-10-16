"""Explicit review and durable attempts owned by the existing session controller."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, TypeVar

from xdog.agent import AgentContext, BeforeToolCallContext
from xdog.agent.session_input import SessionInput
from xdog.ai.types import AssistantMessage, ToolCall
from xdog.coding.core.sessions.batches import TurnBatch
from xdog.coding.core.sessions.inbox_models import InboxRecord, encoded, message_data
from xdog.coding.core.sessions.inbox_store import InboxStore
from xdog.coding.core.sessions.models import Submission, TurnResult

if TYPE_CHECKING:
    from xdog.coding.core.sessions.runtime import SessionRuntime

T = TypeVar('T')


async def _settled(future: asyncio.Future[Any]) -> None:
    while not future.done():
        try:
            await asyncio.shield(future)
        except asyncio.CancelledError:
            pass
        except BaseException:
            break
    if future.done() and not future.cancelled():
        future.exception()  # Observe failures without replacing the caller's cancellation.


class InboxCoordinator:
    def __init__(self, runtime: SessionRuntime, store: InboxStore) -> None:
        self.runtime, self.store = runtime, store
        self.session_id, self.cwd = runtime.record.session_id, runtime.record.cwd
        self.generation, self.branch = runtime.controller.generation, runtime.controller.branch
        self.lease = runtime.session._lease
        self._ready = False
        self._initializing = asyncio.Lock()
        self._bindings: dict[str, InboxRecord] = {}
        self._reviewing: set[str] = set()
        self._reviews: set[asyncio.Task[Any]] = set()
        self._workers: set[asyncio.Task[Any]] = set()
        self._retired = False
        self._last: tuple[InboxRecord, ...] | None = None
        self._snapshot_sequence = 0
        self._published_sequence = -1

    def is_submission(self, request_id: str) -> bool:
        return request_id in self._bindings

    def _lease_owned(self, *, current: bool = True) -> None:
        if self.lease is None or self.lease._fd < 0 or self.lease.session_id != self.session_id:
            raise RuntimeError('Inbox execution requires its session writer lease')
        if current and self.runtime.session._lease is not self.lease:
            raise RuntimeError('Inbox owner lease changed')

    def _context(self) -> None:
        self._lease_owned()
        session, controller = self.runtime.session, self.runtime.controller
        if (self._retired or controller._closing or controller._broken is not None
                or session.session_id != self.session_id or session.working_dir.resolve() != self.cwd
                or controller.generation != self.generation or controller.branch != self.branch
                or session.session_data.active_branch != self.branch):
            raise RuntimeError('Inbox owner context/generation/branch changed or is unavailable')

    async def _owner(self, operation: Callable[[], Awaitable[T]]) -> T:
        controller = self.runtime.controller
        controller.start()
        if asyncio.get_running_loop() is controller._loop:
            return await operation()
        task: asyncio.Task[Any] | None = None
        cancelled = False

        async def run() -> T:
            nonlocal task
            task = asyncio.current_task()
            if cancelled:
                raise asyncio.CancelledError
            return await operation()

        receipt = asyncio.wrap_future(controller._async(run))
        try:
            return await asyncio.shield(receipt)
        except asyncio.CancelledError:
            cancelled = True

            def stop() -> None:
                if task is not None and not task.done():
                    task.cancel()

            assert controller._loop is not None
            controller._loop.call_soon_threadsafe(stop)
            await _settled(receipt)
            raise

    async def _disk(self, operation: Callable[[], T]) -> T:
        worker = asyncio.create_task(asyncio.to_thread(operation))
        self._workers.add(worker)
        worker.add_done_callback(self._workers.discard)
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            await _settled(worker)
            raise

    async def _initialize(self) -> None:
        self._context()
        async with self._initializing:
            if not self._ready:
                await self._disk(lambda: self.store.recover(self.session_id, self.generation))
                self._context()
                self._ready = True

    async def snapshot(self) -> tuple[InboxRecord, ...]:
        return await self._owner(self._snapshot)

    async def _snapshot(self) -> tuple[InboxRecord, ...]:
        await self._initialize()
        sequence, rows = await self._read_snapshot()
        self._context()
        return self._publish(rows, sequence)

    async def _read_snapshot(self) -> tuple[int, tuple[InboxRecord, ...]]:
        self._snapshot_sequence += 1
        sequence = self._snapshot_sequence
        return sequence, await self._disk(lambda: self.store.records(self.session_id, self.cwd))

    def _publish(self, rows: tuple[InboxRecord, ...], sequence: int) -> tuple[InboxRecord, ...]:
        controller = self.runtime.controller
        if (self._retired or controller.generation != self.generation
                or self.runtime.session.session_id != self.session_id):
            return rows
        if sequence < self._published_sequence:
            return self._last if self._last is not None else rows
        self._published_sequence = sequence
        if rows != self._last:
            self._last = rows
            self.runtime.controller.publish('inbox_snapshot', rows)
        return rows

    async def approve(self, message_ids: tuple[str, ...], *, peer_id: str) -> tuple[str, ...]:
        return await self._owner(lambda: self._review(message_ids, peer_id, retry=False))

    async def retry(self, message_id: str, *, peer_id: str) -> str:
        ids = await self._owner(lambda: self._review((message_id,), peer_id, retry=True))
        return ids[0]

    async def _review(self, ids: tuple[str, ...], peer: str, *, retry: bool) -> tuple[str, ...]:
        await self._initialize()
        if (not isinstance(ids, tuple) or not ids or any(not isinstance(key, str) for key in ids)
                or len(set(ids)) != len(ids)):
            raise ValueError('Inbox review requires distinct explicit message IDs')
        if self._reviewing.intersection(ids):
            raise ValueError('Inbox messages are already pending review')
        self._reviewing.update(ids)
        task = asyncio.current_task()
        assert task is not None
        self._reviews.add(task)
        try:
            rows = await self._disk(lambda: self.store.records(self.session_id, self.cwd))
            by_id = {row.message.message_id: row for row in rows}
            selected = []
            for key in ids:
                row = by_id.get(key)
                if row is None or row.status != ('interrupted' if retry else 'pending'):
                    raise ValueError('Inbox message is not pending this explicit review/retry')
                if row.branch_id != self.branch:
                    raise ValueError('Inbox message branch mismatch')
                selected.append(row)
            digests = {r.message.message_id: hashlib.sha256(encoded(message_data(r.message), 1024 * 1024)).hexdigest()
                       for r in selected}
            arguments = {'session_id': self.session_id, 'generation': self.generation, 'branch_id': self.branch,
                         'message_ids': list(ids), 'content_digests': digests, 'retry': retry, 'peer_id': peer}
            context = BeforeToolCallContext(assistant_message=AssistantMessage(),
                tool_call=ToolCall(id='inbox-review-' + ids[0], name='session_inbox_approve', arguments=arguments),
                args=arguments, context=AgentContext())
            verdict = await self.runtime.session.permissions.before_tool_call(context)
            self._context()
            if verdict is not None:
                raise PermissionError(verdict.reason or 'Inbox execution approval denied')
            controller = self.runtime.controller
            if controller.owns_current_task:
                return await self._admit(ids, peer, tuple(selected))
            future = controller.control(lambda: self._admit(ids, peer, tuple(selected)), name='inbox-review')
            receipt = asyncio.wrap_future(future)
            try:
                return await asyncio.shield(receipt)
            except asyncio.CancelledError:
                controller.cancel_control(future)
                await _settled(receipt)
                raise
        finally:
            self._reviewing.difference_update(ids)
            self._reviews.discard(task)

    async def _admit(self, ids: tuple[str, ...], peer: str, expected: tuple[InboxRecord, ...]) -> tuple[str, ...]:
        controller = self.runtime.controller
        tickets: list[Submission] = []
        self._context()
        try:
            records = await self._disk(lambda: self.store.approve(
                self.session_id, ids, cwd=self.cwd, branch_id=self.branch, generation=self.generation,
                expected=expected))
            self._context()
            for record in records:
                self._bindings[record.request_id] = record
                message = record.message
                value = SessionInput(record.request_id, 'acp', message.source, message.instruction, message.evidence,
                    {'inbox': {'message_id': message.message_id, 'attempt_id': record.attempt_id,
                               'branch_id': record.branch_id}, 'submitted': message.provenance})
                tickets.append(controller.submit(value, peer_id=peer))
            for ticket in tickets:
                await asyncio.shield(asyncio.wrap_future(ticket.accepted))
            self._context()
            await self._snapshot()
            self._context()
            return tuple(ticket.id for ticket in tickets)
        except BaseException:
            await self._rollback(ids, peer, tickets)
            raise

    async def _rollback(self, ids: tuple[str, ...], peer: str, tickets: list[Submission]) -> None:
        controller = self.runtime.controller
        try:
            for ticket in tickets:
                await _settled(asyncio.wrap_future(ticket.accepted))
                await asyncio.wrap_future(controller.cancel(ticket.id, peer_id=peer, wait=True))
                await _settled(asyncio.wrap_future(ticket.result))
            # approve may have committed some/all records before a failed or cancelled write receipt.
            rows = await self._disk(lambda: self.store.records(self.session_id, self.cwd))
            for row in rows:
                if (row.message.message_id in ids and row.status == 'claimed'
                        and row.owner_generation == self.generation):
                    await self._disk(lambda: self.store.finish(self.session_id, row.message.message_id,
                        generation=self.generation, attempt_id=row.attempt_id, status='cancelled'))
                    self._bindings.pop(row.request_id, None)
        except BaseException as exc:
            controller._broken = exc
            raise

    async def starting(self, batch: TurnBatch) -> None:
        selected = tuple(self._bindings[key] for key in batch.request_ids if key in self._bindings)
        if not selected:
            return
        self._context()
        updated = await self._disk(lambda: self.store.begin_batch(
            self.session_id, selected, batch_id=batch.id, generation=self.generation))
        for row in updated:
            self._bindings[row.request_id] = row

    async def finished(self, batch: TurnBatch, result: TurnResult) -> None:
        selected = tuple(self._bindings[key] for key in batch.request_ids if key in self._bindings)
        if not selected:
            return
        self._lease_owned(current=False)
        status = result.status if result.status in {'succeeded', 'failed', 'cancelled'} else 'cancelled'

        async def finish(row: InboxRecord) -> None:
            await self._disk(lambda: self.store.finish(self.session_id, row.message.message_id,
                generation=self.generation, attempt_id=row.attempt_id, status=status, error=result.error[:1024],
                batch_id=result.batch_id or None))
            self._bindings.pop(row.request_id, None)

        outcomes = await asyncio.gather(*(finish(row) for row in selected), return_exceptions=True)
        for outcome in outcomes:
            if isinstance(outcome, BaseException):
                raise outcome
        sequence, rows = await self._read_snapshot()
        self._publish(rows, sequence)

    async def retire(self) -> None:
        await self._owner(self._retire)

    async def _retire(self) -> None:
        if self._retired:
            return
        self._retired = True
        current = asyncio.current_task()
        reviews = tuple(t for t in self._reviews if t is not current)
        for task in reviews:
            task.cancel()
        if reviews:
            await asyncio.gather(*reviews, return_exceptions=True)
        if self._workers:
            await _settled(asyncio.gather(*tuple(self._workers), return_exceptions=True))
        if self._ready or self._bindings:
            self._lease_owned(current=False)
            await self._disk(lambda: self.store.retire(self.session_id, self.generation))
        self._bindings.clear()
