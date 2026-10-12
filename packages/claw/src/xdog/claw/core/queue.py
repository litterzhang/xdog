"""Message queue with per-group concurrency and user-message priority."""
import asyncio
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Callable

from xdog.claw.core.types import GroupInput, QueueMode


class MessageQueue:
    """Concurrency control for agent execution.

    Each group gets its own ``asyncio.Lock`` so turns within a group are
    serialized. A global ``asyncio.Semaphore`` limits how many groups can
    run concurrently — but only for background tasks (scheduler, goal
    runner). User-initiated messages bypass the global semaphore so they
    are never starved by background work.
    """

    def __init__(self, max_concurrent: int):
        self._max_concurrent = max_concurrent
        self._global_sem = asyncio.Semaphore(max_concurrent)
        self._group_locks: dict[str, asyncio.Lock] = {}
        self._queues: dict[str, list[GroupInput]] = {}
        self._running: set[str] = set()
        self._reserved: set[str] = set()

    def _get_lock(self, group_id: str) -> asyncio.Lock:
        if group_id not in self._group_locks:
            self._group_locks[group_id] = asyncio.Lock()
        return self._group_locks[group_id]

    async def enqueue(
        self,
        message: GroupInput,
        mode: QueueMode = QueueMode.COLLECT,
        on_steer: Callable[..., Any] | None = None,
        on_follow_up: Callable[..., Any] | None = None,
        *,
        max_queued: int = 0,
    ) -> None:
        if mode == QueueMode.STEER:
            if on_steer:
                await on_steer(message)
        elif mode == QueueMode.STEER_BACKLOG:
            if on_follow_up:
                await on_follow_up(message)
            self._append(message, max_queued)
        else:
            # default to collect
            self._append(message, max_queued)

    def _append(self, message: GroupInput, max_queued: int = 0) -> None:
        gid = message.group_id
        if gid not in self._queues:
            self._queues[gid] = []
        self._queues[gid].append(message)
        # Overflow: drop oldest if over limit
        if max_queued > 0 and len(self._queues[gid]) > max_queued:
            self._queues[gid] = self._queues[gid][-max_queued:]

    async def dequeue(self, group_id: str) -> list[GroupInput]:
        if group_id in self._queues:
            return self._queues.pop(group_id)
        return []

    def is_running(self, group_id: str) -> bool:
        return group_id in self._running or group_id in self._reserved

    def try_reserve(self, group_id: str) -> bool:
        """Atomically reserve an idle group before asynchronous dispatch."""
        if self.is_running(group_id):
            return False
        self._reserved.add(group_id)
        return True

    def claim_reservation(self, group_id: str) -> None:
        self._reserved.discard(group_id)

    def release_reservation(self, group_id: str) -> None:
        self._reserved.discard(group_id)

    @asynccontextmanager
    async def acquire(self, group_id: str, *, is_user: bool = False) -> AsyncIterator[None]:
        """Acquire concurrency control and mark group as running.

        ``is_user=True`` bypasses the global semaphore so user messages
        are never blocked by background tasks in other groups. They
        still wait on the per-group lock (turns within a group are
        always serialized).
        """
        lock = self._get_lock(group_id)
        if is_user:
            async with lock:
                self._running.add(group_id)
                try:
                    yield
                finally:
                    self._running.discard(group_id)
        else:
            # Waiting behind a busy group must not consume capacity for other groups.
            async with lock:
                async with self._global_sem:
                    self._running.add(group_id)
                    try:
                        yield
                    finally:
                        self._running.discard(group_id)

    async def collect_with_debounce(
        self, group_id: str, debounce_ms: int
    ) -> list[GroupInput]:
        """Wait for debounce period, then dequeue all collected messages."""
        if debounce_ms > 0:
            await asyncio.sleep(debounce_ms / 1000.0)
        return await self.dequeue(group_id)
