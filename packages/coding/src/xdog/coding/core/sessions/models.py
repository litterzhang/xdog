"""Immutable request history; queued records are not executable replay state."""

from __future__ import annotations

from concurrent.futures import Future
from dataclasses import dataclass, field

from xdog.agent.session_input import SessionInput
from xdog.ai.types import AssistantMessage

TERMINAL = frozenset({"succeeded", "failed", "cancelled", "discarded", "interrupted"})
STATES = TERMINAL | {"queued", "paused", "running"}


@dataclass(frozen=True)
class InputRecord:
    input: SessionInput
    branch_id: str
    generation: str
    status: str
    error: str = ""
    batch_id: str = ""
    # None identifies legacy records; zero explicitly means not started.
    start_order: int | None = 0
    started_at: float | None = None
    settled_at: float | None = None


@dataclass(frozen=True)
class TurnResult:
    status: str
    response: AssistantMessage | None
    error: str = ""
    cause: BaseException | None = field(default=None, repr=False, compare=False)
    batch_id: str = ""
    execution_continues: bool = False


@dataclass(frozen=True)
class SessionEvent:
    session_id: str
    generation: str
    request_id: str
    kind: str
    data: object
    batch_id: str = ""
    request_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class Submission:
    id: str
    accepted: Future[None]
    result: Future[TurnResult]
