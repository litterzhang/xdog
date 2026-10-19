"""Goal tracker — persistent goal and task management.

Uses owned JSON persistence and immutable updates
via dataclasses.replace. Goals persist across session resets and drive
the background goal runner in the gateway daemon.
"""
from __future__ import annotations

import json
import logging
import os
import stat
import tempfile
import time
from dataclasses import asdict, replace
from dataclasses import fields as dc_fields
from pathlib import Path
from typing import Any

from xdog.claw.core.types import (
    Goal,
    GoalNotification,
    GoalStatus,
    GoalTask,
    TaskStatus,
    Verification,
    VerificationMethod,
    VerificationResult,
    VerificationRun,
)

logger = logging.getLogger(__name__)


def _short_hex_id(prefix: str) -> str:
    """Generate a short hex ID like 'g-a1b2' or 't-c3d4'."""
    return f"{prefix}-{os.urandom(8).hex()}"


def _load_verification(data: dict[str, Any] | None) -> Verification:
    """Deserialize a Verification from JSON dict. Returns default if missing."""
    if not data:
        return Verification()
    return Verification(
        method=VerificationMethod(data.get("method", "conditions")),
        script=data.get("script", ""),
        conditions=tuple(data.get("conditions", ())),
    )


def _load_verification_run(data: dict[str, Any] | None) -> VerificationRun | None:
    """Deserialize a VerificationRun from JSON dict. Returns None if missing."""
    if not data:
        return None
    return VerificationRun(
        result=VerificationResult(data.get("result", "failed")),
        output=data.get("output", ""),
        timestamp=data.get("timestamp", 0.0),
    )


def _load_goal_task(data: dict[str, Any]) -> GoalTask:
    """Deserialize a GoalTask from JSON dict with verification fields."""
    v_data = data.pop("verification", None)
    vr_data = data.pop("last_verification_run", None)
    depends_on = data.pop("depends_on", ())
    # Filter to known fields to handle forward-compatibility
    known = {f.name for f in dc_fields(GoalTask)} - {"verification", "last_verification_run", "depends_on"}
    filtered = {k: v for k, v in data.items() if k in known}
    filtered["status"] = TaskStatus(filtered.get("status", "pending"))
    return GoalTask(
        **filtered,
        depends_on=tuple(depends_on) if depends_on else (),
        verification=_load_verification(v_data) if v_data else None,
        last_verification_run=_load_verification_run(vr_data),
    )


class GoalTracker:
    """Manages goals with JSON persistence.

    Storage layout: ``{goals_file}`` contains a JSON object with
    ``goals`` (dict of goal dicts) and ``notifications`` (list of
    notification dicts).
    """

    # Max age for completed/abandoned goals (seconds). Older ones are
    # purged on load to prevent unbounded growth of goals.json.
    _GC_MAX_AGE = 7 * 86400  # 7 days

    def __init__(self, goals_file: Path) -> None:
        self._goals_file = goals_file
        self._goals: dict[str, Goal] = {}
        self._notifications: list[GoalNotification] = []
        self.diagnostic = ""
        self._load()
        self._gc()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_goal(
        self,
        group_id: str,
        title: str,
        description: str,
        task_descriptions: list[str],
        *,
        verification: Verification | None = None,
    ) -> Goal:
        """Create a new goal with tasks and verification criteria."""
        now = time.time()
        goal_id = _short_hex_id("g")
        tasks = tuple(
            GoalTask(id=_short_hex_id("t"), description=desc)
            for desc in task_descriptions
        )
        goal = Goal(
            id=goal_id,
            group_id=group_id,
            title=title,
            description=description,
            tasks=tasks,
            status=GoalStatus.ACTIVE,
            created_at=now,
            updated_at=now,
            verification=verification or Verification(),
        )
        return self.put(goal)

    def put(self, goal: Goal) -> Goal:
        """Publish an immutable replacement only after its durable commit."""
        previous = self._goals
        candidate = {**previous, goal.id: goal}
        self._goals = candidate
        try:
            self._save()
        except BaseException:
            self._goals = previous
            raise
        return goal

    def update_task(
        self,
        goal_id: str,
        task_id: str,
        status: TaskStatus,
        *,
        summary: str = "",
        notes: str = "",
    ) -> Goal:
        """Update a task's status within a goal. Returns the updated goal."""
        goal = self._get_or_raise(goal_id)
        if all(t.id != task_id for t in goal.tasks):
            raise ValueError(f"Task {task_id} not found in goal {goal_id}")
        new_tasks = tuple(
            replace(
                t,
                status=status,
                summary=summary if summary else t.summary,
                notes=notes if notes else t.notes,
            )
            if t.id == task_id
            else t
            for t in goal.tasks
        )
        updated = replace(goal, tasks=new_tasks, updated_at=time.time())
        self._goals[goal_id] = updated
        self._save()
        return updated

    def add_task(
        self, goal_id: str, description: str,
        *, depends_on: tuple[str, ...] = (),
    ) -> Goal:
        """Add a new task to an existing goal. Returns the updated goal."""
        goal = self._get_or_raise(goal_id)
        new_task = GoalTask(
            id=_short_hex_id("t"),
            description=description,
            depends_on=depends_on,
        )
        updated = replace(
            goal,
            tasks=(*goal.tasks, new_task),
            updated_at=time.time(),
        )
        self._goals[goal_id] = updated
        self._save()
        return updated

    def replace_tasks(self, goal_id: str, tasks: tuple[GoalTask, ...]) -> Goal:
        """Replace all tasks on a goal. Used to set dependencies after creation."""
        goal = self._get_or_raise(goal_id)
        updated = replace(goal, tasks=tasks, updated_at=time.time())
        self._goals[goal_id] = updated
        self._save()
        return updated

    def update_goal_status(
        self,
        goal_id: str,
        status: GoalStatus,
        *,
        summary: str = "",
    ) -> Goal:
        """Update the overall goal status. Returns the updated goal."""
        goal = self._get_or_raise(goal_id)
        updated = replace(
            goal,
            status=status,
            summary=summary if summary else goal.summary,
            updated_at=time.time(),
        )
        self._goals[goal_id] = updated
        self._save()
        return updated

    def record_verification(
        self,
        goal_id: str,
        run: VerificationRun,
    ) -> Goal:
        """Record a verification run result on a goal."""
        goal = self._get_or_raise(goal_id)
        updated = replace(goal, last_verification_run=run, updated_at=time.time())
        self._goals[goal_id] = updated
        self._save()
        return updated

    def list_goals(self, status_filter: str | None = None) -> list[Goal]:
        """List goals, optionally filtered by status.

        ``status_filter`` accepts ``"active"``, ``"completed"``,
        ``"abandoned"``, or ``"all"`` (default when *None*).
        """
        goals = list(self._goals.values())
        if status_filter and status_filter != "all":
            goals = [g for g in goals if g.status == status_filter]
        return goals

    def get_goal(self, goal_id: str) -> Goal | None:
        """Return a goal by ID, or None if not found."""
        return self._goals.get(goal_id)

    def build_active_summary(self) -> str:
        """Build a markdown summary of active goals for the system prompt."""
        active = [g for g in self._goals.values() if g.status == GoalStatus.ACTIVE]
        if not active:
            return ""
        lines = ["## Active Goals", ""]
        for goal in active:
            lines.append(f"### [{goal.id}] {goal.title}")
            for task in goal.tasks:
                if task.status == TaskStatus.COMPLETED:
                    lines.append(f"- [x] [{task.id}] {task.description}")
                elif task.status == TaskStatus.IN_PROGRESS:
                    lines.append(
                        f"- [ ] [{task.id}] **\u2192 {task.description}** (in_progress)"
                    )
                elif task.status == TaskStatus.SKIPPED:
                    lines.append(f"- [-] [{task.id}] ~~{task.description}~~ (skipped)")
                else:
                    lines.append(f"- [ ] [{task.id}] {task.description}")
            lines.append("")
        return "\n".join(lines).rstrip()

    def build_completed_summary(self, max_age_days: int = 7) -> str:
        """Build a markdown summary of recently completed goals."""
        cutoff = time.time() - (max_age_days * 86400)
        recent = [
            g
            for g in self._goals.values()
            if g.status in (GoalStatus.COMPLETED, GoalStatus.ABANDONED)
            and g.updated_at >= cutoff
        ]
        if not recent:
            return ""
        lines = ["## Recently Completed Goals", ""]
        for goal in recent:
            mark = "\u2713" if goal.status == GoalStatus.COMPLETED else "\u2717"
            lines.append(f"### [{goal.id}] {goal.title} {mark}")
            if goal.summary:
                lines.append(goal.summary)
            lines.append("")
        return "\n".join(lines).rstrip()

    def has_running_tasks(self) -> bool:
        """Return True if any active goal has an in-progress task."""
        return any(
            task.status == TaskStatus.IN_PROGRESS
            for goal in self._goals.values()
            if goal.status == GoalStatus.ACTIVE
            for task in goal.tasks
        )

    # ------------------------------------------------------------------
    # Notification queue
    # ------------------------------------------------------------------

    def add_notification(
        self,
        goal_id: str,
        title: str,
        summary: str,
        status: str,
    ) -> None:
        """Append a notification to the queue (persisted in goals.json)."""
        notif = GoalNotification(
            goal_id=goal_id,
            title=title,
            summary=summary,
            status=status,
            timestamp=time.time(),
        )
        self._notifications.append(notif)
        self._save()

    def pop_notifications(self) -> list[GoalNotification]:
        """Return and clear all pending notifications."""
        if not self._notifications:
            return []
        result = list(self._notifications)
        self._notifications.clear()
        self._save()
        return result

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _get_or_raise(self, goal_id: str) -> Goal:
        goal = self._goals.get(goal_id)
        if goal is None:
            raise ValueError(f"Goal {goal_id} not found")
        return goal

    def _load(self) -> None:
        if not self._goals_file.exists() and not self._goals_file.is_symlink():
            return
        try:
            def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
                result: dict[str, Any] = {}
                for key, value in values:
                    if key in result:
                        raise ValueError('Duplicate Goal journal key')
                    result[key] = value
                return result
            def constant(value: str) -> Any:
                raise ValueError('Non-finite Goal journal value')
            fd = os.open(self._goals_file, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    raise ValueError('Goal journal must be a regular file')
                with os.fdopen(fd, 'r', encoding='utf-8', closefd=False) as stream:
                    raw = json.load(stream, object_pairs_hook=pairs, parse_constant=constant)
            finally:
                os.close(fd)
            if (not isinstance(raw, dict) or set(raw) - {'version', 'goals', 'notifications'}
                    or type(raw.get('version', 1)) is not int or raw.get('version', 1) not in {1, 2}
                    or not isinstance(raw.get('goals'), dict) or not isinstance(raw.get('notifications', []), list)):
                raise ValueError('Unsupported or malformed Goal journal; nothing is resumed or overwritten')
            from xdog.claw.core.planning.task_reports import _validate_id
            goal_fields = {field.name for field in dc_fields(Goal)}
            task_fields = {field.name for field in dc_fields(GoalTask)}
            restored: dict[str, Goal] = {}
            for gid, saved in raw['goals'].items():
                _validate_id(gid, 'goal_id')
                if (not isinstance(saved, dict) or set(saved) - goal_fields or saved.get('id') != gid
                        or not isinstance(saved.get('group_id'), str) or not saved['group_id']):
                    raise ValueError('Invalid Goal identity or unknown fields')
                gdata = dict(saved)
                tasks_raw = gdata.pop('tasks', [])
                if (not isinstance(tasks_raw, list) or any(not isinstance(item, dict)
                        or set(item) - task_fields for item in tasks_raw)):
                    raise ValueError('Invalid task data; no partial Goal restore')
                tasks = tuple(_load_goal_task(dict(item)) for item in tasks_raw)
                ids = {task.id for task in tasks}
                if len(ids) != len(tasks):
                    raise ValueError('Duplicate task identity')
                for task in tasks:
                    _validate_id(task.id, 'task_id')
                    if task.attempt_id:
                        _validate_id(task.attempt_id, 'attempt_id')
                    if (type(task.report_revision) is not int or task.report_revision < 0
                            or any(not isinstance(item, str) for item in task.depends_on)):
                        raise ValueError('Invalid task revision/dependencies')
                verification = _load_verification(gdata.pop('verification', None))
                run = _load_verification_run(gdata.pop('last_verification_run', None))
                gdata['status'] = GoalStatus(gdata.get('status', 'active'))
                goal = Goal(**gdata, tasks=tasks, verification=verification, last_verification_run=run)
                if (type(goal.revision) is not int or goal.revision < 1
                        or type(goal.review_revision) is not int or goal.review_revision < 0
                        or type(goal.auto_continue) is not bool
                        or goal.phase not in {'working', 'waiting', 'review_requested', 'reviewing', 'complete'}
                        or goal.notice_state not in {'pending', 'running', 'interrupted', 'acknowledged'}
                        or any(not isinstance(item, str) for item in goal.verification.conditions)):
                    raise ValueError('Invalid Goal lifecycle state')
                restored[gid] = goal
            notifications = [GoalNotification(**item) for item in raw.get('notifications', [])]
            self._goals, self._notifications = restored, notifications
        except (OSError, ValueError, TypeError, UnicodeError, RecursionError) as exc:
            # Do not silently skip records and overwrite them on the next create.
            # The group may still chat; all Goal writes stay disabled until repair/reload.
            self._goals, self._notifications = {}, []
            self.diagnostic = 'Goal journal unavailable; repair and reload before Goal work: ' + str(exc)[:300]
            logger.error('%s', self.diagnostic)

    def _save(self) -> None:
        if self.diagnostic:
            raise ValueError(self.diagnostic)
        from xdog.claw.core.planning.goal_workspace import ensure_private_directory
        ensure_private_directory(self._goals_file.parent)
        goals_data: dict[str, Any] = {}
        for gid, goal in self._goals.items():
            d = asdict(goal)
            goals_data[gid] = d
        data = {
            "version": 2,
            "goals": goals_data,
            "notifications": [asdict(n) for n in self._notifications],
        }
        if self._goals_file.is_symlink():
            raise ValueError("Goal state must not be a symlink")
        fd, name = tempfile.mkstemp(prefix=".goals-", dir=self._goals_file.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(data, stream, indent=2, allow_nan=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self._goals_file)
            directory = os.open(self._goals_file.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def _gc(self) -> None:
        """Purge completed/abandoned goals older than _GC_MAX_AGE."""
        cutoff = time.time() - self._GC_MAX_AGE
        stale = [
            gid for gid, g in self._goals.items()
            if g.status in (GoalStatus.COMPLETED, GoalStatus.ABANDONED)
            and g.updated_at < cutoff
        ]
        if stale:
            for gid in stale:
                del self._goals[gid]
                logger.info("Garbage collected old goal: %s", gid)
            self._save()


# ---------------------------------------------------------------------------
# Cached factory
# ---------------------------------------------------------------------------

_tracker_cache: dict[str, tuple[float, GoalTracker]] = {}


def get_tracker(goals_file: Path) -> GoalTracker:
    """Get a GoalTracker with mtime-based caching."""
    key = str(goals_file)
    mtime = goals_file.stat().st_mtime if goals_file.exists() else 0
    cached = _tracker_cache.get(key)
    if cached and cached[0] == mtime:
        return cached[1]
    tracker = GoalTracker(goals_file)
    _tracker_cache[key] = (mtime, tracker)
    return tracker
