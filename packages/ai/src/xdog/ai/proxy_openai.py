"""Minimal native validation and errors for OpenAI proxy endpoints."""

from __future__ import annotations

import base64
import binascii
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, cast

from xdog.ai.native import ProtocolRequest

OpenAIProtocol = Literal["openai-responses", "openai-completions"]


_LEGACY_REASONING_ID_PREFIX = "rs_xdog_v1_"
_RESPONSE_ITEM_ID_MAX_LENGTH = 64


class InvalidRequest(ValueError):
    """A malformed outer OpenAI request."""

    def __init__(self, message: str, param: str | None = None) -> None:
        super().__init__(message)
        self.param = param


@dataclass(frozen=True)
class ParsedRequest:
    model: str
    stream: bool
    native: ProtocolRequest


def _legacy_reasoning_id(value: str) -> str | None:
    if not value.startswith(_LEGACY_REASONING_ID_PREFIX):
        return None
    encoded = value[len(_LEGACY_REASONING_ID_PREFIX):]
    if not encoded or re.fullmatch(r"[A-Za-z0-9_-]+", encoded) is None:
        return None
    try:
        decoded = base64.b64decode(
            encoded + "=" * (-len(encoded) % 4),
            altchars=b"-_",
            validate=True,
        ).decode()
    except (binascii.Error, UnicodeError):
        return None
    canonical = _LEGACY_REASONING_ID_PREFIX + base64.urlsafe_b64encode(decoded.encode()).decode().rstrip("=")
    return decoded if decoded and canonical == value else None


def _migrate_legacy_reasoning_item(raw: Any) -> tuple[Any, bool]:
    if not isinstance(raw, dict) or raw.get("type") != "reasoning":
        return raw, False
    item_id = raw.get("id")
    original_id = _legacy_reasoning_id(item_id) if isinstance(item_id, str) else None
    if original_id is None:
        return raw, False
    if len(original_id) <= _RESPONSE_ITEM_ID_MAX_LENGTH:
        return {**raw, "id": original_id}, True
    return None, True


def _migrate_legacy_responses_reasoning(body: dict[str, Any]) -> dict[str, Any]:
    input_items = body.get("input")
    if not isinstance(input_items, list):
        return body
    migrated: list[Any] = []
    changed = False
    for raw in input_items:
        item, item_changed = _migrate_legacy_reasoning_item(raw)
        changed = changed or item_changed
        if item is not None:
            migrated.append(item)
    return {**body, "input": migrated} if changed else body


def error_body(
    message: str,
    *,
    kind: str = "invalid_request_error",
    param: str | None = None,
    code: str | None = None,
) -> dict[str, Any]:
    """Build the standard OpenAI error envelope."""
    return {
        "error": {
            "message": message,
            "type": kind,
            "param": param,
            "code": code,
        },
    }


def parse_request(
    value: Any,
    protocol: OpenAIProtocol,
    headers: Mapping[str, str] | None = None,
) -> ParsedRequest:
    """Validate routing-owned fields and preserve every other JSON value."""
    if not isinstance(value, dict):
        raise InvalidRequest("body must be an object", "body")
    model = value.get("model")
    if not isinstance(model, str) or not model:
        raise InvalidRequest("model must be a non-empty string", "model")
    stream = value.get("stream", False)
    if not isinstance(stream, bool):
        raise InvalidRequest("stream must be a boolean", "stream")
    if protocol == "openai-completions" and not isinstance(value.get("messages"), list):
        raise InvalidRequest("messages must be an array", "messages")
    body = cast(dict[str, Any], value)
    if protocol == "openai-responses":
        body = _migrate_legacy_responses_reasoning(body)
    return ParsedRequest(
        model=model,
        stream=stream,
        native=ProtocolRequest.from_json(
            protocol,
            body,
            headers=headers or {},
        ),
    )
