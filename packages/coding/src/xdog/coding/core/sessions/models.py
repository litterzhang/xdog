"""Immutable request history; queued records are not executable replay state."""

from __future__ import annotations

from dataclasses import dataclass

from xdog.agent.session_input import SessionInput

TERMINAL = frozenset({"succeeded", "failed", "cancelled", "discarded", "interrupted"})
STATES = TERMINAL | {"queued", "paused", "running"}


@dataclass(frozen=True)
class InputRecord:
    input: SessionInput
    branch_id: str
    generation: str
    status: str
    error: str = ""
