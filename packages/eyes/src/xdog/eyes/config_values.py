"""Finite scalar configuration values shared by content sources and detectors."""
from __future__ import annotations

import math
import re

from xdog.eyes.errors import ObserverValidationError
from xdog.eyes.loader import _integer


def json_pointer(value: object, path: str) -> str:
    if not isinstance(value, str) or len(value.encode("utf-8")) > 2048 or "\x00" in value:
        raise ObserverValidationError("Expected bounded JSON pointer", path=path)
    if value and not value.startswith("/") or re.search(r"~(?![01])", value):
        raise ObserverValidationError("Expected RFC 6901 JSON pointer", path=path)
    return value


def number(value: object, path: str, *, minimum: float, maximum: float) -> float:
    if (
        not isinstance(value, (float, int))
        or isinstance(value, bool)
        or not math.isfinite(value)
        or not minimum <= value <= maximum
    ):
        raise ObserverValidationError(f"Expected finite number {minimum}..{maximum}", path=path)
    return float(value)


def bounded_int(value: object, path: str, maximum: int) -> int:
    result = _integer(value, path)
    if result > maximum:
        raise ObserverValidationError(f"Expected integer at most {maximum}", path=path)
    return result


def boolean(value: object, path: str) -> bool:
    if type(value) is not bool:
        raise ObserverValidationError("Expected boolean", path=path)
    return value


def projection(value: object, path: str) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > 32:
        raise ObserverValidationError("Expected at most 32 comparison JSON pointers", path=path)
    selected = tuple(json_pointer(item, path) for item in value)
    if len(set(selected)) != len(selected):
        raise ObserverValidationError("Comparison pointers must be unique", path=path)
    return selected
