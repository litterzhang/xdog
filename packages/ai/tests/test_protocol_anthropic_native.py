from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from xdog.ai.core import AuthResult
from xdog.ai.native import NativeHTTPError, NativeOperation, NativeSSEEvent, ProtocolRequest
from xdog.ai.protocols.anthropic_messages import AnthropicMessagesProtocol
from xdog.ai.types import Model

_FULL_BODY: dict[str, Any] = {
    "model": "client/claude-test",
    "messages": [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "hello", "cache_control": {"type": "ephemeral"}},
                {
                    "type": "future_encrypted_block",
                    "opaque": {"ciphertext": "AAECAw==", "revision": 7},
                },
            ],
        },
    ],
    "max_tokens": 0,
    "cache_control": {"type": "ephemeral"},
    "container": {"id": "container_123", "skills": [{"type": "anthropic", "skill_id": "pdfs"}]},
    "context_management": {
        "edits": [
            {
                "type": "clear_tool_uses_20250919",
                "trigger": {"type": "input_tokens", "value": 1000},
            },
        ],
    },
    "diagnostics": {"enabled": True},
    "fallback_credit_token": "credit-token",
    "inference_geo": "us",
    "mcp_servers": [{"type": "url", "name": "docs", "url": "https://example.invalid/mcp"}],
    "metadata": {"user_id": "user-123"},
    "output_config": {
        "effort": "high",
        "format": {"type": "json_schema", "schema": {"type": "object"}},
    },
    "service_tier": "auto",
    "speed": "fast",
    "stop_sequences": ["DONE"],
    "stream": False,
    "system": [{"type": "text", "text": "system", "cache_control": {"type": "ephemeral"}}],
    "temperature": 0.2,
    "thinking": {"type": "adaptive", "display": "summarized"},
    "tool_choice": {"type": "auto", "disable_parallel_tool_use": False},
    "tools": [
        {"name": "custom", "description": "custom", "input_schema": {"type": "object"}},
        {"type": "web_search_20260209", "name": "web_search", "max_uses": 3},
    ],
    "top_k": 8,
    "top_p": 0.9,
    "future_top_level": {"preserve": [1, "two", {"three": True}]},
}


def _client(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    client_type = httpx.AsyncClient

    def factory(**kwargs: Any) -> httpx.AsyncClient:
        return client_type(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)


async def test_native_complete_preserves_full_body_and_safe_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def handle(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        captured["headers"] = dict(request.headers)
        return httpx.Response(
            200,
            headers={"request-id": "req_upstream", "x-ratelimit-remaining": "4"},
            json={
                "id": "msg_upstream",
                "type": "message",
                "role": "assistant",
                "model": "wire-claude-test",
                "content": [{"type": "future_result", "encrypted": "opaque-value"}],
                "stop_reason": "pause_turn",
                "usage": {"input_tokens": 1, "output_tokens": 2, "server_tool_use": {"web_search_requests": 1}},
                "future_response_field": {"kept": True},
            },
        )

    _client(monkeypatch, handle)
    request = ProtocolRequest.from_json(
        "anthropic-messages",
        _FULL_BODY,
        headers={
            "anthropic-version": "2026-09-01",
            "anthropic-beta": "tools-1,tools-2",
            "anthropic-workspace-id": "ws_client",
            "anthropic-user-profile-id": "profile_client",
            "authorization": "Bearer local-proxy-secret",
            "x-api-key": "local-proxy-secret",
        },
    )
    model = Model(
        id="wire-claude-test",
        base_url="https://upstream.invalid",
        headers={
            "anthropic-beta": "provider-beta,tools-1",
            "Authorization": "Bearer stale-model-credential",
        },
    )

    result = await AnthropicMessagesProtocol().request_complete(
        model, request, AuthResult(api_key="vendor-secret", headers={"X-Copilot": "yes"}),
    )

    assert captured["body"] == {**_FULL_BODY, "model": "wire-claude-test", "stream": False}
    assert captured["headers"]["authorization"] == "Bearer vendor-secret"
    assert captured["headers"].get("x-api-key") is None
    assert captured["headers"]["anthropic-version"] == "2026-09-01"
    assert captured["headers"]["anthropic-beta"] == "provider-beta,tools-1,tools-2"
    assert captured["headers"]["anthropic-workspace-id"] == "ws_client"
    assert captured["headers"]["anthropic-user-profile-id"] == "profile_client"
    assert result.status == 200
    assert result.header("request-id") == "req_upstream"
    response_body = result.json()
    assert response_body["model"] == "client/claude-test"
    assert response_body["content"] == [{"type": "future_result", "encrypted": "opaque-value"}]
    assert response_body["future_response_field"] == {"kept": True}


async def test_native_count_tokens_uses_sibling_endpoint_without_generation_rewrites(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    raw_response = (
        b'{ "input_tokens": 17, "context_management": '
        b'{"original_input_tokens": 23}, "future": {"opaque": true} }\n'
    )
    source = {
        "model": "client/claude-test",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": {"future": "opaque"},
        "future": {"model": "nested-do-not-rewrite"},
    }

    def handle(request: httpx.Request) -> httpx.Response:
        captured["url"] = request.url
        captured["body"] = json.loads(request.content)
        captured["headers"] = dict(request.headers)
        return httpx.Response(
            202,
            headers={"request-id": "req_count", "anthropic-ratelimit-requests-remaining": "4"},
            content=raw_response,
        )

    _client(monkeypatch, handle)
    request = ProtocolRequest.from_json(
        "anthropic-messages",
        source,
        headers={"anthropic-beta": "future-beta"},
        operation=NativeOperation.COUNT_TOKENS,
    )

    result = await AnthropicMessagesProtocol().request_complete(
        Model(id="wire-claude-test", base_url="https://upstream.invalid"),
        request,
        AuthResult(api_key="vendor-secret"),
    )

    assert captured["url"].path == "/v1/messages/count_tokens"
    assert captured["body"] == {**source, "model": "wire-claude-test"}
    assert "token-counting-2024-11-01" not in captured["headers"].get("anthropic-beta", "")
    assert result.status == 202
    assert result.body == raw_response
    assert result.header("request-id") == "req_count"
    assert result.header("anthropic-ratelimit-requests-remaining") == "4"


async def test_native_count_tokens_preserves_upstream_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error_body = b'{"type":"error","error":{"type":"overloaded_error","message":"busy"}}'

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/messages/count_tokens"
        return httpx.Response(
            529,
            headers={"request-id": "req_529", "retry-after": "2"},
            content=error_body,
        )

    _client(monkeypatch, handle)
    request = ProtocolRequest.from_json(
        "anthropic-messages",
        {"model": "client", "messages": []},
        operation=NativeOperation.COUNT_TOKENS,
    )

    with pytest.raises(NativeHTTPError) as caught:
        await AnthropicMessagesProtocol().request_complete(
            Model(id="wire", base_url="https://upstream.invalid"),
            request,
            AuthResult(api_key="vendor-secret"),
        )

    assert caught.value.response.status == 529
    assert caught.value.response.body == error_body
    assert caught.value.response.header("request-id") == "req_529"
    assert caught.value.response.header("retry-after") == "2"


async def test_native_count_tokens_rejects_stream_before_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_client(**kwargs: Any) -> httpx.AsyncClient:
        raise AssertionError("HTTP client must not be constructed")

    monkeypatch.setattr(httpx, "AsyncClient", fail_client)
    request = ProtocolRequest.from_json(
        "anthropic-messages",
        {"model": "client", "messages": []},
        operation=NativeOperation.COUNT_TOKENS,
    )

    with pytest.raises(ValueError, match="cannot be streamed"):
        await AnthropicMessagesProtocol().request_stream(
            Model(id="wire", base_url="https://upstream.invalid"),
            request,
            AuthResult(api_key="vendor-secret"),
        )


async def test_native_stream_preserves_unknown_events_and_restores_client_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frames = [
        ("message_start", {
            "type": "message_start",
            "message": {"id": "msg_1", "model": "wire-claude-test", "content": []},
        }),
        ("ping", {"type": "ping"}),
        ("future_event", {
            "type": "future_event",
            "index": 7,
            "opaque": {"encrypted": "do-not-touch"},
        }),
        ("message_stop", {"type": "message_stop"}),
    ]

    def handle(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content) == {**_FULL_BODY, "model": "wire-claude-test", "stream": True}
        content = "".join(
            f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"
            for event, data in frames
        ).encode()
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream", "request-id": "req_stream"},
            content=content,
        )

    _client(monkeypatch, handle)
    request = ProtocolRequest.from_json("anthropic-messages", _FULL_BODY)
    stream = await AnthropicMessagesProtocol().request_stream(
        Model(id="wire-claude-test", base_url="https://upstream.invalid"),
        request,
        AuthResult(api_key="vendor-secret"),
    )
    try:
        events = [event async for event in stream]
    finally:
        await stream.aclose()

    assert stream.start.status == 200
    assert stream.start.header("request-id") == "req_stream"
    assert [event.event for event in events] == [event for event, _ in frames]
    assert all(isinstance(event, NativeSSEEvent) for event in events)
    payloads = [event.json() for event in events]
    assert payloads[0]["message"]["model"] == "client/claude-test"
    assert payloads[1:] == [data for _, data in frames[1:]]


async def test_native_http_error_keeps_status_body_and_response_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from xdog.ai.native import NativeHTTPError

    error_body = {
        "type": "error",
        "error": {"type": "rate_limit_error", "message": "slow down", "details": {"limit": 4}},
        "request_id": "req_429",
    }

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            headers={"request-id": "req_429", "retry-after": "9"},
            json=error_body,
        )

    _client(monkeypatch, handle)
    request = ProtocolRequest.from_json("anthropic-messages", _FULL_BODY)

    with pytest.raises(NativeHTTPError) as caught:
        await AnthropicMessagesProtocol().request_complete(
            Model(id="wire", base_url="https://upstream.invalid"),
            request,
            AuthResult(api_key="vendor-secret"),
        )

    assert caught.value.response.status == 429
    assert caught.value.response.json() == error_body
    assert caught.value.response.header("request-id") == "req_429"
    assert caught.value.response.header("retry-after") == "9"
