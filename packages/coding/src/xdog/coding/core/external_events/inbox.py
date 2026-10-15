"""Bounded thread-safe admission; never starts a model or edits its history."""
from __future__ import annotations

import hashlib
import json
import threading
import uuid
from dataclasses import dataclass, replace
from typing import cast

from xdog.coding.core.external_events.protocol import (
    JsonValue,
    ProtocolError,
    json_value,
    object_value,
    text,
    validate_request,
)


@dataclass(frozen=True)
class ExternalEventJob:
    id: str
    registration_id: str
    route_id: str
    instruction: str
    events: tuple[dict[str, JsonValue], ...]
    delivery_ids: tuple[str, ...]


def _evidence_count(events: tuple[dict[str, JsonValue], ...]) -> int:
    identities = set()
    for event in events:
        for row in cast(list[JsonValue], event["evidence"]):
            evidence = object_value(row)
            locator = evidence.get("id", evidence.get("path"))
            if not isinstance(locator, str):
                locator = hashlib.sha256(json.dumps(evidence, sort_keys=True).encode()).hexdigest()
            identities.add((text(event["source_id"]), locator))
    return len(identities)


class ExternalEventInbox:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._routes: dict[tuple[str, str], str] = {}
        self._pending: dict[tuple[str, str], ExternalEventJob] = {}
        self._admitted: dict[str, str] = {}
        self._processing: dict[str, dict[str, JsonValue]] = {}
        self._budget = 0
        self._preferred: str | None = None
        self._paused_reason = ""
        self._overflow: set[str] = set()

    def register(self, registration_id: str, route_id: str, instruction: str) -> str:
        try:
            text(registration_id)
            text(route_id)
            text(instruction, maximum=65536)
        except ProtocolError:
            return "rejected"
        with self._lock:
            key = registration_id, route_id
            if key in self._routes:
                return "duplicate" if self._routes[key] == instruction else "rejected"
            if len(self._routes) >= 128:
                return "busy"
            self._routes[key] = instruction
            return "accepted"

    def accept(self, request: dict[str, JsonValue]) -> str:
        try:
            message = validate_request(request)
            if message["action"] != "deliver":
                return "rejected"
            body = object_value(message["body"])
            registration, route, identifier = (text(body[key]) for key in
                                                ("registration_id", "route_id", "delivery_id"))
            events = tuple(object_value(event) for event in cast(list[JsonValue], body["events"]))
        except ProtocolError:
            return "rejected"
        with self._lock:
            key = registration, route
            if identifier in self._admitted:
                return "duplicate"
            if key not in self._routes:
                return "rejected"
            if registration in self._overflow:
                return "paused"
            if len(self._admitted) >= 4096:
                self._paused_reason = "overflow: delivery ID capacity; revoke and recreate the context capability"
                return "paused"
            previous = self._pending.get(key)
            if previous:
                merged = {text(event["id"]): event for event in previous.events}
                for event in events:
                    event_id = text(event["id"])
                    if event_id in merged and event != merged[event_id]:
                        return "rejected"
                    merged[event_id] = event
                candidate = replace(previous, events=tuple(merged.values()),
                                    delivery_ids=previous.delivery_ids + (identifier,))
            else:
                candidate = ExternalEventJob(uuid.uuid4().hex, registration, route, self._routes[key],
                                             events, (identifier,))
            if (_evidence_count(candidate.events) > 256
                    or len(json.dumps(candidate.events, ensure_ascii=True).encode()) > 1024 * 1024
                    or (previous is None and len(self._pending) >= 128)):
                self._overflow.add(registration)
                self._paused_reason = "overflow: pending evidence capacity; stop and restart the observer"
                return "paused"
            self._pending[key] = candidate
            self._admitted[identifier] = candidate.id
            self._processing[candidate.id] = {
                "id": candidate.id, "registration_id": registration, "route_id": route,
                "status": "waiting", "delivery_ids": list(candidate.delivery_ids), "error": "",
            }
            return "accepted"

    def take(self, *, busy: bool, human_pending: bool) -> ExternalEventJob | None:
        with self._lock:
            if busy or human_pending or self._budget <= 0:
                return None
            for key, job in self._pending.items():
                if job.registration_id in self._overflow:
                    continue
                if self._preferred is not None and job.registration_id != self._preferred:
                    continue
                del self._pending[key]
                self._budget -= 1
                self._preferred = None
                self._processing[job.id]["status"] = "running"
                return job
            return None

    def complete(self, job_id: str, *, status: str, error: str | None = None) -> None:
        if status not in {"succeeded", "failed", "cancelled", "discarded"}:
            raise ValueError("Invalid external event processing result")
        with self._lock:
            row = self._processing.get(job_id)
            if row is not None and row["status"] == "running":
                row.update(status=status, error=(error or "")[:4096])

    def human_input(self) -> None:
        with self._lock:
            self._budget = 1
            self._preferred = None

    def resume(self, registration_id: str, *, approved: bool) -> str:
        with self._lock:
            if (not approved or not any(key[0] == registration_id for key in self._routes)
                    or registration_id in self._overflow or len(self._admitted) >= 4096):
                return "rejected"
            self._budget = 1
            self._preferred = registration_id
            return "accepted"

    def unregister(self, registration_id: str) -> None:
        with self._lock:
            for key in list(self._routes):
                if key[0] == registration_id:
                    del self._routes[key]
            for key in list(self._pending):
                if key[0] == registration_id:
                    job = self._pending.pop(key)
                    self._processing[job.id]["status"] = "discarded"
            self._overflow.discard(registration_id)
            if not self._overflow and len(self._admitted) < 4096:
                self._paused_reason = ""

    def invalidate(self) -> None:
        with self._lock:
            self._routes.clear()
            self._pending.clear()
            self._admitted.clear()
            self._processing.clear()
            self._overflow.clear()
            self._budget = 0
            self._preferred = None
            self._paused_reason = ""

    def snapshot(self, registration_id: str | None = None) -> dict[str, JsonValue]:
        """Bound presentation, not the authoritative admission/dedup state."""
        with self._lock:
            reason = self._paused_reason or ("wake budget exhausted" if self._budget == 0 and self._pending else "")
            registrations = [{"registration_id": reg, "route_id": route} for reg, route in self._routes
                             if registration_id is None or reg == registration_id]
            pending = [self._processing[job.id] for job in self._pending.values()
                       if registration_id is None or job.registration_id == registration_id]
            processing = [row for row in reversed(list(self._processing.values()))
                          if registration_id is None or row["registration_id"] == registration_id]
            result: dict[str, JsonValue] = {
                "registrations": [], "pending": [], "processing": [], "wake_budget": self._budget,
                "paused_reason": reason, "pending_count": len(pending), "processing_count": len(processing),
                "registration_count": len(registrations), "truncated": False,
            }
            size = len(json.dumps(result, ensure_ascii=True).encode())
            for key, rows in (("registrations", registrations), ("pending", pending), ("processing", processing)):
                output = cast(list[JsonValue], result[key])
                for original in rows:
                    row = dict(original)
                    identifiers = row.get("delivery_ids")
                    if isinstance(identifiers, list):
                        row["delivery_ids"] = identifiers[-8:]
                        row["delivery_count"] = len(identifiers)
                    cost = len(json.dumps(row, ensure_ascii=True).encode()) + 2
                    if size + cost > 900 * 1024:
                        result["truncated"] = True
                        continue
                    size += cost
                    output.append(row)
            return cast(dict[str, JsonValue], json_value(result))
