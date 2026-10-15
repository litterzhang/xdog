"""Convert trustworthy snapshots into debounced net-change events."""
from __future__ import annotations

import uuid

from xdog.eyes.models import EVENT_TYPES, DetectorDef, Event, FileEvidence, FileState
from xdog.eyes.sources.file import FileSnapshot


class FileChangeDetector:
    def __init__(self, definition: DetectorDef, *, source_root: str = "") -> None:
        self.definition = definition
        self.source_root = source_root
        self._baseline: FileSnapshot | None = None
        self._latest: FileSnapshot | None = None
        self._first: float | None = None
        self._last = 0.0
        self._observed: dict[str, float] = {}

    def observe(self, snapshot: FileSnapshot, *, now: float) -> None:
        if self._baseline is None:
            self._baseline = self._latest = snapshot
            return
        if snapshot == self._latest:
            return
        previous = dict(self._latest.entries) if self._latest is not None else {}
        current = dict(snapshot.entries)
        for path in previous.keys() | current.keys():
            if previous.get(path) != current.get(path):
                self._observed[path] = now
        self._latest = snapshot
        if self._first is None:
            self._first = now
        self._last = now

    def flush(self, *, now: float) -> tuple[Event, ...]:
        if (self._first is None or self._baseline is None or self._latest is None
                or (now - self._last < self.definition.debounce_ms / 1000
                    and now - self._first < self.definition.max_batch_delay_ms / 1000)):
            return ()
        before, after = dict(self._baseline.entries), dict(self._latest.entries)
        groups: dict[str, list[FileEvidence]] = {kind: [] for kind in EVENT_TYPES}
        for path in sorted(before.keys() | after.keys()):
            old: FileState | None = before.get(path)
            new: FileState | None = after.get(path)
            if old == new:
                continue
            kind = "file.created" if old is None else "file.deleted" if new is None else "file.modified"
            groups[kind].append(FileEvidence(path, old, new, self._observed.get(path, now), self.source_root))
        result = tuple(Event(uuid.uuid4().hex, self.definition.source, self.definition.id, kind, now,
                             tuple(groups[kind])) for kind in EVENT_TYPES if groups[kind])
        self._baseline = self._latest
        self._first = None
        self._observed.clear()
        return result
