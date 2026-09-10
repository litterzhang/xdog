"""Lossless protocol-native request and response transport types."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

_REQUEST_HEADERS_BY_PROTOCOL = {
    "anthropic-messages": frozenset({
        "anthropic-version",
        "anthropic-beta",
        "anthropic-workspace-id",
        "anthropic-user-profile-id",
    }),
    "openai-completions": frozenset({
        "openai-organization",
        "openai-project",
        "openai-beta",
        "idempotency-key",
    }),
    "openai-responses": frozenset({
        "openai-organization",
        "openai-project",
        "openai-beta",
        "idempotency-key",
    }),
}


def _header_items(
    headers: Mapping[str, str] | tuple[tuple[str, str], ...],
    *,
    allowed: frozenset[str] | None = None,
) -> tuple[tuple[str, str], ...]:
    values: dict[str, str] = {}
    items = headers.items() if isinstance(headers, Mapping) else headers
    for raw_name, raw_value in items:
        name = raw_name.lower().strip()
        value = raw_value.strip()
        if allowed is not None and name not in allowed:
            continue
        if not name or "\r" in name or "\n" in name or "\r" in value or "\n" in value:
            raise ValueError("Invalid HTTP header")
        values[name] = value
    return tuple(values.items())


class NativeOperation(StrEnum):
    """A constrained operation understood by protocol-native transports."""

    GENERATE = "generate"
    COUNT_TOKENS = "count_tokens"


@dataclass(frozen=True)
class ProtocolRequest:
    """An immutable JSON request for one specific wire protocol.

    The encoded JSON is the source of truth. Accessors always decode a fresh
    object, so nested caller-owned dictionaries cannot mutate a queued request.
    """

    protocol: str
    body: bytes
    headers: tuple[tuple[str, str], ...] = ()
    operation: NativeOperation = NativeOperation.GENERATE

    @classmethod
    def from_json(
        cls,
        protocol: str,
        body: Mapping[str, Any],
        *,
        headers: Mapping[str, str] | tuple[tuple[str, str], ...] = (),
        operation: NativeOperation = NativeOperation.GENERATE,
    ) -> ProtocolRequest:
        if not protocol:
            raise ValueError("protocol must be non-empty")
        encoded = json.dumps(
            dict(body), ensure_ascii=False, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
        return cls(
            protocol=protocol,
            body=encoded,
            headers=_header_items(
                headers,
                allowed=_REQUEST_HEADERS_BY_PROTOCOL.get(protocol, frozenset()),
            ),
            operation=operation,
        )

    def json(self) -> dict[str, Any]:
        value = json.loads(self.body)
        if not isinstance(value, dict):
            raise ValueError("Protocol request body must be a JSON object")
        return value

    def header(self, name: str) -> str | None:
        lowered = name.lower()
        return next((value for key, value in self.headers if key == lowered), None)


@dataclass(frozen=True)
class NativeResponseStart:
    """Status and safe response headers available before native stream data."""

    status: int
    headers: tuple[tuple[str, str], ...] = ()

    def header(self, name: str) -> str | None:
        lowered = name.lower()
        return next((value for key, value in self.headers if key == lowered), None)


@dataclass(frozen=True)
class NativeResponse:
    """A complete protocol-native HTTP response."""

    status: int
    body: bytes
    headers: tuple[tuple[str, str], ...] = ()

    def json(self) -> Any:
        return json.loads(self.body)

    def header(self, name: str) -> str | None:
        lowered = name.lower()
        return next((value for key, value in self.headers if key == lowered), None)


@dataclass(frozen=True)
class NativeSSEEvent:
    """One SSE event whose optional name and data remain in the upstream protocol."""

    event: str | None
    data: bytes

    def json(self) -> Any:
        return json.loads(self.data)

    def encode(self) -> bytes:
        data_lines = self.data.decode("utf-8").splitlines() or [""]
        fields = [*(f"data: {line}" for line in data_lines)]
        if self.event is not None:
            fields.insert(0, f"event: {self.event}")
        return ("\n".join(fields) + "\n\n").encode("utf-8")


class NativeEventStream:
    """Closable async stream of protocol-native SSE events."""

    def __init__(
        self,
        start: NativeResponseStart,
        iterator: AsyncIterator[NativeSSEEvent],
        close: Callable[[], Awaitable[None]],
    ) -> None:
        self.start = start
        self._iterator = iterator
        self._close = close
        self._consumed = False
        self._closed = False

    def __aiter__(self) -> AsyncIterator[NativeSSEEvent]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[NativeSSEEvent]:
        if self._consumed:
            return
        try:
            async for event in self._iterator:
                yield event
        finally:
            self._consumed = True
            await self.aclose()

    async def aclose(self) -> None:
        self._consumed = True
        if self._closed:
            return
        self._closed = True
        await self._close()


class NativeHTTPError(RuntimeError):
    """An upstream non-success response with its native envelope intact."""

    def __init__(self, response: NativeResponse) -> None:
        self.response = response
        super().__init__(f"Upstream returned HTTP {response.status}")
