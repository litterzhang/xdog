"""Immutable queue views and command parsing shared by terminal clients."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class QueueEntry:
    id: str
    content: str
    delivery: str
    state: str = "local"
    owner: str = ""


@dataclass(frozen=True, slots=True)
class QueueCommand:
    action: str
    id: str = ""
    content: str = ""
    direction: int = 0


QUEUE_HELP = "/queue [list | edit ID TEXT | remove ID | move ID up/down] · /steer TEXT · /follow-up TEXT"


def parse_queue_command(args: str) -> QueueCommand:
    parts = args.split(None, 2)
    if not parts or parts == ["list"]:
        return QueueCommand("list")
    if parts[0] == "remove" and len(parts) == 2:
        return QueueCommand("remove", parts[1])
    if parts[0] == "edit" and len(parts) == 3 and parts[2].strip():
        return QueueCommand("edit", parts[1], parts[2])
    if parts[0] == "move" and len(parts) == 3 and parts[2] in {"up", "down"}:
        return QueueCommand("move", parts[1], direction=-1 if parts[2] == "up" else 1)
    raise ValueError(QUEUE_HELP)


class LocalQueueIds:
    """Stable IDs alongside compatibility FIFO storage owned by each client."""

    def __init__(self) -> None:
        self.ids: deque[str] = deque()
        self.sequence = 0

    def sync(self, length: int) -> tuple[str, ...]:
        while len(self.ids) < length:
            self.sequence += 1
            self.ids.append(f"q{self.sequence}")
        while len(self.ids) > length:
            self.ids.pop()
        return tuple(self.ids)

    def pop(self) -> None:
        if self.ids:
            self.ids.popleft()

    def clear(self) -> None:
        self.ids.clear()
