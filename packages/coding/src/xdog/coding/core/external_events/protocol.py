"""Source-neutral host protocol v1; no observer/harness implementation imports.

The wire contract lives in docs/eyes-host-protocol.md. Contract tests pin this
independent host validator to the observer client without a runtime dependency.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import TypeAlias, cast

JsonValue: TypeAlias = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
MAX_FRAME = 1024 * 1024
ACTIONS = frozenset({"hello", "register", "unregister", "ping", "deliver", "status", "resume"})
STATUSES = frozenset({"ok", "accepted", "duplicate", "busy", "paused", "stale-owner", "rejected"})


class ProtocolError(ValueError):
    """Invalid local protocol data; messages never include raw capabilities."""


def json_value(value: object, *, depth: int = 0) -> JsonValue:
    if depth > 32:
        raise ProtocolError("JSON nesting exceeds 32 levels")
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    if isinstance(value, list):
        return [json_value(item, depth=depth + 1) for item in value]
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return {key: json_value(item, depth=depth + 1) for key, item in value.items()}
    raise ProtocolError("Expected finite JSON data")


def object_value(value: object, *, allowed: set[str] | None = None,
                 required: set[str] | None = None) -> dict[str, JsonValue]:
    normalized = json_value(value)
    if not isinstance(normalized, dict):
        raise ProtocolError("Expected a JSON object")
    if allowed is not None and normalized.keys() - allowed:
        raise ProtocolError("Unknown protocol fields")
    if required is not None and required - normalized.keys():
        raise ProtocolError("Missing protocol fields")
    return normalized


def text(value: object, *, empty: bool = False, maximum: int = 256) -> str:
    if not isinstance(value, str) or (not empty and not value.strip()) or "\x00" in value:
        raise ProtocolError("Expected bounded protocol text")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeError as exc:
        raise ProtocolError("Expected valid UTF-8 protocol text") from exc
    if size > maximum:
        raise ProtocolError("Expected bounded protocol text")
    return value


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError("Duplicate JSON key")
        result[key] = value
    return result


def _constant(value: str) -> None:
    raise ProtocolError("Nonfinite JSON literal")


def encode_frame(value: object) -> bytes:
    data = object_value(value)
    frame = json.dumps(data, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode() + b"\n"
    if len(frame) > MAX_FRAME:
        raise ProtocolError("Frame exceeds 1 MiB")
    return frame


def decode_frame(frame: bytes) -> dict[str, JsonValue]:
    if len(frame) > MAX_FRAME or not frame.endswith(b"\n"):
        raise ProtocolError("Expected a bounded newline-terminated frame")
    try:
        result = json.loads(frame, object_pairs_hook=_pairs, parse_constant=_constant)
    except (ValueError, RecursionError, UnicodeError) as exc:
        raise ProtocolError("Invalid JSON frame") from exc
    return object_value(result)


@dataclass(frozen=True)
class Owner:
    host_instance: str
    session_id: str
    branch_id: str
    generation: str

    @classmethod
    def parse(cls, value: object) -> Owner:
        keys = {"host_instance", "session_id", "branch_id", "generation"}
        data = object_value(value, allowed=keys, required=keys)
        return cls(text(data["host_instance"]), text(data["session_id"]),
                   text(data["branch_id"], empty=True), text(data["generation"]))

    def as_json(self) -> dict[str, JsonValue]:
        return {"host_instance": self.host_instance, "session_id": self.session_id,
                "branch_id": self.branch_id, "generation": self.generation}


def _body(action: str, value: object) -> dict[str, JsonValue]:
    if action in {"hello", "ping"}:
        return object_value(value, allowed=set(), required=set())
    if action == "register":
        keys = {"registration_id", "route_id", "instruction"}
        data = object_value(value, allowed=keys, required=keys)
        text(data["instruction"], maximum=65536)
        text(data["registration_id"])
        text(data["route_id"])
        return data
    if action in {"unregister", "resume", "status"}:
        keys = {"registration_id"}
        data = object_value(value, allowed=keys, required=set() if action == "status" else keys)
        if "registration_id" in data:
            text(data["registration_id"])
        return data
    keys = {"registration_id", "route_id", "delivery_id", "events"}
    data = object_value(value, allowed=keys, required=keys)
    for key in keys - {"events"}:
        text(data[key])
    events = data["events"]
    if not isinstance(events, list) or not events or len(events) > 256:
        raise ProtocolError("Expected a bounded nonempty event list")
    identifiers = set()
    for value in events:
        event_keys = {"id", "source_id", "detector_id", "type", "observed_at", "evidence"}
        event = object_value(value, allowed=event_keys | {"payload"}, required=event_keys)
        identifier = text(event["id"])
        if identifier in identifiers:
            raise ProtocolError("Duplicate event identifier")
        identifiers.add(identifier)
        for key in ("source_id", "detector_id", "type"):
            text(event[key])
        timestamp = event["observed_at"]
        if type(timestamp) not in {int, float}:
            raise ProtocolError("Expected a finite observation time")
        try:
            finite = math.isfinite(float(cast(int | float, timestamp)))
        except OverflowError:
            finite = False
        if not finite:
            raise ProtocolError("Expected a finite observation time")
        evidence = event["evidence"]
        if not isinstance(evidence, list) or not evidence or any(not isinstance(item, dict) for item in evidence):
            raise ProtocolError("Expected structured evidence")
    return data


def validate_request(value: object) -> dict[str, JsonValue]:
    keys = {"version", "action", "token", "owner", "body"}
    data = object_value(value, allowed=keys, required=keys)
    if type(data["version"]) is not int or data["version"] != 1:
        raise ProtocolError("Unsupported protocol version")
    action = text(data["action"])
    if action not in ACTIONS:
        raise ProtocolError("Unknown protocol action")
    text(data["token"])
    Owner.parse(data["owner"])
    _body(action, data["body"])
    encode_frame(data)
    return data


def validate_response(value: object) -> dict[str, JsonValue]:
    keys = {"version", "status", "body"}
    data = object_value(value, allowed=keys, required=keys)
    if type(data["version"]) is not int or data["version"] != 1 or text(data["status"]) not in STATUSES:
        raise ProtocolError("Invalid protocol response")
    object_value(data["body"])
    return data
