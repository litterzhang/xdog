"""Load and validate the complete observer before starting any side effects."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import cast

from xdog.eyes.errors import ObserverValidationError
from xdog.eyes.models import (
    EVENT_TYPES,
    DetectorDef,
    EventType,
    HandlerDef,
    ObserverDef,
    RouteDef,
    SourceDef,
)


def _object(value: object, path: str, allowed: set[str]) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ObserverValidationError("Expected an object", path=path, code="wrong_shape")
    data = cast(dict[str, object], value)
    unknown = data.keys() - allowed
    if unknown:
        raise ObserverValidationError(f"Unknown fields: {sorted(unknown)}", path=path, code="unknown_field")
    return data


def _text(value: object, path: str, *, maximum: int = 256) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ObserverValidationError("Expected nonempty text", path=path, code="wrong_shape")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeError as exc:
        raise ObserverValidationError("Expected valid UTF-8 text", path=path, code="wrong_shape") from exc
    if size > maximum:
        raise ObserverValidationError(f"Text exceeds {maximum} bytes", path=path, code="capacity")
    return value


def _integer(value: object, path: str) -> int:
    if type(value) is not int or value <= 0:
        raise ObserverValidationError("Expected a positive integer", path=path, code="wrong_shape")
    return value


def _items(value: object, path: str, *, empty: bool = False) -> list[object]:
    if not isinstance(value, list) or (not empty and not value):
        raise ObserverValidationError("Expected a nonempty array", path=path, code="wrong_shape")
    return value


def _patterns(value: object, path: str, *, empty: bool = False) -> tuple[str, ...]:
    result = []
    for index, item in enumerate(_items(value, path, empty=empty)):
        pattern = _text(item, f"{path}[{index}]", maximum=4096)
        segments = pattern.split("/")
        if (pattern.startswith(("/", "!")) or any(part in {"", ".", ".."} for part in segments)
                or any(char in pattern for char in "[]\\") or any("**" in part and part != "**" for part in segments)):
            raise ObserverValidationError("Invalid relative glob", path=path, code="invalid_pattern")
        result.append(pattern)
    return tuple(result)


def _source(value: object, path: str, base_dir: Path) -> SourceDef:
    data = _object(value, path, {"id", "type", "root", "include", "exclude", "poll_interval_ms",
                                 "max_files", "max_file_bytes"})
    if data.get("type") != "file":
        raise ObserverValidationError("Only file sources are supported", path=path, code="unknown_type")
    root = Path(_text(data.get("root"), path + ".root", maximum=4096))
    # Absolute lexical roots retain symlink components for the no-follow scanner to reject.
    root = Path(os.path.abspath(base_dir / root))
    return SourceDef(
        id=_text(data.get("id"), path + ".id"), type="file", root=root,
        include=_patterns(data.get("include", ["**"]), path + ".include"),
        exclude=_patterns(data.get("exclude", []), path + ".exclude", empty=True),
        poll_interval_ms=_integer(data.get("poll_interval_ms", 1000), path + ".poll_interval_ms"),
        max_files=_integer(data.get("max_files", 10_000), path + ".max_files"),
        max_file_bytes=_integer(data.get("max_file_bytes", 10 * 1024 * 1024), path + ".max_file_bytes"),
    )


def _detector(value: object, path: str) -> DetectorDef:
    data = _object(value, path, {"id", "type", "source", "debounce_ms", "max_batch_delay_ms"})
    if data.get("type") != "file.change":
        raise ObserverValidationError("Unknown detector", path=path, code="unknown_type")
    debounce = _integer(data.get("debounce_ms", 500), path + ".debounce_ms")
    maximum = _integer(data.get("max_batch_delay_ms", 2000), path + ".max_batch_delay_ms")
    if maximum < debounce:
        raise ObserverValidationError("Maximum batch delay must cover debounce", path=path)
    return DetectorDef(_text(data.get("id"), path + ".id"), "file.change",
                       _text(data.get("source"), path + ".source"), debounce, maximum)


def _handler(value: object, path: str) -> HandlerDef:
    data = _object(value, path, {"id", "type", "target", "instruction"})
    if data.get("type") != "session.notify" or data.get("target") != "current":
        raise ObserverValidationError("Only current-session notification is supported", path=path, code="unknown_type")
    return HandlerDef(_text(data.get("id"), path + ".id"), "session.notify", "current",
                      _text(data.get("instruction"), path + ".instruction", maximum=65536))


def _route(value: object, path: str) -> RouteDef:
    data = _object(value, path, {"id", "detector", "handler", "event_types"})
    values = _items(data.get("event_types"), path + ".event_types")
    if any(value not in EVENT_TYPES for value in values) or len(set(map(str, values))) != len(values):
        raise ObserverValidationError("Expected unique supported event types", path=path)
    return RouteDef(_text(data.get("id"), path + ".id"), _text(data.get("detector"), path + ".detector"),
                    _text(data.get("handler"), path + ".handler"), tuple(cast(EventType, value) for value in values))


def parse(value: object, *, base_dir: Path) -> ObserverDef:
    data = _object(value, "$", {"version", "name", "sources", "detectors", "handlers", "routes"})
    if type(data.get("version")) is not int or data["version"] != 1:
        raise ObserverValidationError("Expected version 1", path="$.version", code="unknown_version")
    name = _text(data.get("name"), "$.name")
    sources = tuple(_source(item, f"$.sources[{i}]", base_dir)
                    for i, item in enumerate(_items(data.get("sources"), "$.sources")))
    detectors = tuple(_detector(item, f"$.detectors[{i}]")
                      for i, item in enumerate(_items(data.get("detectors"), "$.detectors")))
    handlers = tuple(_handler(item, f"$.handlers[{i}]")
                     for i, item in enumerate(_items(data.get("handlers"), "$.handlers")))
    routes = tuple(_route(item, f"$.routes[{i}]")
                   for i, item in enumerate(_items(data.get("routes"), "$.routes")))
    identifiers = ([item.id for item in sources] + [item.id for item in detectors]
                   + [item.id for item in handlers] + [item.id for item in routes])
    if len(set(identifiers)) != len(identifiers):
        raise ObserverValidationError("Identifiers must be unique", code="duplicate_id")
    source_ids, detector_ids, handler_ids = ({item.id for item in sources}, {item.id for item in detectors},
                                            {item.id for item in handlers})
    for detector in detectors:
        if detector.source not in source_ids:
            raise ObserverValidationError(f"Missing source: {detector.source}", code="missing_reference")
    for route in routes:
        if route.detector not in detector_ids or route.handler not in handler_ids:
            raise ObserverValidationError(f"Missing reference in route: {route.id}", code="missing_reference")
    return ObserverDef(1, name, sources, detectors, handlers, routes)


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ObserverValidationError(f"Duplicate JSON key: {key}", code="duplicate_key")
        result[key] = value
    return result


def load(path: Path) -> ObserverDef:
    try:
        data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_pairs)
    except (OSError, ValueError) as exc:
        if isinstance(exc, ObserverValidationError):
            raise
        raise ObserverValidationError(str(exc), code="invalid_document") from exc
    return parse(data, base_dir=path.absolute().parent)
