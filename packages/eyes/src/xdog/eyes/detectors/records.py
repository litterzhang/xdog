"""Deterministic remote detectors. No model, network, business ranking or storage IO."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict
from typing import Any, cast

from xdog.eyes.models import (
    AvailabilityDetectorDef,
    AvailabilityEvidence,
    ConditionDetectorDef,
    Event,
    EventType,
    Predicate,
    ReceivedDetectorDef,
    RecordDetectorDef,
    RecordDetectorDefinition,
    RecordEvidence,
)
from xdog.eyes.sources.records import Record, RecordSnapshot, canonical, pointer


def matches(value: str, predicates: tuple[Predicate, ...]) -> bool:
    data = json.loads(value)
    for predicate in predicates:
        try:
            actual = pointer(data, predicate.pointer)
        except (ValueError, KeyError, TypeError, IndexError):
            return False  # Missing does not satisfy even "not equal".
        expected = predicate.value
        numeric = type(actual) in {int, float} and type(expected) in {int, float}
        if predicate.op in {"eq", "ne"}:
            equal = (numeric or type(actual) is type(expected)) and actual == expected
            result = equal if predicate.op == "eq" else not equal
        elif not numeric:
            return False
        else:
            result = {
                "gt": lambda: actual > expected,
                "ge": lambda: actual >= expected,
                "lt": lambda: actual < expected,
                "le": lambda: actual <= expected,
            }[predicate.op]()
        if not result:
            return False
    return True


class RecordDetector:
    def __init__(self, definition: RecordDetectorDefinition, *, max_records: int) -> None:
        self.definition = definition
        self.max_records = max_records
        self._records: dict[str, Record] | None = None
        self._pending: tuple[Event, ...] = ()
        self._failures = 0
        self._failed_since: float | None = None
        self._unavailable = False
        self._has_healthy = False

    def _event(
        self, kind: EventType, snapshot: RecordSnapshot, evidence: tuple[RecordEvidence | AvailabilityEvidence, ...]
    ) -> None:
        self._pending += (
            Event(uuid.uuid4().hex, self.definition.source, self.definition.id, kind, snapshot.observed_at, evidence),
        )

    def observe(self, snapshot: RecordSnapshot, *, now: float) -> None:
        definition = self.definition
        if isinstance(definition, AvailabilityDetectorDef):
            self._availability(snapshot)
            return
        if not snapshot.valid or snapshot.unchanged:
            return
        if isinstance(definition, ReceivedDetectorDef):
            for item in snapshot.records:
                self._event("event.received", snapshot, (
                    RecordEvidence(item.key, item.url, None, item.value, snapshot.observed_at, item.input_id),))
            return
        # Validate all projections before changing the baseline or producing any event.
        def comparison(item: Record) -> str:
            if isinstance(definition, RecordDetectorDef) and definition.compare_fields:
                value = json.loads(item.value)
                return canonical({name: pointer(value, name) for name in definition.compare_fields})
            return item.value

        current = {item.key: item for item in snapshot.records}
        for item in current.values():
            comparison(item)
        if self._records is None and not snapshot.stream:
            self._records = current  # Initial content is a baseline, not a notification storm.
            return
        previous = self._records or {}
        # Discard only history older than a source's explicit safe overlap floor.
        # This is dedup retention, never a claim that a remote resource was deleted.
        if snapshot.window_start is not None:
            previous = {
                key: item
                for key, item in previous.items()
                if key in current or item.updated_at is None or item.updated_at >= snapshot.window_start
            }
        combined = current if snapshot.complete or snapshot.retain_current else {**previous, **current}
        if len(combined) > self.max_records:
            raise ValueError("Detector deduplication capacity exceeded; explicitly rebaseline")
        for key, record in current.items():
            old = previous.get(key)
            if old is not None:
                unchanged = comparison(old) == comparison(record)
                if unchanged:
                    continue
            evidence = (
                RecordEvidence(key, record.url, old.value if old else None, record.value,
                               snapshot.observed_at, record.input_id),
            )
            if isinstance(definition, RecordDetectorDef):
                self._event("record.created" if old is None else "record.updated", snapshot, evidence)
            elif isinstance(definition, ConditionDetectorDef):
                before = matches(old.value, definition.all) if old is not None else False
                after = matches(record.value, definition.all)
                if before != after:
                    self._event("condition.met" if after else "condition.cleared", snapshot, evidence)
        if (snapshot.complete and not snapshot.stream
                and isinstance(definition, RecordDetectorDef) and definition.removals):
            for key in previous.keys() - current.keys():
                old = previous[key]
                self._event(
                    "record.removed", snapshot, (RecordEvidence(key, old.url, old.value, None, snapshot.observed_at),)
                )
        self._records = combined

    def _availability(self, snapshot: RecordSnapshot) -> None:
        definition = cast(AvailabilityDetectorDef, self.definition)
        if snapshot.healthy:
            if self._unavailable:
                self._event(
                    "availability.recovered",
                    snapshot,
                    (AvailabilityEvidence(True, self._failures, snapshot.status_code, "", snapshot.observed_at),),
                )
            self._failures, self._failed_since, self._unavailable, self._has_healthy = 0, None, False, True
            return
        self._failures += 1
        if self._failed_since is None:
            self._failed_since = snapshot.observed_at
        if (
            not self._unavailable
            and (self._has_healthy or definition.initial_abnormal)
            and self._failures >= definition.failures
            and snapshot.observed_at - self._failed_since >= definition.for_seconds
        ):
            self._unavailable = True
            self._event(
                "availability.unavailable",
                snapshot,
                (
                    AvailabilityEvidence(
                        False, self._failures, snapshot.status_code, snapshot.error, snapshot.observed_at
                    ),
                ),
            )

    def flush(self, *, now: float) -> tuple[Event, ...]:
        pending, self._pending = self._pending, ()
        return pending

    def checkpoint(self) -> dict[str, Any]:
        return {
            "records": None if self._records is None else [asdict(item) for item in self._records.values()],
            "failures": self._failures,
            "failed_since": self._failed_since,
            "unavailable": self._unavailable,
            "has_healthy": self._has_healthy,
        }

    def restore(self, value: dict[str, Any], *, now: float) -> None:
        rows = value["records"]
        self._records = None if rows is None else {row["key"]: Record(**row) for row in rows}
        if self._records is not None and len(self._records) > self.max_records:
            raise ValueError("Detector checkpoint exceeds capacity")
        self._failures, self._failed_since = value["failures"], value["failed_since"]
        self._unavailable, self._has_healthy = value["unavailable"], value["has_healthy"]
