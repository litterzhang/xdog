"""Immutable request history; queued records are not executable replay state."""

from __future__ import annotations

from concurrent.futures import Future
from dataclasses import dataclass

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


@dataclass(frozen=True)
class TurnResult:
    status: str
    response: AssistantMessage | None
    error: str = ""


@dataclass(frozen=True)
class SessionEvent:
    session_id: str
    generation: str
    request_id: str
    kind: str
    data: object


@dataclass(frozen=True)
class Submission:
    id: str
    accepted: Future[None]
    result: Future[TurnResult]
