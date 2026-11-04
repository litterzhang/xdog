"""Stable error facts, not retry, routing, authentication or replay policy."""
from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass
from typing import Literal

import httpx

FailureKind = Literal[
    'authentication', 'permission', 'rate_limit', 'model', 'context', 'unsupported',
    'request', 'transport', 'upstream', 'protocol', 'parse', 'cancelled', 'unknown',
]


@dataclass(frozen=True, slots=True)
class ModelFailure:
    kind: FailureKind = 'unknown'
    status: int | None = None
    code: str | None = None
    # Whether any response content was observed, not whether a tool had effects.
    partial: bool = False

    def __post_init__(self) -> None:
        if self.kind not in {
            'authentication', 'permission', 'rate_limit', 'model', 'context', 'unsupported',
            'request', 'transport', 'upstream', 'protocol', 'parse', 'cancelled', 'unknown',
        }:
            raise ValueError('Invalid model failure kind')
        if self.status is not None and (type(self.status) is not int or not 100 <= self.status <= 599):
            raise ValueError('Invalid model failure HTTP status')
        if type(self.partial) is not bool:
            raise ValueError('Invalid partial-response flag')
        if self.code is not None and (not isinstance(self.code, str) or not self.code or len(self.code) > 128
                or not all(c.isalnum() or c in '._-' for c in self.code)):
            raise ValueError('Invalid model failure machine code')

    def to_dict(self) -> dict[str, str | int | bool | None]:
        return asdict(self)


def model_failure(
    error: BaseException | None = None, *, status: int | None = None,
    code: str | None = None, partial: bool = False,
) -> ModelFailure:
    """Classify explicit status/code/exception types; never guess from prose.

    URLs, headers, response bodies and credentials are deliberately not retained.
    Unknown 400s stay request errors, not guessed context overflow or auth failures.
    """
    from xdog.ai.native import NativeHTTPError
    from xdog.ai.types import AuthExpiredError

    if code is None:
        code = getattr(error, 'code', None)
    code = code if isinstance(code, str) else None
    status = status if type(status) is int and 100 <= status <= 599 else None
    if isinstance(error, ModelCapabilityError):
        return error.failure
    if isinstance(error, NativeHTTPError):
        if code is None:
            return http_failure(error.response.status, error.response.body, partial=partial)
        status = error.response.status
    elif isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
    elif status is None:
        candidate = getattr(error, 'status_code', None)
        if type(candidate) is int:
            status = candidate
    status = status if type(status) is int and 100 <= status <= 599 else None
    kind: FailureKind = 'unknown'
    if isinstance(error, asyncio.CancelledError):
        kind = 'cancelled'
    elif isinstance(error, AuthExpiredError) or status == 401:
        kind = 'authentication'
    elif status == 403:
        kind = 'permission'
    elif status == 429 or code in {'rate_limit_exceeded', 'rate_limit_error'}:
        kind = 'rate_limit'
    elif code in {'context_length_exceeded', 'prompt_too_long', 'max_tokens_exceeded'}:
        kind = 'context'
    elif code in {'model_not_found', 'model_not_supported'}:
        kind = 'model'
    elif isinstance(error, json.JSONDecodeError):
        kind = 'parse'
    elif isinstance(error, httpx.RemoteProtocolError):
        kind = 'protocol'
    elif isinstance(error, httpx.TransportError):
        kind = 'transport'
    elif status is not None and 400 <= status < 500:
        kind = 'request'
    elif status is not None and status >= 500:
        kind = 'upstream'
    # Only bounded machine codes are public; not arbitrary upstream text.
    safe_code = code if code and len(code) <= 128 and all(c.isalnum() or c in '._-' for c in code) else None
    return ModelFailure(kind, status, safe_code, partial)


class ModelCapabilityError(NotImplementedError):
    """Explicitly unsupported request; no authentication or network attempt needed."""

    def __init__(self, feature: str) -> None:
        self.failure = ModelFailure('unsupported', code=feature)
        super().__init__(f'Model explicitly does not support {feature.replace("_", " ")}')


def http_failure(status: int, body: bytes, *, partial: bool = False) -> ModelFailure:
    """Project known HTTP error-envelope machine fields, never keep the raw body."""
    code = None
    try:
        value = json.loads(body)
        error = value.get('error') if isinstance(value, dict) else None
        if isinstance(error, dict):
            code = error.get('code') or error.get('type')
    except (ValueError, UnicodeError):
        pass
    return model_failure(status=status, code=code, partial=partial)
