"""Bounded immutable extension session entries; no canonical chat-message mutation."""
from __future__ import annotations

import json
import re
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


def identifier(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,31}", value):
        raise ValueError("Invalid extension entry namespace/kind")
    return value


MAX_STATE_BYTES = 512 * 1024


@dataclass(frozen=True, slots=True)
class ExtensionEntry:
    id: str
    kind: str
    data_json: str

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "data": json.loads(self.data_json)}


def validate_entries(value: Any) -> dict[str, tuple[ExtensionEntry, ...]]:
    if not isinstance(value, dict) or len(value) > 16:
        raise ValueError("Invalid saved extension entries")
    result = {}
    count = 0
    for name, rows in value.items():
        identifier(name)
        if not isinstance(rows, (list, tuple)):
            raise ValueError("Invalid extension entry collection")
        entries = []
        for row in rows:
            if (not isinstance(row, dict) or set(row) != {"id", "kind", "data"}
                    or not isinstance(row["id"], str) or not 0 < len(row["id"]) <= 64):
                raise ValueError("Invalid extension entry identity")
            kind = identifier(row["kind"])
            data = json.dumps(row["data"], ensure_ascii=True, allow_nan=False)
            if len(data.encode()) > 16 * 1024:
                raise ValueError("Extension entry is too large")
            entries.append(ExtensionEntry(row["id"], kind, data))
            count += 1
        if len({entry.id for entry in entries}) != len(entries):
            raise ValueError("Duplicate extension entry ID")
        result[name] = tuple(entries)
    if count > 128 or len(json.dumps({name: [entry.to_dict() for entry in rows]
                                     for name, rows in result.items()}, ensure_ascii=True).encode()) > MAX_STATE_BYTES:
        raise ValueError("Extension session entry capacity exceeded")
    return result


class ExtensionEntries:
    def __init__(self, saved: Any, persist: Callable[[dict[str, Any]], None]) -> None:
        self._entries = validate_entries(saved)
        self._persist = persist
        self._lock = threading.Lock()
        self._closed = threading.Event()

    def snapshot(self) -> dict[str, Any]:
        return {name: [entry.to_dict() for entry in rows] for name, rows in self._entries.items()}

    def for_extension(self, name: str) -> list[dict[str, Any]]:
        return [entry.to_dict() for entry in self._entries.get(name, ())]

    def append(self, name: str, rows: Any) -> None:
        identifier(name)
        if not isinstance(rows, list) or len(rows) > 16:
            raise ValueError("Invalid extension entry update")
        with self._lock:
            if self._closed.is_set():
                raise RuntimeError("Extension entry owner closed")
            next_state = self.snapshot()
            appended = next_state.setdefault(name, [])
            for row in rows:
                if not isinstance(row, dict) or set(row) != {"kind", "data"}:
                    raise ValueError("Invalid extension entry contribution")
                appended.append({"id": uuid.uuid4().hex, **row})
            next_entries = validate_entries(next_state)
            self._persist(next_state)  # No publication before the durable write.
            self._entries = next_entries

    def bind_persistence(self, persist: Callable[[dict[str, Any]], None]) -> None:
        """Host-only adoption of a fully prepared owner; buffered setup is not a disk write."""
        with self._lock:
            if self._closed.is_set():
                raise RuntimeError('Extension entry owner closed')
            self._persist = persist

    def close(self) -> None:
        self._closed.set()
