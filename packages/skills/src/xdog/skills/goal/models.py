"""Goal records, not a worker scheduler or completion judge."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path

GOAL_STATES = frozenset({'active', 'waiting', 'paused', 'completed', 'cleared'})
TASK_STATES = frozenset({'pending', 'in_progress', 'blocked', 'completed', 'cancelled'})


def text(value: object, *, empty: bool = False, maximum: int = 65536) -> str:
    if (not isinstance(value, str) or '\x00' in value or len(value.encode()) > maximum
            or not empty and not value.strip()):
        raise ValueError('Expected bounded Goal text')
    return value


def identity(value: object) -> str:
    if not isinstance(value, str) or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}', value) is None:
        raise ValueError('Invalid Goal/task identity')
    return value


def number(value: object) -> int:
    if type(value) is not int or not 1 <= value < 2**53:
        raise ValueError('Expected a positive integer revision')
    return value


def strings(value: tuple[str, ...], *, nonempty: bool = False) -> None:
    if type(value) is not tuple or nonempty and not value:
        raise ValueError('Expected an immutable text collection')
    for item in value:
        text(item)


@dataclass(frozen=True)
class Owner:
    key: str
    workspace: Path

    def __post_init__(self) -> None:
        text(self.key, maximum=1024)
        if not isinstance(self.workspace, Path) or not self.workspace.is_absolute() or '..' in self.workspace.parts:
            raise ValueError('Owner requires an absolute workspace without traversal')
        text(str(self.workspace), maximum=4096)


@dataclass(frozen=True)
class Goal:
    id: str
    title: str
    outcome: str
    criteria: tuple[str, ...] = ()
    status: str = 'active'
    version: int = 1
    reason: str = ''
    summary: str = ''
    wait_for: str = ''
    created_at: float = 0
    updated_at: float = 0

    question: str = ''
    impact: str = ''
    resume_when: str = ''

    def __post_init__(self) -> None:
        identity(self.id)
        text(self.title, maximum=1024)
        text(self.outcome)
        strings(self.criteria)
        if self.status not in GOAL_STATES:
            raise ValueError('Invalid Goal status')
        number(self.version)
        # 'host' is decode-only historical v2 data. Service/tools and store
        # admission reject new host waits; decoding never activates old work.
        if self.wait_for not in {'', 'user', 'tasks', 'host'}:
            raise ValueError('Invalid Goal wait condition')
        if self.status != 'waiting' and self.wait_for:
            raise ValueError('Only a waiting Goal has a wait condition')
        text(self.reason, empty=True)
        text(self.summary, empty=True)
        for value in (self.question, self.impact, self.resume_when):
            text(value, empty=True)
        if any((self.question, self.impact, self.resume_when)):
            if self.status != 'waiting' or self.wait_for != 'user':
                raise ValueError('Only wait(user) may hold a question')
            for value in (self.question, self.impact, self.resume_when):
                text(value)
        if any(type(v) not in (int, float) or not math.isfinite(v) or v < 0
               for v in (self.created_at, self.updated_at)):
            raise ValueError('Invalid Goal timestamp')


@dataclass(frozen=True)
class Task:
    id: str
    title: str
    description: str = ''
    status: str = 'pending'
    worker: str = 'main'
    depends_on: tuple[str, ...] = ()
    summary: str = ''
    evidence: tuple[str, ...] = ()
    version: int = 1

    def __post_init__(self) -> None:
        identity(self.id)
        text(self.title, maximum=1024)
        text(self.description, empty=True)
        text(self.worker, maximum=1024)
        text(self.summary, empty=True)
        strings(self.depends_on)
        strings(self.evidence)
        if self.id in self.depends_on or len(set(self.depends_on)) != len(self.depends_on):
            raise ValueError('Invalid task dependencies')
        for item in self.depends_on:
            identity(item)
        if self.status not in TASK_STATES:
            raise ValueError('Invalid task status')
        number(self.version)
