"""Goal workspace — manages goal.md, status.md, plan.md per goal.

Each goal gets its own directory at ``{goals_dir}/{goal_id}/`` with
three structured markdown files:

- ``goal.md``   — definition, description, verification criteria (immutable)
- ``status.md`` — live task checklist, verification results
- ``plan.md``   — strategy, decisions, adaptations (decision log)

These files are human-readable AND serve as the goal agent's context.
"""
from __future__ import annotations

import json
import logging
import os
import stat
import uuid
from datetime import datetime, timezone
from pathlib import Path

from xdog.claw.core.planning.task_reports import _parent, _validate_id
from xdog.claw.core.types import (
    Goal,
    GoalTask,
    TaskStatus,
    Verification,
    VerificationMethod,
)

logger = logging.getLogger(__name__)


def ensure_private_directory(path: Path) -> None:
    """Create private directories without following any symlink component."""
    path = path.absolute()
    if '..' in path.parts:
        raise ValueError("Goal workspace cannot traverse parent components")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    directory = os.open('/', flags)
    try:
        for part in path.parts[1:]:
            try:
                os.mkdir(part, 0o700, dir_fd=directory)
            except FileExistsError:
                pass
            # Also sync existing entries: they may be visible from a prior
            # mkdir whose parent fsync failed before the caller got an ACK.
            os.fsync(directory)
            child = os.open(part, flags, dir_fd=directory)
            os.close(directory)
            directory = child
    finally:
        os.close(directory)


class GoalWorkspace:
    """Manages markdown files in a goal's workspace directory.

    Pure I/O — no LLM calls, no GoalTracker dependency.
    """

    def __init__(self, goals_dir: Path) -> None:
        self._goals_dir = goals_dir.absolute()

    def goal_dir(self, goal_id: str) -> Path:
        _validate_id(goal_id, "goal_id")
        return self._goals_dir / goal_id

    # -- Lifecycle --------------------------------------------------------

    def init_workspace(self, goal: Goal) -> None:
        """Create goal workspace with all three files."""
        d = self.goal_dir(goal.id)
        self._mkdir(d)
        self.write_goal(goal)
        self.write_status(goal)
        initial_plan = (
            f"# Plan: {goal.title}\n\n"
            "## Current Strategy\n"
            "(The group main Agent maintains the strategy; no automatic planner)\n\n"
            "## Decision Log\n"
        )
        self.write_plan(goal.id, initial_plan)

    # -- goal.md (written once at creation) -------------------------------

    def write_goal(self, goal: Goal) -> None:
        """Write goal.md — definition, description, verification criteria."""
        lines = [f"# {goal.title}", ""]
        lines.append(f"Created: {_format_timestamp(goal.created_at)}")
        lines.append(f"Group: {goal.group_id}")
        lines.append("")

        lines.append("## Description")
        lines.append(goal.description or "(no description)")
        lines.append("")

        lines.append("## Verification")
        lines.extend(_format_verification(goal.verification))
        lines.append("")

        self._write(goal.id, "goal.md", "\n".join(lines))
        self._write(goal.id, 'contract.json', json.dumps({'format': 'xdog.goal.contract.v1',
            'goal_id': goal.id, 'description': goal.description,
            'conditions': list(goal.verification.conditions)}, ensure_ascii=False, indent=2))

    def read_goal(self, goal_id: str) -> str:
        """Read goal.md content."""
        return self._read(goal_id, "goal.md")

    # -- status.md (updated on task/verification changes) -----------------

    def write_status(self, goal: Goal) -> None:
        """Write status.md — task checklist and verification results."""
        lines = [f"# Status: {goal.title}", ""]
        lines.append(f"Status: {goal.status}")

        if goal.last_verification_run:
            vr = goal.last_verification_run
            ts = _format_timestamp(vr.timestamp)
            lines.append(f"Last verified: {ts} — {vr.result.upper()}")
            if vr.output:
                lines.append(f"Verification output: {vr.output[:200]}")
        lines.append("")

        lines.append("## Tasks")
        for task in goal.tasks:
            lines.append(_format_task_line(task))
            if task.summary:
                lines.append(f"  Summary: {task.summary}")
        lines.append("")

        self._write(goal.id, "status.md", "\n".join(lines))

    def read_status(self, goal_id: str) -> str:
        """Read status.md content."""
        return self._read(goal_id, "status.md")

    # -- plan.md (strategy + decision log) --------------------------------

    def read_plan(self, goal_id: str) -> str:
        """Read plan.md content."""
        return self._read(goal_id, "plan.md")

    def write_plan(self, goal_id: str, content: str) -> None:
        """Overwrite plan.md entirely."""
        self._write(goal_id, "plan.md", content)

    def append_decision(
        self,
        goal_id: str,
        trigger: str,
        decisions: list[str],
        strategy: str = "",
    ) -> None:
        """Append a decision entry to the Decision Log in plan.md."""
        ts = _format_timestamp()
        lines = [f"### {ts} — {trigger}"]
        for d in decisions:
            lines.append(f"- {d}")
        if strategy:
            lines.append(f"- Strategy: {strategy}")
        lines.append("")

        current = self.read_plan(goal_id)
        updated = current.rstrip() + "\n" + "\n".join(lines) + "\n"
        self.write_plan(goal_id, updated)

    # -- Private I/O ------------------------------------------------------

    def write_assignment(self, goal_id: str, task_id: str, content: str) -> None:
        self._write(goal_id, f"tasks/{task_id}/assignment.md", content)

    def write_review(self, goal_id: str, review_id: str, verdict: object) -> None:
        self._write(goal_id, f"reviews/{review_id}.json", json.dumps(verdict, ensure_ascii=False, indent=2))

    _mkdir = staticmethod(ensure_private_directory)

    def ensure_task_dir(self, goal_id: str, task_id: str = '') -> Path:
        root = self.goal_dir(goal_id) / 'tasks'
        if task_id:
            _validate_id(task_id, 'task_id')
            root = root / task_id
        self._mkdir(root)
        return root

    def _write(self, goal_id: str, filename: str, content: str) -> None:
        target = self.goal_dir(goal_id) / filename
        if '..' in target.parts:
            raise ValueError('Unsafe goal workspace path')
        self._mkdir(target.parent)
        with _parent(target) as (directory, name):
            try:
                if not stat.S_ISREG(os.stat(name, dir_fd=directory, follow_symlinks=False).st_mode):
                    raise ValueError('Goal workspace destination must be a regular file')
            except FileNotFoundError:
                pass
            temporary = '.goal-' + uuid.uuid4().hex + '.tmp'
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=directory)
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
                os.fsync(directory)
            finally:
                try:
                    os.unlink(temporary, dir_fd=directory)
                except FileNotFoundError:
                    pass

    def _read(self, goal_id: str, filename: str) -> str:
        path = self.goal_dir(goal_id) / filename
        try:
            with _parent(path) as (directory, name):
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
                try:
                    if not stat.S_ISREG(os.fstat(fd).st_mode):
                        raise ValueError('Goal workspace documents must be regular files')
                    with os.fdopen(fd, 'rb', closefd=False) as stream:
                        data = stream.read(1024 * 1024 + 1)
                finally:
                    os.close(fd)
            if len(data) > 1024 * 1024:
                raise ValueError('Goal document exceeds one MiB; archive explicitly, never truncate')
            return data.decode('utf-8')
        except FileNotFoundError:
            logger.warning('Goal workspace file missing: %s', path)
            return ''


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _format_timestamp(ts: float | None = None) -> str:
    """Format a Unix timestamp as ISO-8601. Uses now if ts is None or 0."""
    if not ts:
        dt = datetime.now(timezone.utc)
    else:
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _format_verification(v: Verification) -> list[str]:
    """Format verification criteria for goal.md."""
    lines = [f"Method: {v.method}"]
    if v.method == VerificationMethod.SCRIPT:
        lines.append(f"Historical script reference (not a completion verdict): `{v.script}`")
    elif v.conditions:
        for c in v.conditions:
            lines.append(f"- {c}")
    return lines


def _format_task_line(task: GoalTask) -> str:
    """Format a single task as a markdown checklist line."""
    status_map = {
        TaskStatus.COMPLETED: "[x]",
        TaskStatus.IN_PROGRESS: "[>]",
        TaskStatus.SKIPPED: "[-]",
        TaskStatus.PENDING: "[ ]",
    }
    icon = status_map.get(task.status, "[ ]")
    suffix = f" — {task.status}" if task.status != TaskStatus.PENDING else ""
    line = f"- {icon} [{task.id}] {task.description}{suffix}"
    if task.depends_on:
        line += f" (depends on: {', '.join(task.depends_on)})"
    return line
