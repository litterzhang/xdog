"""Minimal native validation and errors for OpenAI proxy endpoints."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, cast

from xdog.ai.native import ProtocolRequest

OpenAIProtocol = Literal["openai-responses", "openai-completions"]


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
    return ParsedRequest(
        model=model,
        stream=stream,
        native=ProtocolRequest.from_json(
            protocol,
            cast(dict[str, Any], value),
            headers=headers or {},
        ),
    )
