"""Goal-owned derived files. The journal, never these files, is authoritative."""

from __future__ import annotations

import json
import os
import stat
import uuid
from dataclasses import asdict
from pathlib import Path

from xdog.agent.async_work import run_owned
from xdog.skills.goal.evidence import _DIR, _root
from xdog.skills.goal.models import Goal


def _directory(parent: int, name: str) -> int:
    try:
        os.mkdir(name, 0o700, dir_fd=parent)
        os.fsync(parent)
    except FileExistsError:
        pass
    return os.open(name, _DIR, dir_fd=parent)


def _write(directory: int, name: str, data: str) -> None:
    try:
        existing = os.stat(name, dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        pass
    else:
        if not stat.S_ISREG(existing.st_mode):
            raise ValueError("Derived Goal views cannot replace links or special files")
    temporary = ".goal-" + uuid.uuid4().hex
    fd = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=directory
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)
    finally:
        try:
            os.unlink(temporary, dir_fd=directory)
        except FileNotFoundError:
            pass


def materialize(goal: Goal, workspace: Path) -> Path:
    fd = _root(workspace)
    try:
        for name in (".xdog", "goals", goal.id):
            child = _directory(fd, name)
            os.close(fd)
            fd = child
        contract = {
            "id": goal.id,
            "title": goal.title,
            "outcome": goal.outcome,
            "criteria_revision": goal.criteria_revision,
            "criteria": goal.criteria,
            "derived_view": True,
            "authority": "GoalStore, not this file",
        }
        _write(fd, "contract.json", json.dumps(contract, indent=2, ensure_ascii=False) + "\n")
        _write(fd, "status.json", json.dumps(asdict(goal), indent=2, ensure_ascii=False) + "\n")
        _write(fd, "plan.md", f"# {goal.title}\n\n{goal.summary or goal.outcome}\n")
        _write(fd, "review.json", json.dumps(asdict(goal.review) if goal.review else None, indent=2) + "\n")
        tasks = _directory(fd, "tasks")
        try:
            for task in goal.tasks:
                target = _directory(tasks, task.id)
                try:
                    latest = task.attempts[-1] if task.attempts else None
                    _write(
                        target,
                        "assignment.md",
                        f"# {task.description}\n\nTask: {task.id}\n"
                        + (f"Attempt: {latest.id}\n\n{latest.assignment}\n" if latest else "Not assigned\n"),
                    )
                    report = latest.reports[-1] if latest and latest.reports else None
                    _write(target, "report.json", json.dumps(asdict(report) if report else None, indent=2) + "\n")
                finally:
                    os.close(target)
        finally:
            os.close(tasks)
    except OSError as exc:
        raise ValueError("Goal derived views could not be written safely") from exc
    finally:
        os.close(fd)
    return workspace / ".xdog" / "goals" / goal.id


async def export(goal: Goal, workspace: Path) -> Path:
    return await run_owned(lambda: materialize(goal, workspace))
