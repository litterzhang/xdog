"""Presentation-neutral events emitted while a Claw turn is running."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, TypeAlias

_SECRET_KEY = re.compile(
    r"(?:password|passwd|secret|token|authorization|api[_-]?key|credential)",
    re.IGNORECASE,
)
_DISPLAY_STRING_LIMIT = 1024
_DISPLAY_RESULT_LIMIT = 16 * 1024
_SECRET_TEXT_PATTERNS = (
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+"),
    re.compile(
        r"(?i)((?:api[_-]?key|token|password|passwd|secret|credential)\s*[=:]\s*)"
        r"[^\s,;]+"
    ),
)


def display_arguments(arguments: dict[str, Any], *, bounded: bool = True) -> dict[str, Any]:
    """Return recursively redacted, bounded tool arguments for local display."""
    return {
        str(key): _display_value(value, key=str(key), bounded=bounded)
        for key, value in arguments.items()
    }


def display_result(result: str, *, bounded: bool = True) -> str:
    """Redact common inline credentials and bound one display result."""
    redacted = result
    for pattern in _SECRET_TEXT_PATTERNS:
        redacted = pattern.sub(r"\1<redacted>", redacted)
    if not bounded or len(redacted) <= _DISPLAY_RESULT_LIMIT:
        return redacted
    return redacted[:_DISPLAY_RESULT_LIMIT] + "\n… (display result truncated)"


def _display_value(value: Any, *, key: str = "", bounded: bool = True) -> Any:
    if _SECRET_KEY.search(key):
        return "<redacted>"
    if isinstance(value, dict):
        return {
            str(child_key): _display_value(child, key=str(child_key), bounded=bounded)
            for child_key, child in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_display_value(item, bounded=bounded) for item in (value[:50] if bounded else value)]
    if isinstance(value, str):
        redacted = display_result(value, bounded=bounded)
        if not bounded or len(redacted) <= _DISPLAY_STRING_LIMIT:
            return redacted
        return redacted[:_DISPLAY_STRING_LIMIT] + "…"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:_DISPLAY_STRING_LIMIT]


@dataclass(frozen=True, slots=True)
class AssistantTextDelta:
    delta: str

    def to_wire(self) -> dict[str, Any]:
        return {"type": "assistant_delta", "delta": self.delta}


@dataclass(frozen=True, slots=True)
class ToolStarted:
    tool_call_id: str
    name: str
    arguments: dict[str, Any]

    def to_wire(self) -> dict[str, Any]:
        return {
            "type": "tool_call",
            "id": self.tool_call_id,
            "name": self.name,
            "arguments": display_arguments(self.arguments),
        }


@dataclass(frozen=True, slots=True)
class ToolUpdated:
    tool_call_id: str
    name: str
    result: str
    images: tuple[dict[str, str], ...] = ()

    def to_wire(self) -> dict[str, Any]:
        return {
            "type": "tool_update",
            "id": self.tool_call_id,
            "name": self.name,
            "result": display_result(self.result),
            "images": list(self.images),
        }


@dataclass(frozen=True, slots=True)
class ToolFinished:
    tool_call_id: str
    name: str
    result: str
    is_error: bool
    images: tuple[dict[str, str], ...] = ()

    def to_wire(self) -> dict[str, Any]:
        return {
            "type": "tool_result",
            "id": self.tool_call_id,
            "name": self.name,
            "result": display_result(self.result),
            "is_error": self.is_error,
            "images": list(self.images),
        }


@dataclass(frozen=True, slots=True)
class InputConsumed:
    id: str
    content: str
    delivery: str
    image_count: int = 0

    def to_wire(self) -> dict[str, Any]:
        return {"type": "input_consumed", "id": self.id, "content": self.content,
                "delivery": self.delivery, "image_count": self.image_count}


@dataclass(frozen=True, slots=True)
class ExtensionNotice:
    data: dict[str, Any]

    def to_wire(self) -> dict[str, Any]:
        return dict(self.data)


DisplayEvent: TypeAlias = (
    AssistantTextDelta | ToolStarted | ToolUpdated | ToolFinished | InputConsumed | ExtensionNotice
)
