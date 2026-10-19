"""Immutable Goal truth, with no host, UI, source or protocol dependencies."""

from __future__ import annotations

import hashlib
import json
import re
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z", re.ASCII)


def text(value: object, *, empty: bool = False, maximum: int = 256 * 1024) -> str:
    if (
        not isinstance(value, str)
        or "\x00" in value
        or len(value.encode()) > maximum
        or not empty
        and not value.strip()
    ):
        raise ValueError("Expected bounded nonempty Goal text")
    return value


def identity(value: object) -> str:
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        raise ValueError("Expected a bounded safe Goal identity")
    return value


def number(value: object) -> int:
    if type(value) is not int or not 1 <= value < 2**53:
        raise ValueError("Goal version/revision must be a strict positive integer")
    return value


def strings(value: tuple[str, ...], *, nonempty: bool = False) -> None:
    if type(value) is not tuple or nonempty and not value:
        raise ValueError("Goal collections must be immutable tuples")
    for item in value:
        text(item, maximum=64 * 1024)


def state(value: str, allowed: set[str]) -> None:
    if not isinstance(value, str) or value not in allowed:
        raise ValueError("Invalid Goal lifecycle state")


@dataclass(frozen=True)
class Owner:
    key: str
    workspace: Path

    def __post_init__(self) -> None:
        text(self.key, maximum=1024)
        if not isinstance(self.workspace, Path) or not self.workspace.is_absolute() or ".." in self.workspace.parts:
            raise ValueError("Goal owner requires an absolute host workspace")
        text(str(self.workspace), maximum=4096)


@dataclass(frozen=True)
class WorkerRef:
    id: str
    execution: str

    def __post_init__(self) -> None:
        text(self.id, maximum=256)
        text(self.execution, maximum=256)


@dataclass(frozen=True)
class Report:
    task_id: str
    attempt_id: str
    revision: int
    state: str
    summary: str
    evidence: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        identity(self.task_id)
        identity(self.attempt_id)
        number(self.revision)
        state(self.state, {"running", "submitted", "blocked"})
        text(self.summary, empty=self.state == "running", maximum=64 * 1024)
        strings(self.evidence)
        strings(self.blockers)
        if len(json.dumps(asdict(self), ensure_ascii=True).encode()) > 64 * 1024:
            raise ValueError("Goal report exceeds 64 KiB")


def report_digest(report: Report) -> str:
    return hashlib.sha256(json.dumps(asdict(report), sort_keys=True, ensure_ascii=True).encode()).hexdigest()


@dataclass(frozen=True)
class Attempt:
    id: str
    state: str = "starting"
    assignment: str = ""
    access: str = "inherit"
    worker: WorkerRef | None = None
    reports: tuple[Report, ...] = ()

    def __post_init__(self) -> None:
        identity(self.id)
        state(self.state, {"starting", "running", "reported", "blocked", "interrupted", "cancelled"})
        state(self.access, {"inherit", "read-only"})
        text(self.assignment, empty=True)
        if self.worker is not None and not isinstance(self.worker, WorkerRef):
            raise ValueError("Goal worker reference must be typed")
        if type(self.reports) is not tuple or any(not isinstance(item, Report) for item in self.reports):
            raise ValueError("Goal reports require immutable typed snapshots")
        if any(report.attempt_id != self.id for report in self.reports):
            raise ValueError("Report belongs to another Goal attempt")
        if [item.revision for item in self.reports] != sorted({item.revision for item in self.reports}):
            raise ValueError("Report revisions must be unique and increasing")
        if self.state == "running" and self.worker is None:
            raise ValueError("A running attempt needs its actual host worker reference")


@dataclass(frozen=True)
class Acceptance:
    attempt_id: str
    report_revision: int
    report_digest: str
    summary: str

    def __post_init__(self) -> None:
        identity(self.attempt_id)
        number(self.report_revision)
        if not re.fullmatch(r"[0-9a-f]{64}", self.report_digest):
            raise ValueError("Acceptance requires the actual selected report digest")
        text(self.summary, maximum=64 * 1024)


@dataclass(frozen=True)
class Task:
    id: str
    description: str
    depends_on: tuple[str, ...] = ()
    state: str = "pending"
    attempts: tuple[Attempt, ...] = ()
    acceptance: Acceptance | None = None

    def __post_init__(self) -> None:
        identity(self.id)
        text(self.description)
        strings(self.depends_on)
        if len(set(self.depends_on)) != len(self.depends_on):
            raise ValueError("Task dependencies must be unique")
        state(
            self.state,
            {"pending", "starting", "running", "submitted", "accepted", "blocked", "interrupted", "cancelled"},
        )
        if type(self.attempts) is not tuple or any(not isinstance(item, Attempt) for item in self.attempts):
            raise ValueError("Task attempts require immutable typed snapshots")
        if len({item.id for item in self.attempts}) != len(self.attempts):
            raise ValueError("Task attempt identities must be unique")
        if any(item.state in {"starting", "running"} for item in self.attempts[:-1]):
            raise ValueError("Only the current Task attempt may be active")
        if any(report.task_id != self.id for attempt in self.attempts for report in attempt.reports):
            raise ValueError("Report belongs to another Goal task")
        if self.acceptance is not None:
            if not isinstance(self.acceptance, Acceptance) or not self.attempts:
                raise ValueError("Task acceptance needs its selected actual report")
            selected = self.attempts[-1]
            reports = [item for item in selected.reports if item.revision == self.acceptance.report_revision]
            if (
                selected.id != self.acceptance.attempt_id
                or len(reports) != 1
                or reports[0].state != "submitted"
                or report_digest(reports[0]) != self.acceptance.report_digest
                or self.state != "accepted"
            ):
                raise ValueError("Task acceptance does not match the selected report")
        elif self.state == "accepted":
            raise ValueError("Submitted report is not task acceptance")


@dataclass(frozen=True)
class Finding:
    condition: str
    status: str
    evidence: tuple[str, ...]
    reason: str

    def __post_init__(self) -> None:
        text(self.condition, maximum=64 * 1024)
        state(self.status, {"passed", "failed", "blocked"})
        strings(self.evidence, nonempty=True)
        text(self.reason, maximum=64 * 1024)


@dataclass(frozen=True)
class Review:
    id: str
    state: str = "starting"
    criteria_revision: int = 1
    fingerprint: str = ""
    paths: tuple[str, ...] = ()
    worker: WorkerRef | None = None
    findings: tuple[Finding, ...] = ()
    verdict: str = ""
    summary: str = ""

    def __post_init__(self) -> None:
        identity(self.id)
        number(self.criteria_revision)
        state(
            self.state, {"starting", "running", "passed", "continue", "blocked", "interrupted", "invalid", "cancelled"}
        )
        text(self.fingerprint, empty=True, maximum=256)
        strings(self.paths)
        if self.worker is not None and not isinstance(self.worker, WorkerRef):
            raise ValueError("Review needs a typed actual host worker reference")
        if self.state == "running" and self.worker is None:
            raise ValueError("A running review requires its actual execution")
        if type(self.findings) is not tuple or any(not isinstance(item, Finding) for item in self.findings):
            raise ValueError("Review findings must be immutable typed snapshots")
        state(self.verdict, {"", "passed", "continue", "blocked"})
        text(self.summary, empty=True)
        if self.state == "passed" and (
            self.verdict != "passed" or not self.findings or any(item.status != "passed" for item in self.findings)
        ):
            raise ValueError("Passed review requires evidence-backed findings")


@dataclass(frozen=True)
class Goal:
    id: str
    title: str
    outcome: str
    criteria: tuple[str, ...]
    version: int = 1
    criteria_revision: int = 1
    status: str = "active"
    tasks: tuple[Task, ...] = ()
    review: Review | None = None
    summary: str = ""
    wait_reason: str = ""
    notifications_enabled: bool = True

    def __post_init__(self) -> None:
        identity(self.id)
        text(self.title, maximum=4096)
        text(self.outcome)
        strings(self.criteria, nonempty=True)
        number(self.version)
        if number(self.criteria_revision) > self.version:
            raise ValueError("Criteria revision cannot exceed the actual Goal version")
        state(self.status, {"active", "waiting", "paused", "blocked", "reviewing", "completed", "abandoned"})
        text(self.summary, empty=True)
        text(self.wait_reason, empty=True)
        if type(self.notifications_enabled) is not bool:
            raise ValueError("Notification availability is a strict boolean")
        if type(self.tasks) is not tuple or any(not isinstance(item, Task) for item in self.tasks):
            raise ValueError("Goal tasks require immutable typed snapshots")
        indexed = {task.id: task for task in self.tasks}
        if len(indexed) != len(self.tasks):
            raise ValueError("Goal task IDs must be unique")
        # Validate a DAG without imposing Python's call-stack depth as a
        # hidden maximum task count. Preserve the caller's original task order.
        pending = {key: len(task.depends_on) for key, task in indexed.items()}
        followers: dict[str, list[str]] = {key: [] for key in indexed}
        for task in self.tasks:
            for dependency in task.depends_on:
                if dependency not in indexed:
                    raise ValueError("Task dependencies must be a valid acyclic graph")
                followers[dependency].append(task.id)
        ready = deque(key for key, count in pending.items() if count == 0)
        visited = 0
        while ready:
            key = ready.popleft()
            visited += 1
            for follower in followers[key]:
                pending[follower] -= 1
                if pending[follower] == 0:
                    ready.append(follower)
        if visited != len(indexed):
            raise ValueError("Task dependencies must be a valid acyclic graph")
        if self.review is not None and not isinstance(self.review, Review):
            raise ValueError("Goal review must be a typed snapshot")
        if self.status == "completed" and (
            self.review is None
            or self.review.state != "passed"
            or self.review.criteria_revision != self.criteria_revision
            or tuple(row.condition for row in self.review.findings) != self.criteria
            or not self.review.fingerprint
            or self.review.worker is None
        ):
            raise ValueError("Only a current complete independent review can finish a Goal")


@dataclass(frozen=True)
class Notice:
    id: str
    goal_id: str
    instruction: str

    def __post_init__(self) -> None:
        text(self.id, maximum=256)
        identity(self.goal_id)
        text(self.instruction, maximum=64 * 1024)
