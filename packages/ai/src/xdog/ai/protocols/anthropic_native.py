"""Lossless native transport for the Anthropic Messages wire protocol."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from contextlib import AsyncExitStack
from typing import Any

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
_DEFAULT_ANTHROPIC_VERSION = "2023-06-01"
_SAFE_RESPONSE_HEADERS = frozenset({
    "content-type",
    "request-id",
    "retry-after",
    "anthropic-organization-id",
    "anthropic-workspace-id",
})
_SAFE_RESPONSE_PREFIXES = ("anthropic-ratelimit-", "x-ratelimit-")
_CREDENTIAL_HEADERS = frozenset({"authorization", "x-api-key"})
_TRANSPORT_HEADERS = frozenset({
    "host",
    "content-length",
    "transfer-encoding",
    "connection",
    "content-encoding",
})


def _response_headers(headers: httpx.Headers) -> tuple[tuple[str, str], ...]:
    return tuple(
        (name.lower(), value)
        for name, value in headers.multi_items()
        if name.lower() in _SAFE_RESPONSE_HEADERS
        or name.lower().startswith(_SAFE_RESPONSE_PREFIXES)
    )


def _header_map(headers: Mapping[str, str]) -> dict[str, tuple[str, str]]:
    return {name.lower(): (name, value) for name, value in headers.items()}


def _beta_tokens(*values: str | None) -> str | None:
    ordered: dict[str, None] = {}
    for value in values:
        if value:
            for token in value.split(","):
                stripped = token.strip()
                if stripped:
                    ordered[stripped] = None
    return ",".join(ordered) or None


def _request_headers(model: Model, request: ProtocolRequest, auth: AuthResult) -> dict[str, str]:
    model_headers = _header_map(model.headers)
    client_headers = _header_map(dict(request.headers))
    auth_headers = _header_map(auth.headers)

    combined_beta = _beta_tokens(
        model_headers.get("anthropic-beta", ("", ""))[1],
        client_headers.get("anthropic-beta", ("", ""))[1],
    )
    merged = {
        "content-type": ("Content-Type", "application/json"),
        **model_headers,
        **client_headers,
    }
    if combined_beta is not None:
        merged["anthropic-beta"] = ("anthropic-beta", combined_beta)
    merged.setdefault(
        "anthropic-version",
        ("anthropic-version", _DEFAULT_ANTHROPIC_VERSION),
    )
    for name in _CREDENTIAL_HEADERS | _TRANSPORT_HEADERS:
        merged.pop(name, None)
    merged.update(auth_headers)
    for name in _TRANSPORT_HEADERS:
        merged.pop(name, None)
    merged["content-type"] = ("Content-Type", "application/json")

    has_authorization = "authorization" in auth_headers
    has_api_key = "x-api-key" in auth_headers
    if auth.api_key and not has_authorization and not has_api_key:
        base_url = model.base_url or _DEFAULT_BASE_URL
        if "anthropic.com" in base_url:
            merged["x-api-key"] = ("x-api-key", auth.api_key)
        else:
            merged["authorization"] = ("Authorization", f"Bearer {auth.api_key}")
    return {original: value for original, value in merged.values()}


def _request_body(model: Model, request: ProtocolRequest, *, stream: bool) -> tuple[dict[str, Any], str]:
    if request.protocol != "anthropic-messages":
        raise ValueError(f"Expected anthropic-messages request, got {request.protocol!r}")
    source = request.json()
    client_model = source.get("model", "")
    if not isinstance(client_model, str):
        raise ValueError("model must be a string")
    body = {**source, "model": model.id}
    if request.operation is NativeOperation.GENERATE:
        body["stream"] = stream
    elif stream:
        raise ValueError(f"Native operation {request.operation.value!r} cannot be streamed")
    return body, client_model


def _encode_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")


def _restore_response_model(body: bytes, client_model: str) -> bytes:
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return body
    if not isinstance(payload, dict) or "model" not in payload:
        return body
    return _encode_json({**payload, "model": client_model})


def _restore_event_model(event: str, data: bytes, client_model: str) -> bytes:
    try:
        payload = json.loads(data)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return data
    if not isinstance(payload, dict):
        return data
    event_type = payload.get("type", event)
    message = payload.get("message")
    if event_type != "message_start" or not isinstance(message, dict) or "model" not in message:
        return data
    return _encode_json({**payload, "message": {**message, "model": client_model}})


async def _iter_sse_events(
    response: httpx.Response,
    client_model: str,
) -> AsyncIterator[NativeSSEEvent]:
    event_name = "message"
    data_lines: list[str] = []

    async for line in response.aiter_lines():
        if not line:
            if data_lines:
                data = "\n".join(data_lines).encode("utf-8")
                yield NativeSSEEvent(
                    event=event_name,
                    data=_restore_event_model(event_name, data, client_model),
                )
                event_name = "message"
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
            event=event_name,
            data=_restore_event_model(event_name, data, client_model),
        )


async def request_complete(
    model: Model,
    request: ProtocolRequest,
    auth: AuthResult,
) -> NativeResponse:
    body, client_model = _request_body(model, request, stream=False)
    path = (
        "/v1/messages/count_tokens"
        if request.operation is NativeOperation.COUNT_TOKENS
        else "/v1/messages"
    )
    url = f"{(model.base_url or _DEFAULT_BASE_URL).rstrip('/')}{path}"
    async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as client:
        response = await client.post(
            url,
            content=_encode_json(body),
            headers=_request_headers(model, request, auth),
        )
    headers = _response_headers(response.headers)
    response_body = response.content
    native = NativeResponse(status=response.status_code, body=response_body, headers=headers)
    if not 200 <= response.status_code < 300:
        raise NativeHTTPError(native)
    if request.operation is NativeOperation.COUNT_TOKENS:
        return native
    return NativeResponse(
        status=response.status_code,
        body=_restore_response_model(response_body, client_model),
        headers=headers,
    )


async def request_stream(
    model: Model,
    request: ProtocolRequest,
    auth: AuthResult,
) -> NativeEventStream:
    if request.operation is not NativeOperation.GENERATE:
        raise ValueError(f"Native operation {request.operation.value!r} cannot be streamed")
    body, client_model = _request_body(model, request, stream=True)
    url = f"{(model.base_url or _DEFAULT_BASE_URL).rstrip('/')}/v1/messages"
    stack = AsyncExitStack()
    try:
        client = await stack.enter_async_context(httpx.AsyncClient(timeout=httpx.Timeout(300.0)))
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
            async for event in _iter_sse_events(response, client_model):
                yield event
        finally:
            await stack.aclose()

    return NativeEventStream(
        NativeResponseStart(status=response.status_code, headers=headers),
        events(),
        stack.aclose,
    )
