"""Finite pushed source facts. Durable intake belongs to the existing daemon journal."""

from __future__ import annotations

from dataclasses import dataclass

from xdog.eyes.sources.records import Record, RecordSnapshot


@dataclass(frozen=True)
class EventInput:
    sequence: int
    event_id: str
    key: str
    value: str
    received_at: float

    def snapshot(self) -> RecordSnapshot:
        return RecordSnapshot(
            (Record(self.key, self.value, "", input_id=self.event_id),), self.received_at, complete=False, stream=True
        )
