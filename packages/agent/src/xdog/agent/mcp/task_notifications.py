"""Optional bounded task status telemetry after successful creation delivery.

Slow peers may see coalesced states, never private review/result data. Polling remains
canonical. Notification failure does not cancel a separately approved operation.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from xdog.agent.mcp.task_registry import TaskRegistry, TaskRegistryError


class TaskNotifications:
    def __init__(self) -> None:
        self._jobs: dict[str, tuple[str, asyncio.Task[None]]] = {}

    def start(self, registry: TaskRegistry, identifier: str, method: str,
              write: Callable[[dict[str, Any]], Awaitable[Any]], current: Callable[[], bool],
              timeout: float) -> None:
        if identifier in self._jobs or len(self._jobs) >= 16 or not current():
            return
        # One pending snapshot per task, coalescing optional telemetry rather
        # than unbounded queues or delaying the original review/operation loop.
        pending: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1)
        def changed(state: dict[str, Any]) -> None:
            if pending.full():
                pending.get_nowait()
            pending.put_nowait(state)
        try:
            remove = registry.watch(identifier, changed)
        except TaskRegistryError:
            return  # Expiry/withdrawal may race creation delivery.
        async def run() -> None:
            try:
                while current():
                    # Expired/retired identities never emit stale state. Finite
                    # waits also retire idle monitors without a notification.
                    registry.state(identifier)
                    try:
                        async with asyncio.timeout(.1):
                            state = await pending.get()
                    except TimeoutError:
                        continue
                    if not current():
                        return
                    registry.state(identifier)
                    async with asyncio.timeout(timeout):
                        await write({'jsonrpc': '2.0', 'method': 'notifications/tasks/status', 'params': {
                            **state, '_meta': {'io.modelcontextprotocol/related-task': {'taskId': identifier}}}})
                    if state['status'] in {'completed', 'failed', 'cancelled'}:
                        return
            except (TaskRegistryError, ValueError, RuntimeError, OSError, TimeoutError):
                pass  # No notification retry/replay, polling stays authoritative.
            finally:
                remove()
        task = asyncio.create_task(run())
        self._jobs[identifier] = method, task
        def done(completed: asyncio.Task[None]) -> None:
            if self._jobs.get(identifier) == (method, completed):
                self._jobs.pop(identifier, None)
            if not completed.cancelled():
                completed.exception()
        task.add_done_callback(done)

    async def close(self, method: str = '') -> None:
        jobs = tuple(task for family, task in self._jobs.values() if not method or family == method)
        for task in jobs:
            if not task.cancelling():
                task.cancel()
        settlement = asyncio.gather(*jobs, return_exceptions=True)
        cancelled = False
        while not settlement.done():
            try:
                await asyncio.shield(settlement)
            except asyncio.CancelledError:
                cancelled = True
        settlement.result()
        if cancelled:
            raise asyncio.CancelledError
