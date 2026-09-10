from __future__ import annotations

import asyncio
import errno
import json
from copy import deepcopy
from typing import Any

import pytest
from xdog.ai.native import NativeHTTPError, NativeOperation, NativeResponse, ProtocolRequest
from xdog.ai.proxy import _handle_connection
from xdog.ai.proxy_anthropic import (
    InvalidRequest,
    _estimate_count_tokens,
    parse_count_tokens_request,
)
from xdog.ai.types import AuthExpiredError, Model


def test_count_tokens_parser_preserves_full_body_and_semantic_headers() -> None:
    body: dict[str, Any] = {
        "model": "claude-test",
        "messages": [{
            "role": "assistant",
            "content": [{
                "type": "thinking",
                "thinking": "opaque reasoning",
                "signature": "signed",
                "future": {"kept": True},
            }],
        }],
        "system": [{"type": "text", "text": "system", "cache_control": {"type": "ephemeral"}}],
        "tools": [{"name": "weather", "input_schema": {"type": "object"}}],
        "tool_choice": {"type": "auto"},
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": "high"},
        "cache_control": {"type": "ephemeral"},
        "context_management": {"edits": []},
        "mcp_servers": [{"type": "url", "url": "https://example.invalid/mcp"}],
        "speed": "fast",
        "stream": True,
        "future_parameter": {"opaque": [1, "two", True]},
    }

    parsed = parse_count_tokens_request(body, headers={
        "anthropic-version": "2026-09-01",
        "anthropic-beta": "beta-one,beta-two",
        "anthropic-workspace-id": "workspace",
        "anthropic-user-profile-id": "profile",
        "authorization": "Bearer local-secret",
        "x-api-key": "local-secret",
        "connection": "close",
    })

    assert parsed.model == "claude-test"
    assert parsed.native.operation is NativeOperation.COUNT_TOKENS
    assert parsed.native.json() == body
    assert dict(parsed.native.headers) == {
        "anthropic-version": "2026-09-01",
        "anthropic-beta": "beta-one,beta-two",
        "anthropic-workspace-id": "workspace",
        "anthropic-user-profile-id": "profile",
    }


def test_count_tokens_parser_takes_immutable_snapshot() -> None:
    body = {
        "model": "claude-test",
        "messages": [{"role": "user", "content": [{"type": "future", "opaque": [1]}]}],
    }
    expected = deepcopy(body)

    parsed = parse_count_tokens_request(body)
    body["messages"][0]["content"][0]["opaque"].append(2)

    assert parsed.native.json() == expected


@pytest.mark.parametrize(
    ("body", "param"),
    [
        (None, "body"),
        ({}, "model"),
        ({"model": "", "messages": []}, "model"),
        ({"model": "claude", "messages": {}}, "messages"),
        ({"model": "claude", "messages": ["message"]}, "messages[0]"),
        ({"model": "claude", "messages": [{}]}, "messages[0].role"),
        ({"model": "claude", "messages": [{"role": "tool", "content": "x"}]}, "messages[0].role"),
        ({"model": "claude", "messages": [{"role": "user", "content": {}}]}, "messages[0].content"),
        ({"model": "claude", "messages": [{"role": "user", "content": ["block"]}]}, "messages[0].content[0]"),
    ],
)
def test_count_tokens_parser_rejects_malformed_outer_structure(body: Any, param: str) -> None:
    with pytest.raises(InvalidRequest) as caught:
        parse_count_tokens_request(body)

    assert caught.value.param == param


def test_count_tokens_estimator_is_deterministic_non_negative_and_ignores_model() -> None:
    body = {
        "model": "claude-one",
        "messages": [{"role": "user", "content": "hello 世界"}],
        "future": {"value": True},
    }

    estimate = _estimate_count_tokens(body)

    assert estimate >= 0
    assert estimate == _estimate_count_tokens(body)
    assert estimate == _estimate_count_tokens({**body, "model": "a-different-routing-alias"})


def test_count_tokens_estimator_is_monotonic_for_prompt_content() -> None:
    variants = [
        {"model": "m", "messages": []},
        {"model": "m", "messages": [{"role": "user", "content": "hello"}]},
        {
            "model": "m",
            "messages": [{"role": "user", "content": "hello world"}],
            "system": "be concise",
        },
        {
            "model": "m",
            "messages": [{"role": "user", "content": "hello world"}],
            "system": "be concise",
            "tools": [{"name": "lookup", "input_schema": {"type": "object"}}],
        },
        {
            "model": "m",
            "messages": [
                {"role": "user", "content": "hello world"},
                {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "id": "tool_1", "name": "lookup", "input": {"q": "x"}}],
                },
                {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "tool_1", "content": "result"}],
                },
            ],
            "system": "be concise",
            "tools": [{"name": "lookup", "input_schema": {"type": "object"}}],
            "thinking": {"type": "adaptive"},
            "future": {"opaque": "included"},
        },
    ]

    estimates = [_estimate_count_tokens(body) for body in variants]

    assert estimates == sorted(estimates)
    assert len(set(estimates)) == len(estimates)


def test_count_tokens_estimator_handles_large_media_without_mutation() -> None:
    body = {
        "model": "m",
        "messages": [{
            "role": "user",
            "content": [{
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": "aW1hZ2U=" * 100_000,
                },
            }],
        }],
    }
    snapshot = deepcopy(body)

    estimate = _estimate_count_tokens(body)

    assert estimate > 0
    assert body == snapshot


def _parse_http(raw: bytes) -> tuple[int, list[tuple[str, str]], bytes]:
    head, separator, body = raw.partition(b"\r\n\r\n")
    assert separator
    lines = head.decode().split("\r\n")
    headers = [
        (name.lower(), value.strip())
        for line in lines[1:]
        for name, value in [line.split(":", 1)]
    ]
    return int(lines[0].split()[1]), headers, body


async def _request(
    provider: Any,
    body: Any,
    *,
    path: str = "/v1/messages/count_tokens",
    headers: list[tuple[str, str]] | None = None,
    api_key: str = "",
    chunked: bool = False,
) -> tuple[int, list[tuple[str, str]], bytes]:
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await _handle_connection(reader, writer, provider, api_key=api_key)

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    try:
        host, port = server.sockets[0].getsockname()[:2]
        reader, writer = await asyncio.open_connection(host, port)
        payload = body if isinstance(body, bytes) else json.dumps(body).encode()
        request_headers = [
            ("Host", "localhost"),
            ("Content-Type", "application/json"),
            *(headers or []),
        ]
        if chunked:
            request_headers.append(("Transfer-Encoding", "chunked"))
        else:
            request_headers.append(("Content-Length", str(len(payload))))
        head = f"POST {path} HTTP/1.1\r\n" + "".join(
            f"{name}: {value}\r\n" for name, value in request_headers
        ) + "\r\n"
        writer.write(head.encode() + payload)
        await writer.drain()
        raw = await asyncio.wait_for(reader.read(), timeout=3)
        writer.close()
        await writer.wait_closed()
    finally:
        server.close()
        await server.wait_closed()
    return _parse_http(raw)


class CountProvider:
    def __init__(self, *, native: bool = True) -> None:
        self.native = native
        self.requests: list[tuple[str, ProtocolRequest]] = []
        self.preflights: list[tuple[str, ProtocolRequest]] = []
        self.response = NativeResponse(
            200,
            b'{ "input_tokens": 17, "future": {"opaque": true} }\n',
            (
                ("content-type", "application/json; charset=utf-8"),
                ("request-id", "req_count"),
                ("x-ratelimit-limit", "10"),
                ("x-ratelimit-limit", "20"),
                ("set-cookie", "private=1"),
            ),
        )

    def model(self, name: str) -> Model | None:
        if name == "missing":
            return None
        protocols = ("anthropic-messages",) if self.native else ("openai-responses",)
        return Model(id=name, supported_generation_protocols=protocols)

    def supports_native_request(self, model: str, request: ProtocolRequest) -> bool:
        self.preflights.append((model, request))
        return self.native

    async def request_complete(self, model: str, request: ProtocolRequest) -> NativeResponse:
        self.requests.append((model, request))
        return self.response

    async def request_stream(self, model: str, request: ProtocolRequest) -> Any:
        raise AssertionError("counting must never stream")

    async def complete(self, model: str, context: Any, options: Any) -> Any:
        raise AssertionError("counting must never generate")

    def stream(self, model: str, context: Any, options: Any) -> Any:
        raise AssertionError("counting must never generate")


def _header_values(headers: list[tuple[str, str]], name: str) -> list[str]:
    return [value for key, value in headers if key == name]


async def test_count_tokens_route_is_native_non_streaming_and_lossless() -> None:
    provider = CountProvider()
    source = {
        "model": "model",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": True,
        "future": {"opaque": [1, True]},
    }

    status, headers, body = await _request(
        provider,
        source,
        path="/v1/messages/count_tokens?beta=true",
        headers=[
            ("anthropic-version", "2026-09-01"),
            ("anthropic-beta", "beta-one"),
            ("anthropic-beta", "beta-two"),
            ("Authorization", "Bearer local-secret"),
        ],
    )

    assert status == 200
    assert body == provider.response.body
    assert _header_values(headers, "content-type") == ["application/json; charset=utf-8"]
    assert _header_values(headers, "content-length") == [str(len(body))]
    assert _header_values(headers, "x-ratelimit-limit") == ["10", "20"]
    assert not _header_values(headers, "set-cookie")
    assert len(provider.preflights) == 1
    assert len(provider.requests) == 1
    model, request = provider.requests[0]
    assert model == "model"
    assert request.operation is NativeOperation.COUNT_TOKENS
    assert request.json() == source
    assert request.header("anthropic-beta") == "beta-one,beta-two"
    assert request.header("authorization") is None


async def test_count_tokens_known_unsupported_model_returns_marked_estimate() -> None:
    provider = CountProvider(native=False)
    source = {"model": "model", "messages": [{"role": "user", "content": "hello 世界"}]}

    status, headers, body = await _request(provider, source)

    assert status == 200
    assert json.loads(body) == {"input_tokens": _estimate_count_tokens(source)}
    assert _header_values(headers, "x-xdog-upstream-protocol") == ["best-effort"]
    assert len(provider.preflights) == 1
    assert not provider.requests


async def test_count_tokens_unknown_model_is_not_estimated() -> None:
    provider = CountProvider(native=False)

    status, headers, body = await _request(provider, {"model": "missing", "messages": []})

    assert status == 404
    assert json.loads(body)["error"]["type"] == "not_found_error"
    assert not _header_values(headers, "x-xdog-upstream-protocol")
    assert not provider.preflights
    assert not provider.requests


@pytest.mark.parametrize(
    "body",
    [
        b"not-json",
        {},
        {"model": "model", "messages": {}},
    ],
)
async def test_count_tokens_invalid_body_fails_before_provider(body: Any) -> None:
    provider = CountProvider()

    status, _, payload = await _request(provider, body)

    assert status == 400
    assert json.loads(payload)["error"]["type"] == "invalid_request_error"
    assert not provider.preflights
    assert not provider.requests


async def test_count_tokens_auth_and_chunked_fail_before_provider() -> None:
    provider = CountProvider()
    body = {"model": "model", "messages": []}

    unauthorized, _, _ = await _request(provider, body, api_key="expected")
    chunked, _, _ = await _request(provider, body, chunked=True)

    assert unauthorized == 401
    assert chunked == 400
    assert not provider.preflights
    assert not provider.requests


async def test_count_tokens_native_http_failure_never_estimates() -> None:
    provider = CountProvider()
    provider.response = NativeResponse(529, b"unused")

    async def request_complete(model: str, request: ProtocolRequest) -> NativeResponse:
        provider.requests.append((model, request))
        raise NativeHTTPError(NativeResponse(
            529,
            b'{"type":"error","error":{"type":"overloaded_error","message":"busy"}}',
            (("retry-after", "2"),),
        ))

    provider.request_complete = request_complete  # type: ignore[method-assign]

    status, headers, body = await _request(provider, {"model": "model", "messages": []})

    assert status == 529
    assert json.loads(body)["error"]["type"] == "overloaded_error"
    assert _header_values(headers, "retry-after") == ["2"]
    assert not _header_values(headers, "x-xdog-upstream-protocol")
    assert len(provider.requests) == 1


async def test_count_tokens_transport_failure_is_sanitized_and_never_estimates() -> None:
    provider = CountProvider()

    async def request_complete(model: str, request: ProtocolRequest) -> NativeResponse:
        provider.requests.append((model, request))
        raise OSError(errno.ECONNRESET, "sensitive upstream detail")

    provider.request_complete = request_complete  # type: ignore[method-assign]

    status, headers, body = await _request(provider, {"model": "model", "messages": []})

    assert status == 502
    assert b"sensitive upstream detail" not in body
    assert not _header_values(headers, "x-xdog-upstream-protocol")
    assert len(provider.requests) == 1


async def test_count_tokens_auth_failure_remains_an_error_without_estimate() -> None:
    provider = CountProvider()

    async def request_complete(model: str, request: ProtocolRequest) -> NativeResponse:
        provider.requests.append((model, request))
        raise AuthExpiredError("GitHub Copilot", "xdog-ai login copilot")

    provider.request_complete = request_complete  # type: ignore[method-assign]

    status, headers, body = await _request(provider, {"model": "model", "messages": []})

    assert status == 401
    assert json.loads(body)["error"]["type"] == "authentication_error"
    assert not _header_values(headers, "x-xdog-upstream-protocol")
    assert len(provider.requests) == 1


async def test_count_tokens_capability_race_does_not_fall_back_to_estimate() -> None:
    provider = CountProvider()

    async def request_complete(model: str, request: ProtocolRequest) -> NativeResponse:
        provider.requests.append((model, request))
        raise NotImplementedError("capability changed")

    provider.request_complete = request_complete  # type: ignore[method-assign]

    status, headers, _ = await _request(provider, {"model": "model", "messages": []})

    assert status == 500
    assert not _header_values(headers, "x-xdog-upstream-protocol")
    assert len(provider.requests) == 1
