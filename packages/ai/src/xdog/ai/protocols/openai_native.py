"""Lossless native transport shared by OpenAI Responses and Chat Completions."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any, Literal

import httpx
from xdog.ai.core import AuthResult
from xdog.ai.native import (
    NativeEventStream,
    NativeHTTPError,
    NativeOperation,
    NativeResponse,
    NativeResponseStart,
    NativeSSEEvent,
    ProtocolRequest,
)
from xdog.ai.types import Model

_DEFAULT_BASE_URL = "https://api.githubcopilot.com"
_SAFE_RESPONSE_HEADERS = frozenset({
    "content-type",
    "request-id",
    "x-request-id",
    "retry-after",
    "openai-organization",
    "openai-project",
    "openai-processing-ms",
    "openai-version",
})
_SAFE_RESPONSE_PREFIXES = ("x-ratelimit-", "ratelimit-")
_CREDENTIAL_HEADERS = frozenset({"authorization", "x-api-key"})
_TRANSPORT_HEADERS = frozenset({
    "host",
    "content-length",
    "transfer-encoding",
    "connection",
    "content-encoding",
})


@dataclass(frozen=True)
class OpenAIEndpoint:
    """Wire-specific policy for one native OpenAI generation endpoint."""

    protocol: str
    path: str
    sse_style: Literal["named", "data"]
    event_model_location: Literal["response", "root"]


RESPONSES = OpenAIEndpoint(
    protocol="openai-responses",
    path="/v1/responses",
    sse_style="named",
    event_model_location="response",
)
CHAT_COMPLETIONS = OpenAIEndpoint(
    protocol="openai-completions",
    path="/v1/chat/completions",
    sse_style="data",
    event_model_location="root",
)


def _response_headers(headers: httpx.Headers) -> tuple[tuple[str, str], ...]:
    return tuple(
        (name.lower(), value)
        for name, value in headers.multi_items()
        if name.lower() in _SAFE_RESPONSE_HEADERS
        or name.lower().startswith(_SAFE_RESPONSE_PREFIXES)
    )


def _header_map(headers: Mapping[str, str]) -> dict[str, tuple[str, str]]:
    return {name.lower(): (name, value) for name, value in headers.items()}


def _request_headers(model: Model, request: ProtocolRequest, auth: AuthResult) -> dict[str, str]:
    model_headers = _header_map(model.headers)
    client_headers = _header_map(dict(request.headers))
    auth_headers = _header_map(auth.headers)
    merged = {
        "content-type": ("Content-Type", "application/json"),
        **model_headers,
        **client_headers,
    }
    for name in _CREDENTIAL_HEADERS | _TRANSPORT_HEADERS:
        merged.pop(name, None)
    merged.update(auth_headers)
    for name in _TRANSPORT_HEADERS:
        merged.pop(name, None)
    merged["content-type"] = ("Content-Type", "application/json")
    if auth.api_key and not _CREDENTIAL_HEADERS.intersection(auth_headers):
        merged["authorization"] = ("Authorization", f"Bearer {auth.api_key}")
    return {original: value for original, value in merged.values()}


def _request_body(
    endpoint: OpenAIEndpoint,
    model: Model,
    request: ProtocolRequest,
    *,
    stream: bool,
) -> tuple[dict[str, Any], str]:
    if request.operation is not NativeOperation.GENERATE:
        raise ValueError(
            f"Protocol {endpoint.protocol!r} does not support operation {request.operation.value!r}",
        )
    if request.protocol != endpoint.protocol:
        raise ValueError(f"Expected {endpoint.protocol} request, got {request.protocol!r}")
    source = request.json()
    client_model = source.get("model", "")
    if not isinstance(client_model, str):
        raise ValueError("model must be a string")
    return ({**source, "model": model.id, "stream": stream}, client_model)


def _encode_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _restore_response_model(body: bytes, client_model: str) -> bytes:
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return body
    if not isinstance(payload, dict) or "model" not in payload:
        return body
    return _encode_json({**payload, "model": client_model})


def _restore_event_model(
    endpoint: OpenAIEndpoint,
    data: bytes,
    client_model: str,
) -> bytes:
    if data == b"[DONE]":
        return data
    try:
        payload = json.loads(data)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return data
    if not isinstance(payload, dict):
        return data
    if endpoint.event_model_location == "root":
        if "model" not in payload:
            return data
        return _encode_json({**payload, "model": client_model})
    response = payload.get("response")
    if not isinstance(response, dict) or "model" not in response:
        return data
    return _encode_json({**payload, "response": {**response, "model": client_model}})


async def _iter_sse_events(
    response: httpx.Response,
    endpoint: OpenAIEndpoint,
    client_model: str,
) -> AsyncIterator[NativeSSEEvent]:
    event_name: str | None = None
    data_lines: list[str] = []

    async for line in response.aiter_lines():
        if not line:
            if data_lines:
                data = "\n".join(data_lines).encode("utf-8")
                yield NativeSSEEvent(
                    event=event_name if endpoint.sse_style == "named" else None,
                    data=_restore_event_model(endpoint, data, client_model),
                )
                event_name = None
                data_lines = []
            continue
        if line.startswith(":"):
            continue
        field, separator, value = line.partition(":")
        if not separator:
            continue
        value = value[1:] if value.startswith(" ") else value
        if field == "event":
            event_name = value
        elif field == "data":
            data_lines = [*data_lines, value]

    if data_lines:
        data = "\n".join(data_lines).encode("utf-8")
        yield NativeSSEEvent(
            event=event_name if endpoint.sse_style == "named" else None,
            data=_restore_event_model(endpoint, data, client_model),
        )


async def request_complete(
    endpoint: OpenAIEndpoint,
    model: Model,
    request: ProtocolRequest,
    auth: AuthResult,
) -> NativeResponse:
    body, client_model = _request_body(endpoint, model, request, stream=False)
    url = f"{(model.base_url or _DEFAULT_BASE_URL).rstrip('/')}{endpoint.path}"
    async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as client:
        response = await client.post(
            url,
            content=_encode_json(body),
            headers=_request_headers(model, request, auth),
        )
    headers = _response_headers(response.headers)
    native = NativeResponse(
        status=response.status_code,
        body=response.content,
        headers=headers,
    )
    if not 200 <= response.status_code < 300:
        raise NativeHTTPError(native)
    return NativeResponse(
        status=response.status_code,
        body=_restore_response_model(response.content, client_model),
        headers=headers,
    )


async def request_stream(
    endpoint: OpenAIEndpoint,
    model: Model,
    request: ProtocolRequest,
    auth: AuthResult,
) -> NativeEventStream:
    body, client_model = _request_body(endpoint, model, request, stream=True)
    url = f"{(model.base_url or _DEFAULT_BASE_URL).rstrip('/')}{endpoint.path}"
    stack = AsyncExitStack()
    try:
        client = await stack.enter_async_context(
            httpx.AsyncClient(timeout=httpx.Timeout(300.0)),
        )
        response = await stack.enter_async_context(client.stream(
            "POST",
            url,
            content=_encode_json(body),
            headers=_request_headers(model, request, auth),
        ))
        headers = _response_headers(response.headers)
        if not 200 <= response.status_code < 300:
            response_body = await response.aread()
            raise NativeHTTPError(NativeResponse(
                status=response.status_code,
                body=response_body,
                headers=headers,
            ))
    except BaseException:
        await stack.aclose()
        raise

    async def events() -> AsyncIterator[NativeSSEEvent]:
        try:
            async for event in _iter_sse_events(response, endpoint, client_model):
                yield event
        finally:
            await stack.aclose()

    return NativeEventStream(
        NativeResponseStart(status=response.status_code, headers=headers),
        events(),
        stack.aclose,
    )
