from __future__ import annotations

import asyncio
import errno
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from xdog.ai.core import AuthResult
from xdog.ai.native import (
    NativeEventStream,
    NativeHTTPError,
    NativeResponse,
    NativeResponseStart,
    NativeSSEEvent,
    ProtocolRequest,
)
from xdog.ai.protocols.anthropic_messages import AnthropicMessagesProtocol
from xdog.ai.providers.copilot import CopilotProvider
from xdog.ai.proxy import _handle_connection
from xdog.ai.types import AssistantMessage, Context, Model, StreamOptions, TextContent, UserMessage


def _parse_http(raw: bytes) -> tuple[int, dict[str, str], bytes]:
    head, separator, body = raw.partition(b"\r\n\r\n")
    assert separator
    lines = head.decode().split("\r\n")
    header_lines = [line for line in lines[1:] if ":" in line]
    assert sum(line.lower().startswith("content-type:") for line in header_lines) == 1
    headers = {
        name.lower(): value.strip()
        for line in header_lines
        for name, value in [line.split(":", 1)]
    }
    return int(lines[0].split()[1]), headers, body


async def _request(
    provider: Any,
    body: Any,
    *,
    headers: dict[str, str] | None = None,
    api_key: str = "",
) -> tuple[int, dict[str, str], bytes]:
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await _handle_connection(reader, writer, provider, api_key=api_key)

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    try:
        host, port = server.sockets[0].getsockname()[:2]
        reader, writer = await asyncio.open_connection(host, port)
        payload = body if isinstance(body, bytes) else json.dumps(body).encode()
        request_headers = {
            "Host": "localhost",
            "Content-Type": "application/json",
            **(headers or {}),
            "Content-Length": str(len(payload)),
        }
        head = "POST /v1/messages HTTP/1.1\r\n" + "".join(
            f"{name}: {value}\r\n" for name, value in request_headers.items()
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


class NativeProvider:
    def __init__(self) -> None:
        self.requests: list[tuple[str, ProtocolRequest, str]] = []
        self.response = NativeResponse(
            status=200,
            headers=(("content-type", "application/json"), ("request-id", "req_native")),
            body=json.dumps({
                "id": "msg_native",
                "type": "message",
                "role": "assistant",
                "model": "model",
                "content": [{"type": "future_result", "opaque": "value"}],
                "stop_reason": "pause_turn",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }).encode(),
        )
        self.stream_events = (
            NativeSSEEvent("message_start", b'{"type":"message_start","message":{"model":"model"}}'),
            NativeSSEEvent("ping", b'{"type":"ping"}'),
            NativeSSEEvent("future_event", b'{"type":"future_event","opaque":"kept"}'),
            NativeSSEEvent("message_stop", b'{"type":"message_stop"}'),
        )

    def model(self, name: str) -> object:
        return object()

    async def request_complete(self, model: str, request: ProtocolRequest) -> NativeResponse:
        self.requests.append((model, request, "complete"))
        return self.response

    async def request_stream(self, model: str, request: ProtocolRequest) -> NativeEventStream:
        self.requests.append((model, request, "stream"))

        async def events() -> AsyncIterator[NativeSSEEvent]:
            for event in self.stream_events:
                yield event

        async def close() -> None:
            return None

        return NativeEventStream(
            NativeResponseStart(
                200,
                (("content-type", "text/event-stream"), ("request-id", "req_stream")),
            ),
            events(),
            close,
        )


_FULL_BODY: dict[str, Any] = {
    "model": "model",
    "messages": [{"role": "user", "content": [{"type": "future_block", "opaque": {"x": 1}}]}],
    "max_tokens": 0,
    "cache_control": {"type": "ephemeral"},
    "container": "container_1",
    "context_management": {"edits": []},
    "diagnostics": {"enabled": True},
    "fallback_credit_token": "credit",
    "inference_geo": "us",
    "mcp_servers": [],
    "metadata": {"user_id": "u"},
    "output_config": {"effort": "max", "format": {"type": "json_schema", "schema": {"type": "object"}}},
    "service_tier": "auto",
    "speed": "fast",
    "stop_sequences": ["END"],
    "system": [{"type": "text", "text": "system", "cache_control": {"type": "ephemeral"}}],
    "temperature": 0.1,
    "thinking": {"type": "adaptive"},
    "tool_choice": {"type": "any"},
    "tools": [{"type": "web_search_20260209", "name": "web_search"}],
    "top_k": 3,
    "top_p": 0.7,
    "future_parameter": {"opaque": True},
}


async def test_repeated_anthropic_beta_headers_are_combined() -> None:
    provider = NativeProvider()
    payload = json.dumps(_FULL_BODY).encode()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await _handle_connection(reader, writer, provider)

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    try:
        host, port = server.sockets[0].getsockname()[:2]
        reader, writer = await asyncio.open_connection(host, port)
        writer.write(
            b"POST /v1/messages HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Content-Type: application/json\r\n"
            b"anthropic-beta: beta-one\r\n"
            b"anthropic-beta: beta-two\r\n"
            + f"Content-Length: {len(payload)}\r\n\r\n".encode()
            + payload
        )
        await writer.drain()
        raw = await asyncio.wait_for(reader.read(), timeout=3)
        writer.close()
        await writer.wait_closed()
    finally:
        server.close()
        await server.wait_closed()

    status, _, _ = _parse_http(raw)
    assert status == 200
    assert provider.requests[0][1].header("anthropic-beta") == "beta-one,beta-two"


async def test_native_non_streaming_proxy_preserves_body_headers_and_response() -> None:
    provider = NativeProvider()
    status, headers, body = await _request(provider, _FULL_BODY, headers={
        "anthropic-version": "2026-09-01",
        "anthropic-beta": "beta-one,beta-two",
        "anthropic-workspace-id": "ws_1",
        "anthropic-user-profile-id": "profile_1",
        "Authorization": "Bearer proxy-secret",
        "x-api-key": "proxy-secret",
    })

    assert status == 200
    assert headers["request-id"] == "req_native"
    assert json.loads(body) == provider.response.json()
    assert len(provider.requests) == 1
    model, request, mode = provider.requests[0]
    assert (model, mode) == ("model", "complete")
    assert request.json() == _FULL_BODY
    assert dict(request.headers) == {
        "anthropic-version": "2026-09-01",
        "anthropic-beta": "beta-one,beta-two",
        "anthropic-workspace-id": "ws_1",
        "anthropic-user-profile-id": "profile_1",
    }


async def test_native_stream_proxy_preserves_unknown_sse_events() -> None:
    provider = NativeProvider()
    status, headers, body = await _request(provider, {**_FULL_BODY, "stream": True})

    assert status == 200
    assert headers["content-type"] == "text/event-stream"
    assert headers["request-id"] == "req_stream"
    expected = b"".join(event.encode() for event in provider.stream_events)
    assert body == expected


@pytest.mark.parametrize("streaming", [False, True])
async def test_loopback_proxy_copilot_protocol_preserves_wire_contract(
    monkeypatch: pytest.MonkeyPatch,
    streaming: bool,
) -> None:
    captured: dict[str, Any] = {}

    def upstream(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        captured["headers"] = dict(request.headers)
        if streaming:
            frames = (
                NativeSSEEvent(
                    "message_start",
                    json.dumps({
                        "type": "message_start",
                        "message": {
                            "id": "msg_loopback",
                            "model": "claude-wire",
                            "content": [],
                        },
                    }, separators=(",", ":")).encode(),
                ),
                NativeSSEEvent(
                    "future_event",
                    b'{"type":"future_event","opaque":{"ciphertext":"AAECAw=="}}',
                ),
                NativeSSEEvent("message_stop", b'{"type":"message_stop"}'),
            )
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream", "request-id": "req_loopback"},
                content=b"".join(frame.encode() for frame in frames),
            )
        return httpx.Response(
            200,
            headers={"content-type": "application/json", "request-id": "req_loopback"},
            json={
                "id": "msg_loopback",
                "type": "message",
                "role": "assistant",
                "model": "claude-wire",
                "content": [{"type": "future_result", "encrypted": "opaque"}],
                "future_response_field": {"kept": True},
            },
        )

    client_type = httpx.AsyncClient

    def client(**kwargs: Any) -> httpx.AsyncClient:
        return client_type(transport=httpx.MockTransport(upstream), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    provider = CopilotProvider()
    provider._model_cache["copilot/client-model"] = Model(
        id="copilot/client-model",
        api="anthropic-messages",
        provider="copilot",
        base_url="https://upstream.invalid",
        headers={"anthropic-beta": "provider-beta"},
        supported_protocols=("anthropic-messages",),
        preferred_protocol="anthropic-messages",
    )
    provider._protocols["anthropic-messages"] = AnthropicMessagesProtocol()

    async def resolve_auth(model: Model, context: Context | None = None) -> AuthResult:
        return AuthResult(api_key="vendor-secret", headers={"X-Copilot": "yes"})

    monkeypatch.setattr(provider._get_vendor(), "resolve_auth", resolve_auth)
    request_body = {**_FULL_BODY, "model": "client-model", "stream": streaming}

    status, headers, body = await _request(
        provider,
        request_body,
        headers={
            "anthropic-version": "2026-09-01",
            "anthropic-beta": "client-beta",
            "Authorization": "Bearer local-proxy-secret",
        },
    )

    assert status == 200
    assert headers["request-id"] == "req_loopback"
    assert captured["body"] == {**request_body, "model": "client-model"}
    assert captured["headers"]["authorization"] == "Bearer vendor-secret"
    assert captured["headers"]["anthropic-version"] == "2026-09-01"
    assert captured["headers"]["anthropic-beta"] == "provider-beta,client-beta"
    if streaming:
        assert headers["content-type"] == "text/event-stream"
        assert b'"model":"client-model"' in body
        assert b'"ciphertext":"AAECAw=="' in body
    else:
        payload = json.loads(body)
        assert payload["model"] == "client-model"
        assert payload["content"] == [{"type": "future_result", "encrypted": "opaque"}]
        assert payload["future_response_field"] == {"kept": True}


@pytest.mark.parametrize("fallbacks", ["default", [{"model": "fallback-model"}], None])
async def test_native_proxy_accepts_current_fallback_shapes(fallbacks: Any) -> None:
    provider = NativeProvider()
    request_body = {
        "model": "model",
        "messages": [{"role": "user", "content": "hello"}],
        "max_tokens": 1,
        "fallbacks": fallbacks,
    }

    status, _, _ = await _request(provider, request_body)

    assert status == 200
    assert provider.requests[0][1].json()["fallbacks"] == fallbacks


@pytest.mark.parametrize("credit", [
    "opaque-credit",
    {"token": "opaque-credit", "mode": "best_effort"},
    None,
])
async def test_native_proxy_accepts_current_fallback_credit_shapes(credit: Any) -> None:
    provider = NativeProvider()
    request_body = {
        "model": "model",
        "messages": [{"role": "user", "content": "hello"}],
        "max_tokens": 1,
        "fallback_credit_token": credit,
    }

    status, _, _ = await _request(provider, request_body)

    assert status == 200
    assert provider.requests[0][1].json()["fallback_credit_token"] == credit


async def test_native_proxy_accepts_legacy_output_format() -> None:
    provider = NativeProvider()
    output_format = {
        "type": "json_schema",
        "schema": {"type": "object", "properties": {"answer": {"type": "string"}}},
    }
    request_body = {
        "model": "model",
        "messages": [{"role": "user", "content": "hello"}],
        "max_tokens": 1,
        "output_format": output_format,
    }

    status, _, _ = await _request(provider, request_body)

    assert status == 200
    assert provider.requests[0][1].json()["output_format"] == output_format


async def test_native_proxy_accepts_system_message_variant() -> None:
    provider = NativeProvider()
    request_body = {
        "model": "model",
        "messages": [
            {"role": "system", "content": "system", "clear_at": "next_user_message"},
            {"role": "user", "content": "hello"},
        ],
        "max_tokens": 1,
    }

    status, _, _ = await _request(provider, request_body)

    assert status == 200
    assert provider.requests[0][1].json()["messages"] == request_body["messages"]


async def test_native_stream_stops_after_terminal_error_event() -> None:
    provider = NativeProvider()
    provider.stream_events = (
        NativeSSEEvent("ping", b'{"type":"ping"}'),
        NativeSSEEvent(
            "error",
            b'{"type":"error","error":{"type":"overloaded_error","message":"busy"}}',
        ),
        NativeSSEEvent("message_stop", b'{"type":"message_stop"}'),
    )

    status, _, body = await _request(provider, {**_FULL_BODY, "stream": True})

    assert status == 200
    assert body == b"".join(event.encode() for event in provider.stream_events[:2])


async def test_native_stream_transport_failure_emits_terminal_sse_error() -> None:
    class FailingStreamProvider(NativeProvider):
        async def request_stream(self, model: str, request: ProtocolRequest) -> NativeEventStream:
            async def events() -> AsyncIterator[NativeSSEEvent]:
                yield NativeSSEEvent("ping", b'{"type":"ping"}')
                raise RuntimeError("sensitive transport detail")

            async def close() -> None:
                return None

            return NativeEventStream(
                NativeResponseStart(200, (("content-type", "text/event-stream"),)),
                events(),
                close,
            )

    status, _, body = await _request(FailingStreamProvider(), {**_FULL_BODY, "stream": True})

    assert status == 200
    assert b"event: ping" in body
    assert b"event: error" in body
    assert b"Upstream connection failed; retry later" in body
    assert b"sensitive transport detail" not in body
    assert b"HTTP/1.1" not in body


async def test_native_stream_treats_json_error_type_as_terminal() -> None:
    provider = NativeProvider()
    provider.stream_events = (
        NativeSSEEvent(
            "message",
            b'{"type":"error","error":{"type":"overloaded_error","message":"busy"}}',
        ),
        NativeSSEEvent("message_stop", b'{"type":"message_stop"}'),
    )

    status, _, body = await _request(provider, {**_FULL_BODY, "stream": True})

    assert status == 200
    assert body == provider.stream_events[0].encode()


@pytest.mark.parametrize("mode", ["complete", "stream"])
async def test_native_transport_failure_before_response_returns_502(mode: str) -> None:
    class TransportErrorProvider(NativeProvider):
        async def request_complete(self, model: str, request: ProtocolRequest) -> NativeResponse:
            raise OSError(errno.ECONNRESET, "sensitive connection detail")

        async def request_stream(self, model: str, request: ProtocolRequest) -> NativeEventStream:
            raise OSError(errno.ECONNRESET, "sensitive connection detail")

    status, headers, body = await _request(
        TransportErrorProvider(),
        {**_FULL_BODY, "stream": mode == "stream"},
    )

    assert status == 502
    assert headers["content-type"] == "application/json"
    assert json.loads(body) == {
        "type": "error",
        "error": {
            "type": "api_error",
            "message": "Upstream connection failed; retry later",
        },
    }
    assert b"sensitive connection detail" not in body


async def test_native_proxy_preserves_upstream_error() -> None:
    class ErrorProvider(NativeProvider):
        async def request_complete(self, model: str, request: ProtocolRequest) -> NativeResponse:
            raise NativeHTTPError(NativeResponse(
                429,
                json.dumps({
                    "type": "error",
                    "error": {"type": "rate_limit_error", "message": "slow"},
                    "request_id": "req_429",
                }).encode(),
                (("request-id", "req_429"), ("retry-after", "5")),
            ))

    status, headers, body = await _request(ErrorProvider(), _FULL_BODY)
    assert status == 429
    assert headers["request-id"] == "req_429"
    assert headers["retry-after"] == "5"
    assert json.loads(body)["error"] == {"type": "rate_limit_error", "message": "slow"}


@pytest.mark.parametrize("invalid,param", [
    ({}, "model"),
    ({"model": "", "messages": [], "max_tokens": 1}, "model"),
    ({"model": "m", "messages": {}, "max_tokens": 1}, "messages"),
    ({"model": "m", "messages": [], "max_tokens": -1}, "max_tokens"),
    ({"model": "m", "messages": [], "max_tokens": True}, "max_tokens"),
    ({"model": "m", "messages": [], "max_tokens": 1, "stream": "yes"}, "stream"),
    ({"model": "m", "messages": [], "max_tokens": 1, "temperature": float("inf")}, "temperature"),
    ({"model": "m", "messages": [], "max_tokens": 1, "output_format": {"type": "json_schema"},
      "output_config": {"format": {"type": "json_schema"}}}, "output_format"),
    ({"model": "m", "messages": [], "max_tokens": 1, "fallbacks": "other"}, "fallbacks"),
    ({"model": "m", "messages": [], "max_tokens": 1, "fallbacks": ["model"]}, "fallbacks[0]"),
    ({"model": "m", "messages": [], "max_tokens": 1, "fallback_credit_token": 42},
     "fallback_credit_token"),
    ({"model": "m", "messages": [], "max_tokens": 1, "fallback_credit_token": {}},
     "fallback_credit_token.token"),
])
async def test_invalid_anthropic_request_returns_400_without_provider_call(
    invalid: dict[str, Any], param: str,
) -> None:
    provider = NativeProvider()
    status, _, body = await _request(provider, invalid)
    assert status == 400
    error = json.loads(body)["error"]
    assert error["type"] == "invalid_request_error"
    assert param in error["message"]
    assert not provider.requests


async def test_best_effort_reports_nested_and_target_specific_losses() -> None:
    class OpenAIResponsesProvider:
        def __init__(self) -> None:
            self.calls: list[tuple[Context, StreamOptions]] = []

        def model(self, name: str) -> Model:
            return Model(
                id=name,
                api="openai-responses",
                supported_protocols=("openai-responses",),
            )

        async def request_complete(self, model: str, request: ProtocolRequest) -> NativeResponse:
            raise AssertionError("native Anthropic request should not be attempted")

        async def request_stream(self, model: str, request: ProtocolRequest) -> NativeEventStream:
            raise AssertionError("native Anthropic request should not be attempted")

        def stream(self, model: str, context: Context, options: StreamOptions) -> Any:
            raise AssertionError("unexpected stream")

        async def complete(self, model: str, context: Context, options: StreamOptions) -> AssistantMessage:
            self.calls.append((context, options))
            return AssistantMessage(content=(TextContent(text="fallback"),))

    provider = OpenAIResponsesProvider()
    status, headers, _ = await _request(provider, {
        "model": "openai-only",
        "messages": [
            {"role": "system", "content": "turn system", "clear_at": "next_user_message"},
            {"role": "user", "content": [
                {"type": "image", "source": {"type": "url", "url": "https://example.invalid/image.png"}},
                {"type": "document", "source": {"type": "text", "data": "document"}},
            ]},
        ],
        "max_tokens": 12,
        "stop_sequences": ["END"],
        "service_tier": "standard_only",
        "thinking": {"type": "adaptive", "display": "omitted"},
        "output_config": {"effort": "high", "task_budget": {"type": "tokens", "total": 4096}},
        "tools": [
            {"type": "web_search_20260209", "name": "web_search"},
            {"type": "custom", "name": "weather", "input_schema": {"type": "object"}, "strict": True},
        ],
    })

    assert status == 200
    ignored = set(headers["x-xdog-ignored-parameters"].split(","))
    assert {
        "messages.role.system",
        "messages.clear_at",
        "messages.content.image.source.url",
        "messages.content.document",
        "stop_sequences",
        "service_tier",
        "thinking.display",
        "output_config.task_budget",
        "tools.web_search_20260209",
        "tools.custom.strict",
    } <= ignored
    context, options = provider.calls[0]
    assert context.system_prompt == "turn system"
    assert context.tools is not None and context.tools[0].name == "weather"
    assert options.web_search is True
    assert options.service_tier == "default"


async def test_openai_only_model_uses_best_effort_without_native_attempt() -> None:
    class OpenAIOnlyProvider:
        def __init__(self) -> None:
            self.native_calls = 0
            self.fallback_calls: list[tuple[str, Context, StreamOptions]] = []

        def model(self, name: str) -> Model:
            return Model(
                id=name,
                api="openai-responses",
                supported_protocols=("openai-responses",),
            )

        async def request_complete(self, model: str, request: ProtocolRequest) -> NativeResponse:
            self.native_calls += 1
            raise AssertionError("native Anthropic request should not be attempted")

        async def request_stream(self, model: str, request: ProtocolRequest) -> NativeEventStream:
            self.native_calls += 1
            raise AssertionError("native Anthropic request should not be attempted")

        def stream(self, model: str, context: Context, options: StreamOptions) -> Any:
            raise AssertionError("unexpected stream")

        async def complete(self, model: str, context: Context, options: StreamOptions) -> AssistantMessage:
            self.fallback_calls.append((model, context, options))
            return AssistantMessage(content=(TextContent(text="fallback"),))

    provider = OpenAIOnlyProvider()
    status, headers, _ = await _request(provider, {
        "model": "openai-only",
        "messages": [{"role": "user", "content": "hello"}],
        "max_tokens": 12,
    })

    assert status == 200
    assert headers["x-xdog-upstream-protocol"] == "best-effort"
    assert provider.native_calls == 0
    assert len(provider.fallback_calls) == 1


async def test_provider_without_native_api_uses_best_effort_projection() -> None:
    class LegacyProvider:
        def __init__(self) -> None:
            self.calls: list[Any] = []

        def stream(self, model: str, context: Context, options: StreamOptions) -> Any:
            raise AssertionError("unexpected stream")

        async def complete(self, model: str, context: Context, options: StreamOptions) -> AssistantMessage:
            self.calls.append((model, context, options))
            return AssistantMessage(content=(TextContent(text="fallback"),))

    provider = LegacyProvider()
    request_body = {
        "model": "openai-only",
        "messages": [{"role": "user", "content": "hello"}],
        "max_tokens": 12,
        "top_p": 0.7,
        "stop_sequences": ["END"],
        "tool_choice": {"type": "tool", "name": "weather", "disable_parallel_tool_use": True},
        "metadata": {"user_id": "user-1"},
        "output_config": {
            "format": {
                "type": "json_schema",
                "schema": {"type": "object"},
            },
        },
        "service_tier": "auto",
        "future_anthropic_only": {"ignored": True},
    }
    status, headers, body = await _request(provider, request_body)

    assert status == 200
    assert headers["x-xdog-upstream-protocol"] == "best-effort"
    assert "future_anthropic_only" in headers["x-xdog-ignored-parameters"]
    assert json.loads(body)["content"] == [{"type": "text", "text": "fallback"}]
    model, context, options = provider.calls[0]
    assert model == "openai-only"
    assert context.messages == (UserMessage(content="hello"),)
    assert options.max_tokens == 12
    assert options.top_p == 0.7
    assert options.stop_sequences == ("END",)
    assert options.tool_choice is not None
    assert (options.tool_choice.type, options.tool_choice.name) == ("tool", "weather")
    assert options.parallel_tool_calls is False
    assert options.metadata == (("user_id", "user-1"),)
    assert options.response_format is not None
    assert options.response_format.schema() == {"type": "object"}
    assert options.service_tier == "auto"
