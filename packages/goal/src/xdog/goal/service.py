"""Explicit parent decisions over Goal-owned truth; no planner or turn-end loop."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from collections.abc import Callable, Coroutine
from dataclasses import replace
from pathlib import Path
from typing import Any, TypeVar

from xdog.agent.async_work import wait_owned
from xdog.goal.host import GoalHost, OperationContext, require_user
from xdog.goal.models import Goal, Notice, Report, Task, WorkerRef, text
from xdog.goal.notifications import drain
from xdog.goal.review import ReviewLifecycle
from xdog.goal.store import GoalStore
from xdog.goal.worker import BoundReporter, WorkerLifecycle

T = TypeVar("T")
logger = logging.getLogger(__name__)
_TERMINAL = frozenset({"completed", "abandoned"})


class GoalService:
    """Own only domain lifecycle/storage. The host owns actual Agent execution.

    Accepted mutations are owned operations: losing the caller does not roll back
    admitted persistence/start. Original effects settle before close; uncertain
    host starts become interrupted and are never retried by a background loop.
    """

    def __init__(self, store: GoalStore, host: GoalHost, *, on_change: Callable[[], None] | None = None) -> None:
        if store.owner != host.owner:
            raise ValueError("Goal host owner/workspace does not match storage")
        self.store, self.host = store, host
        self._locks: dict[str, asyncio.Lock] = {}
        self._notices_lock = asyncio.Lock()
        self._operations: set[asyncio.Task[Any]] = set()
        self._on_change = on_change
        self._closing = False
        self._close_task: asyncio.Task[None] | None = None
        self._workers = WorkerLifecycle(self)
        self._reviews = ReviewLifecycle(self)

    def _owner(self) -> None:
        if self.host.owner != self.store.owner:
            raise ValueError("Goal host owner changed; no automatic transfer or replay")

    def _lock(self, identifier: str) -> asyncio.Lock:
        return self._locks.setdefault(identifier, asyncio.Lock())

    async def _owned(self, operation: Coroutine[Any, Any, T]) -> T:
        if self._closing:
            operation.close()
            raise RuntimeError("Goal service is closing")
        try:
            self._owner()
        except BaseException:
            operation.close()
            raise
        task = asyncio.create_task(operation)
        self._operations.add(task)
        task.add_done_callback(self._operations.discard)
        return await wait_owned(task)

    async def _get(self, identifier: str, *, live: bool = False) -> Goal:
        self._owner()
        goal = await self.store.get(identifier)
        if goal is None:
            raise ValueError("Unknown Goal in this owner")
        if live and goal.status in _TERMINAL:
            raise ValueError("Terminal Goal cannot start new work or change criteria")
        return goal

    async def _save(
        self,
        old: Goal,
        *,
        notices: tuple[Notice, ...] = (),
        artifacts: tuple[tuple[str, object], ...] = (),
        cancel_notices: bool = False,
        **changes: Any,
    ) -> Goal:
        self._owner()
        goal = replace(old, version=old.version + 1, **changes)
        result = await self.store.put(
            goal,
            expected_version=old.version,
            notices=notices,
            artifacts=artifacts,
            cancel_pending_notices=cancel_notices,
        )
        self._changed()
        return result

    def _changed(self) -> None:
        if self._on_change is not None:
            try:
                self._on_change()
            except Exception:
                logger.warning("Goal fact committed; optional view hint failed", exc_info=True)

    @staticmethod
    def _notice(goal: Goal, key: str, instruction: str) -> tuple[Notice, ...]:
        if not goal.notifications_enabled:
            return ()
        identifier = "goal-notice-" + hashlib.sha256(f"{goal.id}:{key}".encode()).hexdigest()
        return (
            Notice(
                identifier, goal.id, f"Goal {goal.id}: {instruction[:8192]}\nInspect current Goal facts before acting."
            ),
        )

    @staticmethod
    def _task(goal: Goal, identifier: str) -> Task:
        task = next((item for item in goal.tasks if item.id == identifier), None)
        if task is None:
            raise ValueError("Unknown task in this Goal")
        return task

    @staticmethod
    def _replace_task(goal: Goal, task: Task) -> tuple[Task, ...]:
        return tuple(task if item.id == task.id else item for item in goal.tasks)

    async def create(
        self, title: str, outcome: str, criteria: tuple[str, ...], *, context: OperationContext | None = None
    ) -> Goal:
        require_user(context)

        async def commit() -> Goal:
            goal = Goal("g-" + uuid.uuid4().hex, title, outcome, criteria)
            await self.store.put(goal, expected_version=0)
            self._changed()
            return goal

        return await self._owned(commit())

    async def status(self, identifier: str) -> Goal:
        result = await self._get(identifier)
        self._owner()
        return result

    async def snapshot(self, identifier: str, version: int) -> Goal:
        from xdog.goal.models import number
        number(version)
        await self._get(identifier)
        original = await self.store.at_version(identifier, version)
        self._owner()
        if original.id != identifier or original.version != version:
            raise ValueError("Goal snapshot does not match its immutable selector")
        return original

    async def list(self) -> tuple[Goal, ...]:
        self._owner()
        result = await self.store.list()
        self._owner()
        return result

    async def revise(
        self,
        identifier: str,
        outcome: str,
        criteria: tuple[str, ...],
        reason: str,
        *,
        context: OperationContext | None = None,
    ) -> Goal:
        require_user(context)
        text(reason)

        async def commit() -> Goal:
            async with self._lock(identifier):
                goal = await self._get(identifier, live=True)
                return await self._save(
                    goal,
                    outcome=outcome,
                    criteria=criteria,
                    criteria_revision=goal.criteria_revision + 1,
                    review=replace(goal.review, state="invalid") if goal.review is not None else None,
                    status="paused" if goal.status == "paused" else "active",
                    wait_reason=reason,
                )

        return await self._owned(commit())

    async def add_task(self, identifier: str, description: str, *, depends_on: tuple[str, ...] = ()) -> Goal:
        async def commit() -> Goal:
            async with self._lock(identifier):
                goal = await self._get(identifier, live=True)
                if goal.status in {"paused", "reviewing"}:
                    raise ValueError("Paused/reviewing Goal cannot add autonomous work")
                task = Task("t-" + uuid.uuid4().hex, description, depends_on)
                return await self._save(goal, tasks=(*goal.tasks, task))

        return await self._owned(commit())

    async def assign(self, goal: str, task: str, assignment: str, *, access: str = "read-only") -> Goal:
        return await self._owned(self._workers.assign(goal, task, assignment, access))

    async def bind_worker(self, goal: str, task: str, attempt: str, reference: WorkerRef) -> Goal:
        """Host-only durable actual-admission binding before a worker can report.

        This records cancellation intent but does not await cancellation of the
        admitting child from inside that child's own construction callback.
        The host refuses its model admission if the returned task is cancelled.
        """
        return await self._owned(self._workers.bind(goal, task, attempt, reference))

    def bound_reporter(self, goal: str, task: str, attempt: str) -> BoundReporter:
        return BoundReporter(self, goal, task, attempt)

    async def report(self, goal: str, report: Report) -> Goal:
        """Host role entry; parent model tools do not expose this operation."""
        return await self._owned(self._workers.report(goal, report))

    async def accept_task(self, goal: str, task: str, attempt: str, revision: int, digest: str, summary: str) -> Goal:
        return await self._owned(self._workers.accept(goal, task, attempt, revision, digest, summary))

    async def cancel_task(self, goal: str, task: str, reason: str) -> Goal:
        return await self._owned(self._workers.cancel(goal, task, reason))

    async def _state(self, identifier: str, status: str, reason: str) -> Goal:
        text(reason)
        async with self._lock(identifier):
            goal = await self._get(identifier, live=True)
            if goal.status == "paused" and status not in {"paused", "abandoned"}:
                raise ValueError("User-paused Goal requires actual user resume authority")
            return await self._save(goal, status=status, wait_reason=reason)

    async def wait(self, identifier: str, reason: str) -> Goal:
        return await self._owned(self._state(identifier, "waiting", reason))

    async def pause(self, identifier: str, reason: str, *, context: OperationContext | None = None) -> Goal:
        require_user(context)
        return await self._owned(self._state(identifier, "paused", reason))

    async def resume(self, identifier: str, *, context: OperationContext | None = None) -> Goal:
        async def commit() -> Goal:
            async with self._lock(identifier):
                goal = await self._get(identifier, live=True)
                if goal.status == "paused":
                    require_user(context)
                return await self._save(goal, status="active", wait_reason="")

        return await self._owned(commit())

    async def abandon(self, identifier: str, reason: str) -> Goal:
        return await self._owned(self._state(identifier, "abandoned", reason))

    async def cancel_notifications(self, identifier: str) -> Goal:
        async def commit() -> Goal:
            async with self._lock(identifier):
                goal = await self._get(identifier)
                return await self._save(goal, notifications_enabled=False, cancel_notices=True)

        return await self._owned(commit())

    async def request_complete(self, identifier: str, *, paths: tuple[str, ...] = (), summary: str = "") -> Goal:
        return await self._owned(self._reviews.request(identifier, paths, summary))

    async def bind_reviewer(self, identifier: str, review_id: str, reference: WorkerRef) -> Goal:
        return await self._owned(self._reviews.bind(identifier, review_id, reference))

    async def cancel_review(self, identifier: str, reason: str) -> Goal:
        return await self._owned(self._reviews.cancel(identifier, reason))

    async def reconcile(self, identifier: str) -> Goal:
        async def facts() -> Goal:
            await self._workers.reconcile(identifier)
            return await self._reviews.reconcile(identifier)

        return await self._owned(facts())

    async def worker_context(self, identifier: str, task_id: str, attempt_id: str) -> Goal:
        await self._get(identifier)
        pointer = await self.store.artifact(identifier, "assignment-context:" + attempt_id)
        if not isinstance(pointer, dict) or type(pointer.get("version")) is not int:
            raise ValueError("Original worker contract unavailable; no current-context substitution")
        original = await self.store.at_version(identifier, pointer["version"])
        selected = self._task(original, task_id)
        if not any(item.id == attempt_id for item in selected.attempts):
            raise ValueError("Worker context belongs to another attempt")
        return original

    async def review_context(self, identifier: str, review_id: str) -> Goal:
        await self._get(identifier)
        pointer = await self.store.artifact(identifier, "review-context:" + review_id)
        if not isinstance(pointer, dict) or type(pointer.get("version")) is not int:
            raise ValueError("Original independent review contract unavailable")
        original = await self.store.at_version(identifier, pointer["version"])
        if original.review is None or original.review.id != review_id:
            raise ValueError("Review context belongs to another execution")
        return original

    async def export_views(self, identifier: str) -> Path:
        from xdog.goal.views import export

        return await export(await self._get(identifier), self.store.owner.workspace)

    async def drain_notifications(self) -> None:
        await self._owned(drain(self))

    async def close(self) -> None:
        if self._close_task is None:
            self._closing = True
            self._close_task = asyncio.create_task(self._close())
        await wait_owned(self._close_task)

    async def _close(self) -> None:
        # Host shutdown owns actual workers; UI unload must never cancel them.
        tasks = tuple(self._operations)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self.store.close()
