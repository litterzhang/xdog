"""Automatic durable input admission under the session's single execution owner."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from concurrent.futures import Future
from typing import TYPE_CHECKING, Any, TypeVar

from xdog.agent.session_input import SessionInput
from xdog.coding.core.sessions.batches import TurnBatch
from xdog.coding.core.sessions.inbox_models import InboxMessage, InboxRecord
from xdog.coding.core.sessions.inbox_store import InboxReservationRollbackError, InboxStore
from xdog.coding.core.sessions.models import Submission, TurnResult

if TYPE_CHECKING:
    from xdog.coding.core.sessions.controller import SessionController

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
        future.exception()


class InboxPromptCancelled(RuntimeError):
    """A durable prompt was withdrawn before an execution attempt was admitted."""


class InboxCoordinator:
    def __init__(self, controller: SessionController, store: InboxStore) -> None:
        self.controller, self.store = controller, store
        session = controller.session
        self.session_id, self.cwd = session.session_id, session.working_dir.resolve()
        self.generation, self.branch = controller.generation, controller.branch
        self.lease = session._lease
        self._ready = False
        self._initializing = asyncio.Lock()
        self._activation_lock = asyncio.Lock()
        self._bindings: dict[str, InboxRecord] = {}
        self._tickets: dict[str, Submission] = {}
        self._ticket_events: dict[str, asyncio.Event] = {}
        self._withdrawals: dict[str, asyncio.Event] = {}
        self._message_peers: dict[str, str] = {}
        self._workers: set[asyncio.Task[Any]] = set()
        self._retired = False
        self._stopping = False
        self._last: tuple[InboxRecord, ...] | None = None
        self._snapshot_sequence = 0
        self._published_sequence = -1
        self._watch_task: asyncio.Task[None] | None = None
        self._enabled = False
        self._draining: Future[tuple[Submission, ...]] | None = None

    def is_submission(self, request_id: str) -> bool:
        return request_id in self._bindings

    def message_id(self, request_id: str) -> str | None:
        row = self._bindings.get(request_id)
        return row.message.message_id if row is not None else None

    def _lease_owned(self, *, current: bool = True) -> None:
        if self.lease is None or self.lease._fd < 0 or self.lease.session_id != self.session_id:
            raise RuntimeError('Inbox execution requires its session writer lease')
        if current and self.controller.session._lease is not self.lease:
            raise RuntimeError('Inbox owner lease changed')

    def _context(self) -> None:
        self._lease_owned()
        session, controller = self.controller.session, self.controller
        if (self._retired or self._stopping or controller._closing or controller._broken is not None
                or session.session_id != self.session_id or session.working_dir.resolve() != self.cwd
                or controller.generation != self.generation or controller.branch != self.branch
                or session.session_data.active_branch != self.branch):
            raise RuntimeError('Inbox owner context/generation/branch changed or is unavailable')

    async def _owner(self, operation: Callable[[], Awaitable[T]]) -> T:
        controller = self.controller
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

    async def _disk(self, operation: Callable[[], T], *, retiring: bool = False) -> T:
        if self._retired and not retiring:
            raise RuntimeError('Inbox owner is retired')
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
            self._context()
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
        controller = self.controller
        if (self._retired or controller.generation != self.generation
                or self.controller.session.session_id != self.session_id):
            return rows
        if sequence < self._published_sequence:
            return self._last if self._last is not None else rows
        self._published_sequence = sequence
        if rows != self._last:
            self._last = rows
            self.controller.publish('inbox_snapshot', rows)
        return rows

    async def activate(self) -> None:
        try:
            await self._owner(self._activate)
        except Exception as exc:
            # Navigation publishes SessionData before the generation/lease rebind
            # settles. A stale startup may fail its fence, but must not poison the
            # successor controller. Actual failures of the current owner still do.
            session = self.controller.session
            if (not self._retired and not self._stopping and self.controller.inbox is self
                    and self.controller.generation == self.generation
                    and session.session_id == self.session_id and session.working_dir.resolve() == self.cwd
                    and session.session_data.active_branch == self.branch and session._lease is self.lease):
                self.controller._broken = exc
                self.controller._inputs_ready = True
                self.controller.publish('inbox_error', str(exc))
                self.controller._pump()  # Settle accepted inputs as failed; never strand them behind setup.
            raise

    async def _activate(self) -> None:
        async with self._activation_lock:
            if self._enabled:
                return
            await self._initialize()
            # Installed owner capabilities must recover when the execution owner
            # resumes, not only after its first new human/model turn. Keep this
            # on the existing controller loop and behind the intake setup fence.
            if self.controller.owns_current_task:
                await self.controller.session.initialize_capabilities()
            else:
                # Background intake startup cannot race a navigation control.
                # Rebind/turn operations already own this same serialized route.
                from xdog.agent.async_work import wait_owned
                await wait_owned(asyncio.wrap_future(self.controller.control(
                    self.controller.session.initialize_capabilities, name='capability-owner-start')))
            self._context()
            await self._drain(initial=True)
            self._context()
            self._enabled = True
            self.controller._inputs_ready = True
            if self._watch_task is None:
                self._watch_task = asyncio.create_task(self._watch())
            self.controller._pump()

    async def _watch(self) -> None:
        error = ''
        while not self._retired and not self.controller._closing:
            try:
                rows = await self._snapshot()
                if any(row.status == 'pending' and row.branch_id == self.branch for row in rows):
                    await self._drain()
                error = ''
            except (OSError, ValueError, RuntimeError) as exc:
                if self._retired or self.controller._closing:
                    break
                if str(exc) != error:
                    self.controller.publish('inbox_error', str(exc))
                    error = str(exc)
            await asyncio.sleep(0.5)

    async def drain(self) -> tuple[Submission, ...]:
        return await self._owner(self._drain)

    async def _drain(self, *, initial: bool = False) -> tuple[Submission, ...]:
        await self._initialize()
        if ((self.controller._active is not None and not self.controller.owns_current_task)
                or not (initial or self._enabled)):
            return ()
        if self.controller.owns_current_task:
            return await self._admit_pending()
        if self._draining is None or self._draining.done():
            self._draining = self.controller.control(self._admit_pending, name='inbox-intake')
        receipt = asyncio.wrap_future(self._draining)
        try:
            return await asyncio.shield(receipt)
        except asyncio.CancelledError:
            # The shared control remains owned by the coordinator. Retirement
            # settles it; an abandoned polling observer must still retrieve errors.
            receipt.add_done_callback(lambda done: None if done.cancelled() else done.exception())
            raise

    async def _admit_pending(self) -> tuple[Submission, ...]:
        self._context()
        controller = self.controller
        # Prefetch only the next fixed turn, rather than claiming the entire durable backlog.
        available = min(128 - len(controller._jobs) - len(controller._admissions),
                        controller.max_messages_per_turn - len(controller._jobs) - len(controller._admissions))
        if available <= 0:
            return ()
        rows = await self._disk(lambda: self.store.records(self.session_id, self.cwd))
        self._context()
        selected = tuple(row for row in rows if row.status == 'pending' and row.branch_id == self.branch)[:available]
        if not selected:
            return ()
        claimed: tuple[InboxRecord, ...] = ()
        reservation_error: InboxReservationRollbackError | None = None
        tickets = []

        def reserve() -> None:
            nonlocal claimed, reservation_error
            # The disk worker settles before cancellation propagates, so retain
            # its receipts even if its awaiting control never receives a result.
            try:
                claimed = self.store.reserve(self.session_id,
                    tuple(row.message.message_id for row in selected), cwd=self.cwd, branch_id=self.branch,
                    generation=self.generation, expected=selected)
            except InboxReservationRollbackError as error:
                reservation_error = error
                raise

        try:
            await self._disk(reserve)
            self._context()
            for row in claimed:
                withdrawn = self._withdrawals.get(row.message.message_id)
                if withdrawn is not None and withdrawn.is_set():
                    continue  # Operator withdrawal won before ticket handoff.
                self._bindings[row.request_id] = row
                message = row.message
                value = SessionInput(row.request_id, 'acp', message.source, message.instruction, message.evidence,
                    {'inbox': {'message_id': message.message_id, 'attempt_id': row.attempt_id,
                               'branch_id': row.branch_id}, 'submitted': message.provenance})
                ticket = controller.submit(value, peer_id=self._message_peers.get(message.message_id, 'inbox'))
                self._tickets[message.message_id] = ticket
                self._ticket_events.setdefault(message.message_id, asyncio.Event()).set()
                tickets.append(ticket)
            for ticket in tickets:
                await asyncio.shield(asyncio.wrap_future(ticket.accepted))
            self._context()
            await self._snapshot()
            return tuple(tickets)
        except BaseException as cause:
            try:
                for ticket in tickets:
                    await asyncio.wrap_future(controller.cancel(ticket.id,
                        peer_id=self._message_peers.get(self.message_id(ticket.id) or '', 'inbox'), wait=True))
                if claimed:
                    await self._disk(lambda: self.store.release_unstarted(self.session_id,
                        tuple(row.message.message_id for row in claimed), generation=self.generation, expected=claimed))
            except BaseException as cleanup:
                controller._broken = cleanup
                controller.publish('inbox_error', 'Inbox claim cleanup failed: ' + str(cleanup))
                raise
            finally:
                for row in claimed:
                    self._bindings.pop(row.request_id, None)
                    registered_ticket = self._tickets.get(row.message.message_id)
                    if registered_ticket is not None and registered_ticket.id == row.request_id:
                        self._tickets.pop(row.message.message_id, None)
            if isinstance(cause, InboxReservationRollbackError) or reservation_error is not None:
                controller._broken = reservation_error or cause
                controller.publish('inbox_error', str(controller._broken))
            raise

    async def submit_prompt(self, message: InboxMessage, *, peer_id: str) -> Submission:
        return await self._owner(lambda: self._submit_prompt(message, peer_id))

    async def _submit_prompt(self, message: InboxMessage, peer_id: str) -> Submission:
        await self._initialize()
        if message.session_id != self.session_id or message.cwd.resolve() != self.cwd:
            raise ValueError('Prompt target does not match its owner')
        previous_peer = self._message_peers.get(message.message_id)
        self._message_peers[message.message_id] = peer_id
        try:
            await self._disk(lambda: self.store.enqueue(message, expected_branch_id=self.branch))
        except ValueError:
            if previous_peer is None:
                self._message_peers.pop(message.message_id, None)
            else:
                self._message_peers[message.message_id] = previous_peer
            raise
        ready = self._ticket_events.setdefault(message.message_id, asyncio.Event())
        withdrawn = self._withdrawals.setdefault(message.message_id, asyncio.Event())
        self._context()
        if message.message_id in self._tickets:
            return self._tickets[message.message_id]
        if self.controller.owns_current_task:
            await self._admit_pending()
        else:
            future = self.controller.control(self._admit_pending, name='inbox-prompt-intake')
            receipt = asyncio.wrap_future(future)
            withdrawal = asyncio.create_task(withdrawn.wait())
            try:
                signals: set[asyncio.Future[Any]] = {receipt, withdrawal}
                await asyncio.wait(signals, return_when=asyncio.FIRST_COMPLETED)
                if withdrawn.is_set():
                    raise InboxPromptCancelled('Prompt withdrawn before admission')
                await asyncio.shield(receipt)
            except BaseException:
                self.controller.cancel_control(future)
                await _settled(receipt)
                raise
            finally:
                withdrawal.cancel()
                await asyncio.gather(withdrawal, return_exceptions=True)
        while True:
            self._context()
            if withdrawn.is_set():
                raise InboxPromptCancelled('Prompt withdrawn before admission')
            ticket = self._tickets.get(message.message_id)
            if ticket is not None:
                return ticket
            ready.clear()
            await ready.wait()

    async def retry(self, message_id: str, *, peer_id: str) -> str:
        async def retry_owned() -> str:
            await self._initialize()
            await self._disk(lambda: self.store.retry(self.session_id, message_id, cwd=self.cwd, branch_id=self.branch))
            self._tickets.pop(message_id, None)
            self._withdrawals[message_id] = asyncio.Event()
            self._message_peers[message_id] = peer_id
            self._context()
            if self.controller.owns_current_task:
                await self._admit_pending()
            else:
                future = self.controller.control(self._admit_pending, name='inbox-retry')
                await asyncio.shield(asyncio.wrap_future(future))
            ticket = self._tickets.get(message_id)
            if ticket is None:
                raise RuntimeError('Retry retained pending until queue capacity is available')
            return ticket.id
        return await self._owner(retry_owned)

    async def cancel(self, message_id: str, *, peer_id: str) -> bool:
        async def cancel_owned() -> bool:
            await self._initialize()
            if self._message_peers.get(message_id) != peer_id and peer_id != 'inbox-command':
                return False
            ticket = self._tickets.get(message_id)
            if ticket is not None and ticket.id in self.controller._jobs:
                job = self.controller._jobs[ticket.id]
                if self.controller._in_active(job):
                    return await asyncio.wrap_future(self.controller.cancel(ticket.id, peer_id=job.peer, wait=True))
            withdrawal = self._withdrawals.setdefault(message_id, asyncio.Event())
            already_withdrawn = withdrawal.is_set()
            withdrawal.set()  # Fence claim→ticket handoff before the asynchronous disk operation.
            try:
                await self._disk(lambda: self.store.cancel_pending(self.session_id, message_id, cwd=self.cwd,
                    branch_id=self.branch, generation=self.generation))
            except OSError as error:
                self.controller._broken = error  # Unknown publication is a real storage failure, not a normal cancel.
                self.controller.publish('inbox_error', str(error))
                raise
            except Exception:
                if not already_withdrawn:
                    withdrawal.clear()
                raise
            self._ticket_events.setdefault(message_id, asyncio.Event()).set()
            if ticket is not None:
                row = self._bindings.pop(ticket.id, None)
                if row is not None:
                    await asyncio.wrap_future(self.controller.cancel(ticket.id,
                        peer_id=self._message_peers.get(message_id, 'inbox'), wait=True))
            await self._snapshot()
            return True
        return await self._owner(cancel_owned)

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

    async def stop_polling(self) -> None:
        await self._owner(self._stop_polling)

    async def _stop_polling(self) -> None:
        self._stopping = True
        self._enabled = False
        self.controller._inputs_ready = False
        if self._watch_task is not None and self._watch_task is not asyncio.current_task():
            self._watch_task.cancel()
            await asyncio.gather(self._watch_task, return_exceptions=True)

    async def retire(self) -> None:
        await self._owner(self._retire)

    async def _retire(self) -> None:
        if self._retired:
            return
        self._retired = True
        await self._stop_polling()
        if self._draining is not None and not self._draining.done():
            self.controller.cancel_control(self._draining)
            await _settled(asyncio.wrap_future(self._draining))
        if self._workers:
            await _settled(asyncio.gather(*tuple(self._workers), return_exceptions=True))
        if self._ready or self._bindings:
            self._lease_owned(current=False)
            await self._disk(lambda: self.store.retire(self.session_id, self.generation), retiring=True)
        self._bindings.clear()
        for event in self._ticket_events.values():
            event.set()
