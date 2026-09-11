"""Immutable UI lifecycle; queue/worker synchronization remains in the client."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum


class RunPhase(StrEnum):
    READY = "ready"
    WAITING = "waiting"
    REASONING = "reasoning"
    RESPONDING = "responding"
    RUNNING = "running"
    PERMISSION = "awaiting tool permission"
    CANCELLING = "cancelling"


@dataclass(frozen=True, slots=True)
class RunStatus:
    phase: RunPhase = RunPhase.READY
    started: float | None = None
    resume_phase: RunPhase = RunPhase.WAITING

    @property
    def busy(self) -> bool:
        return self.phase != RunPhase.READY

    @property
    def awaiting_permission(self) -> bool:
        return self.phase == RunPhase.PERMISSION

    def start(self, now: float) -> RunStatus:
        return RunStatus(RunPhase.WAITING, now)

    def finish(self) -> RunStatus:
        return RunStatus()

    def advance(self, phase: RunPhase) -> RunStatus:
        if phase == RunPhase.READY:
            return self.finish()
        if not self.busy or self.phase == RunPhase.CANCELLING:
            return self
        if phase == RunPhase.CANCELLING:
            return replace(self, phase=phase)
        if self.awaiting_permission:
            return self if phase == RunPhase.PERMISSION else replace(self, resume_phase=phase)
        if phase == RunPhase.PERMISSION:
            return replace(self, phase=phase, resume_phase=self.phase)
        return replace(self, phase=phase)

    def resume(self) -> RunStatus:
        return replace(self, phase=self.resume_phase) if self.awaiting_permission else self
