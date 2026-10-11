"""Connection-scoped legacy MCP task state, bounded retention and owned cleanup.

Explicit host/transport installation is required. This registry is not persistence,
permission, task negotiation or a substitute for sampling/elicitation human review.
Disconnect never resumes work; cancellation is not rollback of external effects.
"""
from __future__ import annotations

import asyncio
import json
import math
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

MAX_TASKS = 16
MAX_TASK_BYTES = 1024 * 1024
MAX_RESULT_BYTES = 256 * 1024
MAX_TTL_MS = 600000
_TERMINAL = frozenset({'completed', 'failed', 'cancelled'})


class TaskRegistryError(ValueError):
    """Fixed public diagnostics never include private request/result data."""


@dataclass(slots=True)
class _Task:
    id: str
    method: str
    ttl: float
    expires: float
    created: str
    updated: str
    status: str = 'working'
    encoded_result: str | None = field(default=None, repr=False)
    worker: asyncio.Task[None] | None = field(default=None, repr=False)
    observer: Callable[[dict[str, Any]], None] | None = field(default=None, repr=False)
    cancel_requested: bool = False
    expiry: asyncio.TimerHandle | None = field(default=None, repr=False)


class TaskRegistry:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._loop: asyncio.AbstractEventLoop | None = None
        self._closed = False
        self._tasks: dict[str, _Task] = {}
        self._retained_bytes = 0
        self._expiring: set[asyncio.Task[None]] = set()

    def _check_loop(self) -> None:
        loop = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = loop
        if self._loop is not loop:
            raise TaskRegistryError('Task controls require the original connection loop')

    @staticmethod
    def _timestamp() -> str:
        return datetime.now(UTC).isoformat(timespec='milliseconds').replace('+00:00', 'Z')

    def _lookup(self, identifier: str) -> _Task:
        self._check_loop()
        if not isinstance(identifier, str) or not identifier or len(identifier) > 128:
            raise TaskRegistryError('Invalid task identity')
        task = self._tasks.get(identifier)
        if self._closed or task is None or self._clock() >= task.expires:
            raise TaskRegistryError('Task unavailable/expired on this connection; no replay')
        return task

    def _public(self, task: _Task) -> dict[str, Any]:
        return {'taskId': task.id, 'status': task.status, 'ttl': task.ttl,
                'createdAt': task.created, 'lastUpdatedAt': task.updated, 'pollInterval': 250}

    def create(self, method: str, operation: Callable[[str], Awaitable[dict[str, Any]]],
               *, ttl: float = MAX_TTL_MS) -> dict[str, Any]:
        self._check_loop()
        if (self._closed or method not in {'sampling/createMessage', 'elicitation/create'}
                or not callable(operation) or type(ttl) not in (int, float)
                or not math.isfinite(ttl) or not 1 <= ttl <= MAX_TTL_MS):
            raise TaskRegistryError('Explicit supported task operation and bounded retention required')
        # Expired tasks still cleaning up own their slots. Automatic expiry or
        # an explicit sweep must actually settle them before capacity is released.
        if len(self._tasks) >= MAX_TASKS:
            raise TaskRegistryError('Task capacity full; settle/expire existing tasks before admission')
        stamp = self._timestamp()
        task = _Task(secrets.token_hex(16), method, ttl, self._clock() + ttl / 1000, stamp, stamp)
        self._tasks[task.id] = task
        async def work() -> None:
            try:
                result = await operation(task.id)
                # Cancelled/expired operations cannot publish even if a callback
                # suppresses cancellation and completes with private data.
                if self._closed or task.cancel_requested or self._clock() >= task.expires:
                    task.status = 'cancelled'
                    return
                from xdog.agent.mcp.schema import json_value
                encoded = json_value(result, maximum=MAX_RESULT_BYTES)
                if not isinstance(result, dict) or self._retained_bytes + len(encoded.encode()) > MAX_TASK_BYTES:
                    raise TaskRegistryError('Task result retention exhausted')
                task.encoded_result = encoded
                self._retained_bytes += len(encoded.encode())
                task.status = 'completed'
            except asyncio.CancelledError:
                task.status = 'cancelled'
            except Exception:
                task.status = 'cancelled' if task.cancel_requested else 'failed'
            finally:
                task.updated = self._timestamp()
                self._changed(task)
        task.worker = asyncio.create_task(work())
        task.expiry = asyncio.get_running_loop().call_later(ttl / 1000, self._expire, task)
        return {'task': self._public(task)}

    @staticmethod
    def _signal(task: _Task) -> None:
        if task.worker is not None and not task.worker.done():
            task.cancel_requested = True
            if not task.worker.cancelling():
                task.worker.cancel()

    def _expire(self, task: _Task) -> None:
        task.expiry = None
        if self._closed or self._tasks.get(task.id) is not task:
            return
        remaining = task.expires - self._clock()
        if remaining > 0:
            task.expiry = asyncio.get_running_loop().call_later(remaining, self._expire, task)
            return
        # Signal synchronously at the deadline; never wait for polling, a new
        # request or another task's slow provider finally before stopping work.
        self._signal(task)
        async def retire() -> None:
            await self._settle(task)
            self._remove(task)
        cleanup = asyncio.create_task(retire())
        self._expiring.add(cleanup)
        def done(completed: asyncio.Task[None]) -> None:
            self._expiring.discard(completed)
            if not completed.cancelled():
                completed.exception()
        cleanup.add_done_callback(done)

    def watch(self, identifier: str, observer: Callable[[dict[str, Any]], None]) -> Callable[[], None]:
        """Connection-local public-state observer; never a private payload callback."""
        task = self._lookup(identifier)
        if task.observer is not None:
            raise TaskRegistryError('Task status observer already registered')
        task.observer = observer
        def remove() -> None:
            if task.observer is observer:
                task.observer = None
        self._changed(task)
        return remove

    def _changed(self, task: _Task) -> None:
        if not self._closed and self._clock() < task.expires and task.observer is not None:
            try:
                task.observer(self._public(task))
            except Exception:
                task.observer = None  # Optional telemetry cannot change operation outcome.

    def state(self, identifier: str) -> dict[str, Any]:
        return self._public(self._lookup(identifier))

    def list(self) -> dict[str, Any]:
        self._check_loop()
        if self._closed:
            raise TaskRegistryError('Task connection closed')
        # This finite registry fits in one bounded page; no synthetic cursors.
        return {'tasks': [self._public(task) for task in self._tasks.values() if self._clock() < task.expires]}

    def result(self, identifier: str) -> dict[str, Any]:
        task = self._lookup(identifier)
        if task.status != 'completed' or task.encoded_result is None:
            raise TaskRegistryError('Task result not completed/available; no private partial result')
        return cast(dict[str, Any], json.loads(task.encoded_result))

    def authorize(self, identifier: str, method: str) -> None:
        """Only the registered operation worker may enter its task-aware consent owner."""
        task = self._lookup(identifier)
        if task.method != method or task.worker is not asyncio.current_task() or task.cancel_requested:
            raise TaskRegistryError('Task execution requires its original registered worker')

    def input_required(self, identifier: str, required: bool) -> None:
        """Host review transition only; peer cannot forge consent by changing state."""
        task = self._lookup(identifier)
        if type(required) is not bool or task.status in _TERMINAL or task.cancel_requested:
            raise TaskRegistryError('Task review transition unavailable')
        task.status = 'input_required' if required else 'working'
        task.updated = self._timestamp()
        self._changed(task)

    async def _settle(self, task: _Task) -> None:
        worker = task.worker
        if worker is None or worker is asyncio.current_task():
            raise TaskRegistryError('Task cannot cancel its own lifecycle owner')
        if not worker.done():
            task.cancel_requested = True
            if not worker.cancelling():
                worker.cancel()
        # Do not issue repeated cancellations into provider/tool finally. Await
        # the actual owned task even if the control caller is cancelled repeatedly.
        cancelled = False
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                caller = asyncio.current_task()
                if caller is not None and caller.cancelling():
                    cancelled = True
        if not worker.cancelled():
            worker.result()
        elif task.status not in _TERMINAL:
            # A task cancelled before its first step never executes finally.
            task.status = 'cancelled'
            task.updated = self._timestamp()
            self._changed(task)
        if cancelled:
            raise asyncio.CancelledError

    async def cancel(self, identifier: str) -> dict[str, Any]:
        task = self._lookup(identifier)
        if task.status not in _TERMINAL:
            await self._settle(task)
        return self._public(task)

    async def abandon(self, identifier: str) -> None:
        """Retire an undelivered creation, including expired or completed records."""
        self._check_loop()
        task = self._tasks.get(identifier)
        if task is None:
            return
        self._signal(task)
        try:
            await self._settle(task)
        finally:
            # _settle propagates caller cancellation only after worker cleanup.
            self._remove(task)

    def _remove(self, task: _Task) -> None:
        task.observer = None
        if task.expiry is not None:
            task.expiry.cancel()
            task.expiry = None
        if task.encoded_result is not None:
            self._retained_bytes -= len(task.encoded_result.encode())
            task.encoded_result = None
        self._tasks.pop(task.id, None)

    async def sweep(self) -> None:
        self._check_loop()
        tasks = tuple(task for task in self._tasks.values() if self._clock() >= task.expires)
        for task in tasks:
            self._signal(task)
        cancelled = False
        for task in tasks:
            try:
                await self._settle(task)
            except asyncio.CancelledError:
                cancelled = True
            self._remove(task)
        if cancelled:
            raise asyncio.CancelledError

    async def withdraw(self, method: str) -> None:
        """Cancel/retire one request family without revoking the other family."""
        self._check_loop()
        tasks = tuple(task for task in self._tasks.values() if task.method == method)
        for task in tasks:
            if task.worker is not None and not task.worker.done():
                task.cancel_requested = True
                if not task.worker.cancelling():
                    task.worker.cancel()
        cancelled = False
        for task in tasks:
            try:
                await self._settle(task)
            except asyncio.CancelledError:
                cancelled = True
            self._remove(task)
        if cancelled:
            raise asyncio.CancelledError

    async def close(self) -> None:
        self._check_loop()
        self._closed = True  # No result access during provider finally settlement.
        # Signal every running task before awaiting any one provider's finally.
        # Slow cleanup must not let a sibling continue model/tool execution.
        tasks = tuple(self._tasks.values())
        for task in tasks:
            if task.worker is not None and not task.worker.done():
                task.cancel_requested = True
                if not task.worker.cancelling():
                    task.worker.cancel()
        cancelled = False
        for task in tasks:
            try:
                await self._settle(task)
            except asyncio.CancelledError:
                cancelled = True
            self._remove(task)
        # Deadline cleanup may concurrently own the same worker. Retire all
        # original-loop cleanup tasks too; no timer/cleanup survives disconnect.
        settlement = asyncio.gather(*tuple(self._expiring), return_exceptions=True)
        while not settlement.done():
            try:
                await asyncio.shield(settlement)
            except asyncio.CancelledError:
                cancelled = True
        settlement.result()
        self._expiring.clear()
        if cancelled:
            raise asyncio.CancelledError
