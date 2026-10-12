"""Single in-process authority for persisted cron/interval/one-off tasks."""
from __future__ import annotations

import json
import logging
import math
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from xdog.claw.core.types import ScheduledTask, TaskSchedule

logger = logging.getLogger(__name__)


def next_fire(schedule: TaskSchedule, after: float) -> float:
    """Validate a schedule and compute its next occurrence, never a fallback interval."""
    if sum(value is not None for value in (schedule.cron, schedule.run_at, schedule.interval_seconds)) != 1:
        raise ValueError("Specify exactly one of cron, run_at, interval_seconds")
    if schedule.run_at is not None:
        if not math.isfinite(schedule.run_at):
            raise ValueError("run_at must be finite")
        return schedule.run_at
    if schedule.interval_seconds is not None:
        if not math.isfinite(schedule.interval_seconds) or schedule.interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive and finite")
        return after + schedule.interval_seconds
    if not schedule.timezone:
        raise ValueError("cron requires an explicit IANA timezone (e.g. Asia/Shanghai)")
    try:
        zone = ZoneInfo(schedule.timezone)
    except (KeyError, ValueError) as exc:
        raise ValueError(f"Invalid timezone: {schedule.timezone}") from exc
    try:
        from croniter import croniter
    except ImportError as exc:
        raise ValueError("cron requires the croniter dependency; task not scheduled") from exc
    if not schedule.cron or len(schedule.cron.split()) != 5:
        raise ValueError("cron must contain exactly five fields")
    try:
        return float(croniter(schedule.cron, datetime.fromtimestamp(after, zone)).get_next(float))
    except (ValueError, KeyError, OverflowError) as exc:
        raise ValueError(f"Invalid cron: {schedule.cron}") from exc


class TaskScheduler:
    """One service shared by all tool contexts and the orchestrator.

    Mutations are serialized and atomically replaced on disk before publication.
    This is a single-process store, not an external-side-effect exactly-once system.
    """

    def __init__(self, tasks_file: Path, *, clock: Callable[[], float] = time.time) -> None:
        self._tasks_file = tasks_file
        self._clock = clock
        self._lock = threading.RLock()
        self._tasks = self._load()

    def now(self) -> float:
        return self._clock()

    def add_task(self, task: ScheduledTask) -> None:
        with self._lock:
            if not task.id or not task.group_id.strip() or not task.prompt.strip():
                raise ValueError("Task id, group_id and prompt must not be empty")
            if task.id in self._tasks:
                raise ValueError(f"Task already exists: {task.id}")
            now = self.now()
            task = replace(task, created_at=now, next_run=next_fire(task.schedule, now))
            self._commit({**self._tasks, task.id: task})

    def remove_task(self, task_id: str) -> None:
        with self._lock:
            self._commit({tid: task for tid, task in self._tasks.items() if tid != task_id})

    def get_task(self, task_id: str) -> ScheduledTask | None:
        with self._lock:
            return self._tasks.get(task_id)

    def list_tasks(self) -> list[ScheduledTask]:
        with self._lock:
            return list(self._tasks.values())

    def get_due_tasks(self) -> list[ScheduledTask]:
        with self._lock:
            now = self.now()
            return [task for task in self._tasks.values() if task.enabled and task.status != "running"
                    and task.next_run is not None and task.next_run <= now]

    def start_run(self, task_id: str) -> ScheduledTask | None:
        """Persist admission before waiting for the normal Agent queue."""
        with self._lock:
            task = self._tasks.get(task_id)
            if (task is None or not task.enabled or task.status == "running"
                    or task.next_run is None or task.next_run > self.now()):
                return None
            task = replace(task, status="running", last_attempt=self.now(), last_error=None,
                           delivery_status="pending")
            self._commit({**self._tasks, task.id: task})
            return task

    def finish_run(self, task_id: str, *, error: str | None = None, delivered: bool = False) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:  # Cancellation must not resurrect a task.
                return
            now = self.now()
            error = error or (None if delivered else "No report delivered")
            enabled = task.schedule.run_at is None
            upcoming = None
            if enabled:
                try:
                    # Coalesce missed occurrences; never replay a burst after a slow run.
                    upcoming = next_fire(task.schedule, now)
                except ValueError as exc:
                    enabled = False
                    error = error or str(exc)
            task = replace(task, enabled=enabled, next_run=upcoming,
                           last_run=now if error is None else task.last_run,
                           status="failed" if error else "succeeded", last_error=error,
                           delivery_status="delivered" if delivered else "failed")
            self._commit({**self._tasks, task.id: task})

    def mark_run(self, task_id: str) -> None:
        """Record a confirmed successful execution and delivery (SDK compatibility)."""
        self.finish_run(task_id, delivered=True)

    def _load(self) -> dict[str, ScheduledTask]:
        if not self._tasks_file.exists():
            return {}
        try:
            raw = json.loads(self._tasks_file.read_text(encoding="utf-8"))
            # One explicit migration for the old task tool's incompatible envelope.
            if isinstance(raw, dict) and isinstance(raw.get("tasks"), list):
                records = raw["tasks"]
                raw = {record["id"]: record for record in records}
                if len(raw) != len(records):
                    raise ValueError("Duplicate task IDs")
            if not isinstance(raw, dict):
                raise ValueError("Expected task ID -> record mapping")
            result = {}
            for tid, data in raw.items():
                data = dict(data)
                schedule = TaskSchedule(**data.pop("schedule"))
                task = ScheduledTask(**data, schedule=schedule)
                if tid != task.id or not task.group_id.strip() or not task.prompt.strip():
                    raise ValueError(f"Invalid task identity: {tid}")
                if task.status == "running":
                    # A crash may have happened after a side effect. Require operator review.
                    task = replace(task, enabled=False, status="interrupted", delivery_status="unknown",
                                   last_error="Interrupted execution; review before rescheduling", next_run=None)
                elif task.enabled:
                    try:
                        upcoming = next_fire(schedule, task.last_run or task.created_at or self.now())
                        task = replace(task, next_run=task.next_run if task.next_run is not None else upcoming)
                    except ValueError as exc:
                        task = replace(task, enabled=False, status="failed", last_error=str(exc), next_run=None)
                        logger.error("Disabled scheduled task %s: %s", tid, exc)
                result[tid] = task
            if raw != {tid: asdict(task) for tid, task in result.items()}:
                self._commit(result)  # Persist migration/recovery so another restart sees the same state.
            return result
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise ValueError(f"Invalid task store {self._tasks_file}: {exc}") from exc

    def _commit(self, tasks: dict[str, ScheduledTask]) -> None:
        self._tasks_file.parent.mkdir(parents=True, exist_ok=True)
        temp = self._tasks_file.with_name(self._tasks_file.name + ".tmp")
        try:
            temp.write_text(json.dumps({tid: asdict(task) for tid, task in tasks.items()}, indent=2) + "\n",
                            encoding="utf-8")
            temp.replace(self._tasks_file)
        finally:
            temp.unlink(missing_ok=True)
        self._tasks = tasks
