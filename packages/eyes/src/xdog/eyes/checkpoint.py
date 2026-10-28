"""Data-only codecs for the existing executor's durable checkpoints."""
from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from xdog.eyes.deliveries import Delivery
from xdog.eyes.local_control import JsonValue, object_value
from xdog.eyes.models import (
    AvailabilityEvidence,
    Event,
    Evidence,
    FileEvidence,
    FileState,
    ObserverDef,
    RecordEvidence,
    SchedulerEvidence,
)
from xdog.eyes.sources.file import FileSnapshot


def data(value: object) -> dict[str, JsonValue]:
    return object_value(json.loads(json.dumps(value, allow_nan=False)))


def delivery_data(value: Delivery) -> dict[str, JsonValue]:
    return data(asdict(value))


def restore_delivery(value: Any, definition: ObserverDef, registration_id: str) -> Delivery:
    """Only journal-owned data; never imports classes named by a checkpoint."""
    if value['registration_id'] != registration_id:
        raise ValueError('Checkpoint registration mismatch')
    route = next(route for route in definition.routes if route.id == value['route_id'])
    detector = next(item for item in definition.detectors if item.id == route.detector)
    events = []
    for event in value['events']:
        if (event['source_id'] != detector.source or event['detector_id'] != detector.id
                or event['type'] not in route.event_types):
            raise ValueError('Checkpoint event does not match its definition')
        evidence: list[Evidence] = []
        for item in event['evidence']:
            if event['type'] == 'scheduler.due':
                evidence.append(SchedulerEvidence(**item))
            elif event['type'].startswith(('record.', 'condition.')):
                evidence.append(RecordEvidence(**item))
            elif event['type'].startswith('availability.'):
                evidence.append(AvailabilityEvidence(**item))
            else:
                evidence.append(FileEvidence(**{**item,
                    'before': FileState(**item['before']) if item['before'] is not None else None,
                    'after': FileState(**item['after']) if item['after'] is not None else None}))
        events.append(Event(**{**event, 'evidence': tuple(evidence)}))
    return Delivery(value['id'], registration_id, route.id, tuple(events))


def snapshot_data(value: FileSnapshot | None) -> Any:
    return None if value is None else [[path, asdict(state)] for path, state in value.entries]


def restore_snapshot(value: Any) -> FileSnapshot | None:
    return None if value is None else FileSnapshot(tuple((path, FileState(**state)) for path, state in value))
