"""One group's actual execution owner, with fenced reservations and admission."""
from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Coroutine
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from typing import Any, TypeVar

T = TypeVar('T')
InputKey = tuple[str, str]


@dataclass(frozen=True)
class GroupReservation:
    group_id: str
    token: str


@dataclass(frozen=True)
class InputExecution:
    message_id: str
    attempt_id: str
    session_id: str


@dataclass(frozen=True)
class _Owner:
    reservation: GroupReservation
    task: asyncio.Task[Any] | None = None
    input_key: InputKey | None = None
    admitted: bool = False
    session_id: str = ''


class GroupController:
    """No model, protocol, queue policy or feature state belongs to this owner.

    Reservations fence the interval before a worker task's first step. The
    actual task retains ownership through cancellation cleanup. Same-task
    nested operations borrow that execution scope, never another task's lease.
    """

    def __init__(self, group_id: str, background_slots: asyncio.Semaphore) -> None:
        if not isinstance(group_id, str) or not group_id or '\x00' in group_id or len(group_id.encode()) > 256:
            raise ValueError('Invalid group execution scope')
        self.group_id, self._background_slots = group_id, background_slots
        self._owner: _Owner | None = None
        self._available = asyncio.Event()
        self._available.set()

    @property
    def busy(self) -> bool:
        return self._owner is not None

    def reserve(self) -> GroupReservation | None:
        if self._owner is not None:
            return None
        reservation = GroupReservation(self.group_id, uuid.uuid4().hex)
        self._owner = _Owner(reservation)
        self._available.clear()
        return reservation

    def release(self, reservation: GroupReservation) -> bool:
        owner = self._owner
        if (owner is None or owner.reservation != reservation
                or owner.task is not None and not owner.task.done()):
            return False  # A result waiter/late callback cannot release real execution.
        self._owner = None
        self._available.set()
        return True

    def _finish(self, reservation: GroupReservation) -> None:
        owner = self._owner
        if owner is not None and owner.reservation == reservation and owner.task is asyncio.current_task():
            self._owner = None
            self._available.set()

    @asynccontextmanager
    async def acquire(self, *, is_user: bool = False, reservation: GroupReservation | None = None,
                      input_key: InputKey | None = None) -> AsyncIterator[None]:
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError('Group execution requires an owned asyncio task')
        owner = self._owner
        if owner is not None and owner.task is task:
            if ((reservation is not None and reservation != owner.reservation)
                    or input_key is not None and input_key != owner.input_key):
                raise ValueError('Nested operation cannot change the original execution owner')
            yield
            return
        if reservation is not None:
            if (reservation.group_id != self.group_id or owner is None or owner.reservation != reservation
                    or owner.task is not None):
                raise ValueError('Group reservation is foreign, released or already bound to another task')
        else:
            while self._owner is not None:
                await self._available.wait()
            reservation = self.reserve()
            assert reservation is not None
        self._owner = _Owner(reservation, task, input_key)
        try:
            if is_user:
                yield
            else:
                # A pending background group cannot consume a slot before it
                # owns its group. Independent human groups bypass this pool.
                async with self._background_slots:
                    yield
        finally:
            self._finish(reservation)

    def start(self, reservation: GroupReservation, coroutine: Coroutine[Any, Any, T], *,
              input_key: InputKey | None = None) -> asyncio.Task[T]:
        called = False
        async def run() -> T:
            nonlocal called
            async with self.acquire(is_user=True, reservation=reservation, input_key=input_key):
                called = True
                return await coroutine
        job = asyncio.create_task(run(), name=f'claw-operation:{self.group_id}')
        def settled(done: asyncio.Task[T]) -> None:
            if not called:
                coroutine.close()
            self.release(reservation)  # Captured fence; never an unfenced group name.
        job.add_done_callback(settled)
        return job

    def confirm_input(self, message_id: str, attempt_id: str, session_id: str) -> None:
        owner = self._owner
        if (owner is None or owner.task is not asyncio.current_task() or owner.input_key != (message_id, attempt_id)
                or owner.admitted or not isinstance(session_id, str) or not session_id):
            raise ValueError('Actual input owner changed or was already admitted')
        self._owner = replace(owner, admitted=True, session_id=session_id)

    def owns_input(self, message_id: str, attempt_id: str | None = None) -> bool:
        owner = self._owner
        return bool(owner is not None and owner.admitted and owner.input_key is not None
                    and owner.input_key[0] == message_id and (attempt_id is None or owner.input_key[1] == attempt_id)
                    and owner.task is not None and not owner.task.done())

    def execution_for(self, message_id: str) -> InputExecution | None:
        if not self.owns_input(message_id):
            return None
        assert self._owner is not None and self._owner.input_key is not None
        return InputExecution(*self._owner.input_key, self._owner.session_id)

    def cancel_input(self, message_id: str, attempt_id: str) -> bool:
        if not self.owns_input(message_id, attempt_id):
            return False
        assert self._owner is not None and self._owner.task is not None
        if not self._owner.task.cancelling():
            self._owner.task.cancel()
        return True
