"""Small owned display ports; no harness reference or business-service lifetime."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from xdog.agent.async_work import wait_owned
from xdog.agent.cross_loop import settle_on
from xdog.agent.extensions.views import ViewRegistry
from xdog.skills.goal.service import GoalService
from xdog.skills.goal.ui_views import GoalView


class GoalDisplay:
    def __init__(self, service: GoalService, registry: ViewRegistry, *, valid: Callable[[], bool],
                 notify: Callable[[str, str], None]) -> None:
        self.loop = asyncio.get_running_loop()
        self._view, self._valid, self._notify = GoalView(service), valid, notify
        self._retired = False
        self._reads: set[asyncio.Task[Any]] = set()
        self._close_task: asyncio.Task[None] | None = None
        self._remove = registry.bind("goal_ui", "goals", self.read,
                                     valid=lambda: not self._retired and self._valid())

    async def read(self, query: dict[str, Any]) -> dict[str, Any]:
        return await settle_on(self.loop, self._read(query))

    async def _read(self, query: dict[str, Any]) -> dict[str, Any]:
        if self._retired or not self._valid():
            raise ValueError("Goal display owner retired")
        task = asyncio.current_task()
        assert task is not None
        self._reads.add(task)
        try:
            result = await self._view.read(query)
            if self._retired or not self._valid():
                raise ValueError("Goal display owner changed during read")
            return result
        finally:
            self._reads.discard(task)

    def changed(self) -> None:
        if not self._retired and self._valid():
            self._notify("goals", "")

    def retire(self) -> None:
        self._retired = True
        self._remove()

    async def aclose(self) -> None:
        await settle_on(self.loop, self._aclose())

    async def _aclose(self) -> None:
        if self._close_task is None:
            self.retire()

            async def close() -> None:
                reads = tuple(self._reads)
                for task in reads:
                    task.cancel()
                await asyncio.gather(*reads, return_exceptions=True)

            self._close_task = asyncio.create_task(close())
        await wait_owned(self._close_task)
