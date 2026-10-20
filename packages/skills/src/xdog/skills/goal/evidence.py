"""Goal-local bounded regular-file evidence, descriptor-relative and no-follow."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import stat
from dataclasses import asdict
from pathlib import Path

from xdog.skills.goal.models import Goal

MAX_FILES = 10_000
MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
_IGNORED = frozenset(
    {".git", ".venv", ".xdog", ".pytest_cache", ".mypy_cache", ".ruff_cache", "node_modules", "__pycache__"}
)
_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


def _root(path: Path) -> int:
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("Evidence workspace must be host-bound absolute data")
    fd = os.open("/", _DIR)
    try:
        for part in path.parts[1:]:
            child = os.open(part, _DIR, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def snapshot(goal: Goal, workspace: Path, paths: tuple[str, ...] = (), *, excluded: tuple[str, ...] = ()) -> str:
    if type(paths) is not tuple or any(not isinstance(item, str) for item in paths):
        raise ValueError("Evidence selections must be immutable relative paths")
    selected = paths or (".",)
    for value in selected:
        path = Path(value)
        if not value or "\x00" in value or path.is_absolute() or ".." in path.parts or any(c in value for c in "*?["):
            raise ValueError("Review evidence must be explicit confined relative paths")
    digest = hashlib.sha256()
    digest.update(
        json.dumps(
            {
                "goal": goal.id,
                "criteria_revision": goal.criteria_revision,
                "outcome": goal.outcome,
                "criteria": goal.criteria,
                "tasks": [asdict(task) for task in goal.tasks],
            },
            sort_keys=True,
        ).encode()
    )
    total = 0
    files = 0
    seen: set[str] = set()

    def inspect(fd: int, name: str, relative: Path, *, explicit: bool, depth: int = 0) -> None:
        nonlocal total, files
        if depth > 64:
            raise ValueError("Evidence tree depth exceeds resource capacity")
        label = relative.as_posix()
        if label in seen:
            return
        if not explicit and (
            name in _IGNORED or any(relative == Path(item) or relative.is_relative_to(item) for item in excluded)
        ):
            return
        try:
            before = os.stat(name, dir_fd=fd, follow_symlinks=False)
        except (FileNotFoundError, NotADirectoryError):
            seen.add(label)
            digest.update(json.dumps([label, "absent"]).encode())
            return
        if stat.S_ISLNK(before.st_mode) or not (stat.S_ISDIR(before.st_mode) or stat.S_ISREG(before.st_mode)):
            raise ValueError("Evidence must not select/traverse symlinks or non-regular data")
        child = os.open(
            name,
            _DIR if stat.S_ISDIR(before.st_mode) else os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
            dir_fd=fd,
        )
        try:
            actual = os.fstat(child)
            if (actual.st_ino, actual.st_dev, actual.st_mode) != (before.st_ino, before.st_dev, before.st_mode):
                raise ValueError("Evidence changed during selection")
            seen.add(label)
            if stat.S_ISDIR(actual.st_mode):
                digest.update(json.dumps([label, "directory"]).encode())
                for entry in sorted(os.listdir(child)):
                    inspect(child, entry, relative / entry, explicit=False, depth=depth + 1)
            else:
                files += 1
                if files > MAX_FILES or actual.st_size > MAX_FILE_BYTES:
                    raise ValueError("Evidence exceeds bounded file capacity; nothing silently omitted")
                content = hashlib.sha256()
                size = 0
                while chunk := os.read(child, 64 * 1024):
                    size += len(chunk)
                    total += len(chunk)
                    if size > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
                        raise ValueError("Evidence exceeds bounded byte capacity; nothing silently truncated")
                    content.update(chunk)
                after = os.fstat(child)
                if (actual.st_size, actual.st_mtime_ns, actual.st_ctime_ns) != (
                    after.st_size,
                    after.st_mtime_ns,
                    after.st_ctime_ns,
                ):
                    raise ValueError("Evidence changed while being read")
                digest.update(json.dumps([label, size, content.hexdigest()]).encode())
        finally:
            os.close(child)

    fd = _root(workspace)
    try:
        for selection in selected:
            current = os.dup(fd)
            try:
                path = Path(selection)
                parts = path.parts or (".",)
                missing = False
                for part in parts[:-1]:
                    try:
                        child = os.open(part, _DIR, dir_fd=current)
                    except FileNotFoundError:
                        missing = True
                        break
                    os.close(current)
                    current = child
                if missing:
                    digest.update(json.dumps([path.as_posix(), "absent"]).encode())
                else:
                    inspect(current, parts[-1], path, explicit=True)
            finally:
                os.close(current)
    except OSError as exc:
        raise ValueError("Evidence cannot be read safely") from exc
    finally:
        os.close(fd)
    return digest.hexdigest()


async def fingerprint(
    goal: Goal, workspace: Path, paths: tuple[str, ...] = (), *, excluded: tuple[str, ...] = ()
) -> str:
    return await asyncio.to_thread(snapshot, goal, workspace, paths, excluded=excluded)
