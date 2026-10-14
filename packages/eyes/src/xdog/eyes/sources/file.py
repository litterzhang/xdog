"""Bounded, no-follow content scans. Failed scans never become partial snapshots."""
from __future__ import annotations

import asyncio
import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from xdog.eyes.errors import SourceError
from xdog.eyes.models import FileState, SourceDef
from xdog.eyes.sources.patterns import matches


@dataclass(frozen=True)
class FileSnapshot:
    entries: tuple[tuple[str, FileState], ...]


def _root_fd(path: Path) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    current = os.open("/", flags)
    try:
        for part in path.parts[1:]:
            child = os.open(part, flags, dir_fd=current)
            os.close(current)
            current = child
        return current
    except BaseException:
        os.close(current)
        raise


def _identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns


def _fingerprint(directory: int, name: str, expected: os.stat_result, maximum: int) -> FileState:
    if expected.st_size > maximum:
        raise SourceError(f"File exceeds {maximum} bytes: {name}")
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or _identity(opened) != _identity(expected):
            raise SourceError(f"File changed during scan: {name}")
        digest, total = hashlib.sha256(), 0
        while block := os.read(fd, min(64 * 1024, maximum - total + 1)):
            total += len(block)
            if total > maximum:
                raise SourceError(f"File exceeds {maximum} bytes: {name}")
            digest.update(block)
        after = os.fstat(fd)
        named = os.stat(name, dir_fd=directory, follow_symlinks=False)
        if (_identity(after) != _identity(opened) or _identity(named) != _identity(opened)
                or not stat.S_ISREG(named.st_mode) or total != opened.st_size):
            raise SourceError(f"File changed during scan: {name}")
        return FileState(digest.hexdigest(), total)
    finally:
        os.close(fd)


def scan(source: SourceDef) -> FileSnapshot:
    entries: list[tuple[str, FileState]] = []

    def visit(directory: int, prefix: str) -> None:
        with os.scandir(directory) as iterator:
            for entry in iterator:
                relative = prefix + entry.name
                metadata = entry.stat(follow_symlinks=False)
                if stat.S_ISLNK(metadata.st_mode):
                    continue
                if stat.S_ISDIR(metadata.st_mode):
                    if any(matches(relative + "/", pattern) for pattern in source.exclude):
                        continue
                    child = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                    try:
                        visit(child, relative + "/")
                    finally:
                        os.close(child)
                elif (stat.S_ISREG(metadata.st_mode)
                      and any(matches(relative, pattern) for pattern in source.include)
                      and not any(matches(relative, pattern) for pattern in source.exclude)):
                    if len(entries) >= source.max_files:
                        raise SourceError(f"Source exceeds {source.max_files} matching files")
                    entries.append((relative, _fingerprint(directory, entry.name, metadata, source.max_file_bytes)))

    try:
        root = _root_fd(source.root)
        try:
            visit(root, "")
        finally:
            os.close(root)
    except OSError as exc:
        raise SourceError(f"Cannot scan {source.root}: {exc}") from exc
    return FileSnapshot(tuple(sorted(entries)))


async def scan_async(source: SourceDef) -> FileSnapshot:
    """Cancellation waits for already-started I/O before releasing the owner."""
    task = asyncio.create_task(asyncio.to_thread(scan, source))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except SourceError:
                break
        if task.done() and not task.cancelled():
            task.exception()  # Consume a failed scan while preserving cancellation.
        raise
