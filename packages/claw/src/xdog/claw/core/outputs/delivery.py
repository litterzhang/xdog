"""Application-owned output transport. Sending is not Agent execution."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence

from xdog.agent.async_work import wait_owned
from xdog.claw.core.inputs.models import InputEnvelope, InputScope
from xdog.claw.core.outputs.routes import Policy, ReadPolicy
from xdog.claw.core.outputs.store import OutputRecord, OutputStore

Sender = Callable[[OutputRecord], Awaitable[None]]
ResolveSender = Callable[[OutputRecord], Sender | None]


class DeliveryNotSent(RuntimeError):
    """Positive transport knowledge that a particular send was not accepted."""


class OutputDelivery:
    """One ordered delivery lane per scope; no Agent, ingress or feature policy.

    Unknown is terminal for automatic transport. Only pending (including a
    positively rejected send) is resumed. A detached emitter never owns an
    already committed output's transport lifetime.
    """

    def __init__(self, store: OutputStore, resolve_sender: ResolveSender, *, gate: ReadPolicy | None = None) -> None:
        self.store, self.resolve_sender, self.gate = store, resolve_sender, gate
        self._locks: dict[InputScope, asyncio.Lock] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._closing = False
        self._close_task: asyncio.Task[None] | None = None

    async def emit(self, envelope: InputEnvelope, attempt_id: str, binding: str,
                   chunks: Sequence[str], *, ordinal: int = 0) -> int:
        if self._closing:
            raise RuntimeError('Application outputs are closing')
        for text in chunks:
            await self.store.append(envelope, attempt_id, ordinal, binding, text)
            ordinal += 1
        await asyncio.shield(self._start(envelope.scope))
        return ordinal

    def _start(self, scope: InputScope) -> asyncio.Task[None]:
        if self._closing:
            raise RuntimeError('Application outputs are closing')
        task = asyncio.create_task(self._drain(scope), name=f'claw-output:{scope.group_id}')
        self._tasks.add(task)
        task.add_done_callback(self._done)
        return task

    def _done(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        # Observe detached errors; disk/schema failures still reach an attached
        # emitter, unlike normal delivery failures which have durable receipts.
        if not task.cancelled():
            task.exception()

    async def resume(self) -> None:
        scopes = {row.envelope.scope for row in await self.store.records() if row.status == 'pending'}
        await asyncio.gather(*(self._start(scope) for scope in scopes))

    async def cancel_pending(self, scope: InputScope, message_ids: tuple[str, ...]) -> tuple[str, ...]:
        # Do not wait behind an entered network sender or cancel its actual work.
        return await self.store.cancel_pending(scope, message_ids)

    async def _policy(self, row: OutputRecord) -> Policy:
        try:
            value = await self.gate(row) if self.gate is not None else "ready"
        except (OSError, RuntimeError):
            return "pending"  # Capability/IO unavailable is not permission to enter the sender.
        if value not in {"ready", "pending", "cancelled"}:
            raise ValueError("Invalid host output policy")
        return value

    async def _drain(self, scope: InputScope) -> None:
        lock = self._locks.setdefault(scope, asyncio.Lock())
        async with lock:
            for row in await self.store.records(scope):
                if self._closing:
                    return
                if row.status != 'pending':
                    continue
                policy = await self._policy(row)
                if policy == "cancelled":
                    await self.store.cancel_pending(scope, (row.envelope.message.id,))
                    continue
                if policy == "pending":
                    continue
                sender = self.resolve_sender(row)
                if sender is None:
                    continue  # Original endpoint absent; do not invent another recipient.
                await self._send(row, sender)

    async def _send(self, row: OutputRecord, sender: Sender) -> None:
        started: OutputRecord | None = None
        entered_sender = False
        try:
            started = await self.store.begin(row.id)
            policy = await self._policy(started)
            if policy != "ready":
                await self.store.settle(row.id, started.send_id, "pending",
                    "Notification cancelled before transport" if policy == "cancelled" else
                    "Output policy unavailable before transport")
                if policy == "cancelled":
                    await self.store.cancel_pending(row.envelope.scope, (row.envelope.message.id,))
                return
            entered_sender = True
            await sender(started)
        except asyncio.CancelledError:
            # begin's admitted commit may have completed while its ACK was
            # cancelled. No sender call before the fence means proven-not-sent.
            async def settle_cancelled() -> None:
                current = next(item for item in await self.store.records(row.envelope.scope) if item.id == row.id)
                if current.status == 'sending':
                    await self.store.settle(row.id, current.send_id, 'unknown' if entered_sender else 'pending',
                        'Transport interrupted; inspect effects' if entered_sender else 'Cancelled before transport')
            await wait_owned(asyncio.create_task(settle_cancelled()))
            raise
        except DeliveryNotSent as exc:
            if started is None:
                raise
            await self.store.settle(row.id, started.send_id, 'pending', str(exc))
        except Exception as exc:
            if started is None:
                current = next((item for item in await self.store.records(row.envelope.scope)
                                if item.id == row.id), None)
                if current is not None and current.status == "cancelled":
                    return  # An explicit pending-only cancellation won before begin.
                raise  # Storage is not transport; do not relabel a failed commit.
            await self.store.settle(row.id, started.send_id, 'unknown' if entered_sender else 'pending',
                                    str(exc) or type(exc).__name__)
        else:
            await self.store.settle(row.id, started.send_id, 'delivered')

    async def close(self) -> None:
        if self._close_task is None:
            self._closing = True
            self._close_task = asyncio.create_task(self._close())
        await wait_owned(self._close_task)

    async def _close(self) -> None:
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        try:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, BaseException) and not isinstance(result, asyncio.CancelledError):
                    raise result
        finally:
            await self.store.close()
