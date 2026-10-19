"""Data-only worker reports; no goal transitions, runtime handles or tool policy.

All paths are opened component-by-component with no symlink traversal. Parents
must already exist; ``..`` is rejected. These synchronous bounded I/O helpers
should be offloaded by asynchronous callers. They are not an OS sandbox.
"""
from __future__ import annotations

import json
import os
import re
import stat
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Literal

__all__ = ["MAX_REPORT_BYTES", "TaskReport", "TaskReportError", "atomic_write_report", "read_report", "sync_report"]

MAX_REPORT_BYTES = 64 * 1024
TaskReportState = Literal["running", "submitted", "blocked"]
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z", re.ASCII)
_FIELDS = frozenset({"task_id", "attempt_id", "revision", "state", "summary", "evidence", "blockers"})


class TaskReportError(ValueError):
    """Malformed, unsafe, unreadable or unwritable report (OSError is chained)."""


def _validate_id(value: object, field: str) -> None:
    if type(value) is not str or _ID.fullmatch(value) is None:
        raise TaskReportError(f"{field} must be a safe ASCII ID (1..128 characters)")


@dataclass(frozen=True, slots=True)
class TaskReport:
    """Immutable report snapshot. Revision starts at 1 within one attempt.

    JSON evidence/blockers are arrays of strings; Python snapshots use tuples.
    A submitted report is a proposal for review, not acceptance or completion.
    """

    task_id: str
    attempt_id: str
    revision: int
    state: TaskReportState
    summary: str
    evidence: tuple[str, ...]
    blockers: tuple[str, ...]

    def __post_init__(self) -> None:
        _validate_id(self.task_id, "task_id")
        _validate_id(self.attempt_id, "attempt_id")
        if type(self.revision) is not int or self.revision < 1:
            raise TaskReportError("revision must be a positive integer, not bool")
        if type(self.state) is not str or self.state not in {"running", "submitted", "blocked"}:
            raise TaskReportError("state must be running, submitted or blocked")
        if type(self.summary) is not str:
            raise TaskReportError("summary must be a string")
        for name in ("evidence", "blockers"):
            value = getattr(self, name)
            if type(value) is not tuple or any(type(item) is not str for item in value):
                raise TaskReportError(f"{name} must be a tuple of strings")
        # Reject invalid Unicode even for directly constructed snapshots.
        for text in (self.summary, *self.evidence, *self.blockers):
            try:
                text.encode("utf-8", errors="strict")
            except UnicodeError as exc:
                raise TaskReportError("report strings must be valid UTF-8") from exc

    def to_dict(self) -> dict[str, object]:
        """Return only the seven JSON-compatible schema fields."""
        return {
            "task_id": self.task_id,
            "attempt_id": self.attempt_id,
            "revision": self.revision,
            "state": self.state,
            "summary": self.summary,
            "evidence": list(self.evidence),
            "blockers": list(self.blockers),
        }

    @classmethod
    def from_dict(cls, data: object) -> TaskReport:
        """Validate an exact JSON schema; no coercion or unknown fields."""
        if type(data) is not dict or set(data) != _FIELDS:
            raise TaskReportError("report must be an object with exactly the seven schema fields")
        _validate_id(data["task_id"], "task_id")
        _validate_id(data["attempt_id"], "attempt_id")
        for name in ("evidence", "blockers"):
            if type(data[name]) is not list or any(type(item) is not str for item in data[name]):
                raise TaskReportError(f"{name} must be a JSON array of strings")
        return cls(
            task_id=data["task_id"], attempt_id=data["attempt_id"], revision=data["revision"],
            state=data["state"], summary=data["summary"],
            evidence=tuple(data["evidence"]), blockers=tuple(data["blockers"]),
        )


def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise TaskReportError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _constant(value: str) -> object:
    raise TaskReportError(f"non-finite JSON constant: {value}")


@contextmanager
def _parent(path: str | os.PathLike[str]) -> Iterator[tuple[int, str]]:
    target = Path(path)
    if not target.name or target.name in {".", ".."} or ".." in target.parts:
        raise TaskReportError("report path must name a file without '..' components")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    directory = os.open(target.anchor or ".", flags)
    try:
        components = target.parts[1:-1] if target.is_absolute() else target.parts[:-1]
        for component in components:
            child = os.open(component, flags, dir_fd=directory)
            os.close(directory)
            directory = child
        yield directory, target.name
    finally:
        os.close(directory)


def read_report(
    path: str | os.PathLike[str], expected_task: str, expected_attempt: str,
) -> TaskReport:
    """Read a bounded regular file and validate exact task/attempt identity.

    Raises TaskReportError on every validation or filesystem failure. Does not
    check revision freshness; the owning group host compares against its last
    accepted revision and current assignment after each notification.
    """
    _validate_id(expected_task, "expected_task")
    _validate_id(expected_attempt, "expected_attempt")
    try:
        with _parent(path) as (directory, name):
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=directory)
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode):
                    raise TaskReportError("report must be a regular file")
                if info.st_size > MAX_REPORT_BYTES:
                    raise TaskReportError("report exceeds size limit")
                with os.fdopen(fd, "rb", closefd=False) as stream:
                    raw = stream.read(MAX_REPORT_BYTES + 1)
            finally:
                os.close(fd)
        if len(raw) > MAX_REPORT_BYTES:
            raise TaskReportError("report exceeds size limit")
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_object, parse_constant=_constant)
        report = TaskReport.from_dict(data)
        if report.task_id != expected_task or report.attempt_id != expected_attempt:
            raise TaskReportError("report task/attempt identity mismatch")
        return report
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        if isinstance(exc, TaskReportError):
            raise
        raise TaskReportError(f"cannot read task report: {exc}") from exc


def atomic_write_report(path: str | os.PathLike[str], report: TaskReport) -> None:
    """Write a validated snapshot by private same-directory atomic replacement.

    Parents must exist and cannot be symlinks. Existing non-regular destinations
    are rejected. New files have mode 0600 regardless of the old file's mode.
    Data and directory are fsynced; an fsync failure after replace may mean the
    new snapshot is already visible. This is not revision CAS or a writer lock.
    """
    if type(report) is not TaskReport:
        raise TaskReportError("report must be a TaskReport")
    # Revalidate rather than serialize arbitrary runtime objects/subclasses.
    validated = TaskReport.from_dict(report.to_dict())
    try:
        raw = (json.dumps(validated.to_dict(), ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
        if len(raw) > MAX_REPORT_BYTES:
            raise TaskReportError("report exceeds size limit")
        with _parent(path) as (directory, name):
            try:
                info = os.stat(name, dir_fd=directory, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                if not stat.S_ISREG(info.st_mode):
                    raise TaskReportError("report destination must be a regular file")
            temporary = f".task-report-{uuid.uuid4().hex}.tmp"
            fd = os.open(
                temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600, dir_fd=directory,
            )
            try:
                with os.fdopen(fd, "wb") as stream:
                    os.fchmod(stream.fileno(), 0o600)
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
                os.fsync(directory)
            finally:
                try:
                    os.unlink(temporary, dir_fd=directory)
                except FileNotFoundError:
                    pass
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        if isinstance(exc, TaskReportError):
            raise
        raise TaskReportError(f"cannot write task report: {exc}") from exc


def sync_report(path: str | os.PathLike[str]) -> None:
    """Re-establish file/directory durability after an identical ACK-loss retry.

    Never rewrite the content or advance revision/mtime. A previous rename may
    be visible even though directory fsync failed; visibility alone is not ACK.
    """
    try:
        with _parent(path) as (directory, name):
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=directory)
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    raise TaskReportError('report must be a regular file')
                os.fsync(fd)
            finally:
                os.close(fd)
            os.fsync(directory)
    except OSError as exc:
        raise TaskReportError(f'cannot sync task report: {exc}') from exc
