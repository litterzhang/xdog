"""Exact bounded Goal snapshots; no migration or mutable caller-owned JSON."""

from __future__ import annotations

import json
from dataclasses import asdict, fields, is_dataclass
from types import UnionType
from typing import Any, TypeVar, get_args, get_origin, get_type_hints

from xdog.goal.models import Goal, Notice

T = TypeVar("T")
FORMAT = "xdog.goal.snapshot.v1"
MAX_SNAPSHOT_BYTES = 1024 * 1024


def json_text(value: object, *, maximum: int = MAX_SNAPSHOT_BYTES) -> str:
    try:
        result = json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False)
    except (ValueError, TypeError, RecursionError) as exc:
        raise ValueError("Goal data must be finite bounded JSON") from exc
    if len(result.encode()) > maximum:
        raise ValueError("Goal data exceeds bounded storage capacity")
    return result


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate Goal snapshot field")
        result[key] = value
    return result


def _value(annotation: Any, value: object, depth: int) -> object:
    if depth > 32:
        raise ValueError("Goal snapshot nesting exceeds capacity")
    origin = get_origin(annotation)
    if origin is UnionType:
        choices = get_args(annotation)
        if value is None and type(None) in choices:
            return None
        return _value(next(item for item in choices if item is not type(None)), value, depth + 1)
    if origin is tuple:
        if not isinstance(value, list):
            raise ValueError("JSON Goal collection must be an array")
        item_type = get_args(annotation)[0]
        return tuple(_value(item_type, item, depth + 1) for item in value)
    if annotation in (str, int, bool):
        if type(value) is not annotation:
            raise ValueError("Goal snapshot primitive type cannot be coerced")
        return value
    if isinstance(annotation, type) and is_dataclass(annotation):
        return _model(annotation, value, depth + 1)
    raise ValueError("Unsupported Goal snapshot type")


def _model(kind: type[T], value: object, depth: int = 0) -> T:
    if not is_dataclass(kind):
        raise ValueError("Goal codec requires a registered data model")
    if not isinstance(value, dict) or set(value) != {item.name for item in fields(kind)}:
        raise ValueError("Unexpected Goal snapshot fields")
    hints = get_type_hints(kind)
    return kind(**{key: _value(hints[key], item, depth + 1) for key, item in value.items()})


def encode_goal(goal: Goal) -> str:
    if not isinstance(goal, Goal):
        raise ValueError("Expected a typed Goal snapshot")
    return json_text({"format": FORMAT, **asdict(goal)})


def decode_goal(payload: str) -> Goal:
    if not isinstance(payload, str) or len(payload.encode()) > MAX_SNAPSHOT_BYTES:
        raise ValueError("Goal snapshot exceeds capacity")
    try:
        data = json.loads(payload, object_pairs_hook=_unique)
        if not isinstance(data, dict) or data.pop("format", None) != FORMAT:
            raise ValueError("Unsupported Goal snapshot format; no upgrade is performed")
        return _model(Goal, data)
    except (RecursionError, TypeError, KeyError, StopIteration) as exc:
        raise ValueError("Malformed Goal snapshot") from exc


def encode_notice(notice: Notice) -> str:
    return json_text(asdict(notice), maximum=128 * 1024)


def decode_notice(payload: str) -> Notice:
    return _model(Notice, json.loads(payload, object_pairs_hook=_unique))
