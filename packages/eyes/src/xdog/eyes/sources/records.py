"""Finite immutable remote facts; no transport, detector or persistence ownership."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


def pointer(value: Any, path: str) -> Any:
    """Strict RFC 6901 traversal; missing values are errors, never empty datasets."""
    if not path:
        return value
    if not path.startswith("/"):
        raise ValueError("JSON pointer must be empty or start with /")
    for token in path.split("/")[1:]:
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(value, dict):
            value = value[token]
        elif isinstance(value, list) and token.isascii() and token.isdecimal() and (token == "0" or token[0] != "0"):
            value = value[int(token)]
        else:
            raise ValueError("JSON pointer does not select a value")
    return value


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def decode(raw: str | bytes) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON object key")
            result[key] = value
        return result

    def constant(value: str) -> Any:
        raise ValueError("Non-finite JSON value")

    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


@dataclass(frozen=True)
class Record:
    key: str
    value: str  # Canonical JSON, immutable and bounded by the source.
    url: str
    updated_at: float | None = None


@dataclass(frozen=True)
class RecordSnapshot:
    records: tuple[Record, ...]
    observed_at: float
    healthy: bool = True
    complete: bool = True
    unchanged: bool = False
    status_code: int | None = None
    error: str = ""
    gap: bool = False
    valid: bool = True
    window_start: float | None = None


@dataclass(frozen=True)
class PollResult:
    snapshot: RecordSnapshot
    state: dict[str, Any]  # Candidate JSON only: executor owns the actual commit.
    delay: float
