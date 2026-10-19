"""Small trusted async host ports; no runtime/session/controller or Agent factory."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from xdog.goal.models import Notice, Owner, WorkerRef, identity, state, text


@dataclass(frozen=True)
class OperationContext:
    user_authorized: bool = False

    def __post_init__(self) -> None:
        if type(self.user_authorized) is not bool:
            raise ValueError("User authority must be a host-produced boolean")


@dataclass(frozen=True)
class WorkerSpec:
    goal_id: str
    task_id: str
    attempt_id: str
    prompt: str
    access: str = "read-only"

    def __post_init__(self) -> None:
        for value in (self.goal_id, self.task_id, self.attempt_id):
            identity(value)
        text(self.prompt)
        state(self.access, {"read-only", "inherit"})


@dataclass(frozen=True)
class ReviewerSpec:
    goal_id: str
    review_id: str
    criteria_revision: int
    prompt: str
    # A role cannot request a writable reviewer through JSON/config inheritance.
    access: str = "read-only"

    def __post_init__(self) -> None:
        identity(self.goal_id)
        identity(self.review_id)
        text(self.prompt)
        if self.access != "read-only":
            raise ValueError("Reviewer must use the host read-only tool boundary")


@dataclass(frozen=True)
class ChildStatus:
    worker: WorkerRef
    state: str
    result_json: str = ""
    error: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.worker, WorkerRef):
            raise ValueError("Child status needs its actual execution reference")
        state(self.state, {"running", "settling", "succeeded", "failed", "cancelled", "unknown"})
        text(self.result_json, empty=True, maximum=64 * 1024)
        text(self.error, empty=True, maximum=64 * 1024)


@dataclass(frozen=True)
class EnqueueReceipt:
    owner_key: str
    notice_id: str
    status: str

    def __post_init__(self) -> None:
        text(self.owner_key, maximum=1024)
        text(self.notice_id, maximum=256)
        state(self.status, {"accepted", "duplicate", "not-sent", "unknown"})


class GoalHost(Protocol):
    @property
    def owner(self) -> Owner: ...

    async def start_worker(self, spec: WorkerSpec) -> WorkerRef: ...
    async def start_reviewer(self, spec: ReviewerSpec) -> WorkerRef: ...
    async def child_status(self, worker: WorkerRef) -> ChildStatus: ...
    async def cancel_child(self, worker: WorkerRef) -> None: ...
    async def enqueue(self, notice: Notice) -> EnqueueReceipt:
        """Durable original-ID, content-checked idempotent intake; never prompt."""
        ...

    async def receipt(self, notice: Notice) -> EnqueueReceipt | None:
        """Validate owner/id AND expected canonical notice content before ACK."""
        ...


def require_user(context: OperationContext | None) -> None:
    if not isinstance(context, OperationContext) or not context.user_authorized:
        raise ValueError("This Goal operation requires actual host-authorized user input")
