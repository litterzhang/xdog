"""Transport-independent deterministic detector configuration."""
from __future__ import annotations

import math
from typing import Literal, cast

from xdog.eyes.config_values import boolean, bounded_int, json_pointer, number, projection
from xdog.eyes.errors import ObserverValidationError
from xdog.eyes.loader import _object, _text
from xdog.eyes.models import (
    AvailabilityDetectorDef,
    ConditionDetectorDef,
    Predicate,
    ReceivedDetectorDef,
    RecordDetectorDef,
    RecordDetectorDefinition,
)


def detector(value: object, path: str) -> RecordDetectorDefinition:
    kind = value.get("type") if isinstance(value, dict) else None
    fields = {
        "record.change": {"removals", "compare_fields"},
        "event.received": set(),
        "value.condition": {"all"},
        "availability": {"failures", "for_seconds", "initial_abnormal"},
    }
    data = _object(value, path, {"id", "type", "source"} | fields.get(str(kind), set()))
    identifier, source_id = _text(data.get("id"), path + ".id"), _text(data.get("source"), path + ".source")
    if kind == "record.change":
        return RecordDetectorDef(
            id=identifier, source=source_id, removals=boolean(data.get("removals", False), path + ".removals"),
            compare_fields=projection(data.get("compare_fields", []), path + ".compare_fields")
        )
    if kind == "event.received":
        return ReceivedDetectorDef(id=identifier, source=source_id)
    if kind == "availability":
        return AvailabilityDetectorDef(
            id=identifier,
            source=source_id,
            failures=bounded_int(data.get("failures", 3), path + ".failures", 10000),
            for_seconds=number(data.get("for_seconds", 0), path + ".for_seconds", minimum=0, maximum=2592000),
            initial_abnormal=boolean(data.get("initial_abnormal", True), path + ".initial_abnormal"),
        )
    if kind == "value.condition":
        conditions = data.get("all")
        if not isinstance(conditions, list) or not 1 <= len(conditions) <= 16:
            raise ObserverValidationError("Expected 1..16 scalar predicates", path=path + ".all")
        predicates = []
        for index, condition in enumerate(conditions):
            loc = f"{path}.all[{index}]"
            row = _object(condition, loc, {"pointer", "op", "value"})
            op, expected = row.get("op"), row.get("value")
            if not isinstance(op, str) or op not in {"eq", "ne", "gt", "ge", "lt", "le"} or "value" not in row:
                raise ObserverValidationError("Expected supported op and scalar value", path=loc)
            if (
                not isinstance(expected, (str, int, float, bool, type(None)))
                or isinstance(expected, float)
                and not math.isfinite(expected)
                or isinstance(expected, str)
                and len(expected.encode()) > 4096
            ):
                raise ObserverValidationError("Expected bounded finite scalar", path=loc + ".value")
            if op not in {"eq", "ne"} and (type(expected) not in {int, float}):
                raise ObserverValidationError("Ordered comparisons require numeric values", path=loc)
            predicates.append(
                Predicate(
                    json_pointer(row.get("pointer", ""), loc + ".pointer"),
                    cast(Literal["eq", "ne", "gt", "ge", "lt", "le"], op),
                    expected,
                )
            )
        return ConditionDetectorDef(id=identifier, source=source_id, all=tuple(predicates))
    raise ObserverValidationError("Unknown detector", path=path)
