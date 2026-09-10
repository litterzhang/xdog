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
from xdog.ai.protocols.openai_completions import OpenAICompletionsProtocol
from xdog.ai.protocols.openai_responses import OpenAIResponsesProtocol
from xdog.ai.providers.copilot import CopilotProvider
from xdog.ai.proxy import _handle_connection
from xdog.ai.types import AssistantMessage, Context, Model, TextContent


def _parse_http(raw: bytes) -> tuple[int, dict[str, str], bytes]:
    head, separator, body = raw.partition(b"\r\n\r\n")
    assert separator
    lines = head.decode().split("\r\n")
    return (
        int(lines[0].split()[1]),
        {
            name.lower(): value.strip()
            for line in lines[1:]
            if ":" in line
            for name, value in [line.split(":", 1)]
        },
        body,
    )


async def _request(
    provider: Any,
    path: str,
    body: Any,
    *,
    headers: dict[str, str] | None = None,
    chunked: bool = False,
) -> tuple[int, dict[str, str], bytes]:
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await _handle_connection(reader, writer, provider)

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    try:
        host, port = server.sockets[0].getsockname()[:2]
        reader, writer = await asyncio.open_connection(host, port)
        payload = body if isinstance(body, bytes) else json.dumps(body).encode()
        request_headers = {
            "Host": "localhost",
            "Content-Type": "application/json",
            **(headers or {}),
        }
        if chunked:
            request_headers["Transfer-Encoding"] = "chunked"
            framed_body = f"{len(payload):x}\r\n".encode() + payload + b"\r\n0\r\n\r\n"
        else:
            request_headers["Content-Length"] = str(len(payload))
            framed_body = payload
        head = f"POST {path}?test=1 HTTP/1.1\r\n" + "".join(
            f"{name}: {value}\r\n" for name, value in request_headers.items()
        ) + "\r\n"
        writer.write(head.encode() + framed_body)
        await writer.drain()
        raw = await asyncio.wait_for(reader.read(), timeout=3)
        writer.close()
        await writer.wait_closed()
    finally:
        server.close()
        await server.wait_closed()
    return _parse_http(raw)


class NativeProvider:
    def __init__(self, protocols: tuple[str, ...]) -> None:
        self.protocols = protocols
        self.requests: list[tuple[str, ProtocolRequest, str]] = []
        self.normalized_calls: list[str] = []
        self.response = NativeResponse(
            200,
            json.dumps({
                "id": "native_1",
                "model": "client-model",
                "future": {"opaque": True},
            }).encode(),
            (
                ("content-type", "application/json"),
                ("x-request-id", "req_native"),
                ("retry-after", "2"),
            ),
        )
        self.events = (
            NativeSSEEvent(
                "response.created" if "openai-responses" in protocols else None,
                b'{"type":"response.created","response":{"model":"client-model"}}'
                if "openai-responses" in protocols
                else b'{"id":"chat_1","model":"client-model","choices":[]}',
            ),
            NativeSSEEvent(None, b"[DONE]") if "openai-completions" in protocols else NativeSSEEvent(
                "response.completed",
                b'{"type":"response.completed","response":{"model":"client-model"}}',
            ),
        )

    def model(self, name: str) -> Model:
        return Model(
            id=name,
            api=self.protocols[0] if self.protocols else "anthropic-messages",
            supported_generation_protocols=self.protocols,
        )

    async def request_complete(self, model: str, request: ProtocolRequest) -> NativeResponse:
        self.requests.append((model, request, "complete"))
        return self.response

    async def request_stream(self, model: str, request: ProtocolRequest) -> NativeEventStream:
        self.requests.append((model, request, "stream"))

        async def events() -> AsyncIterator[NativeSSEEvent]:
            for event in self.events:
                yield event

        async def close() -> None:
            return None

        return NativeEventStream(
            NativeResponseStart(200, (("content-type", "text/event-stream"), ("x-request-id", "req_stream"))),
            events(),
            close,
        )

    def stream(self, model: str, context: Any, options: Any) -> Any:
        self.normalized_calls.append("stream")
        raise AssertionError("normalized stream must not be called")

    async def complete(self, model: str, context: Any, options: Any) -> AssistantMessage:
        self.normalized_calls.append("complete")
        return AssistantMessage(content=(TextContent(text="fallback"),))


@pytest.mark.parametrize(
    ("path", "protocol"),
    [("/v1/responses", "openai-responses"), ("/v1/chat/completions", "openai-completions")],
)
async def test_native_complete_preserves_future_fields_headers_and_response(
    path: str,
    protocol: str,
) -> None:
    provider = NativeProvider((protocol,))
    body = {
        "model": "client-model",
        "input" if protocol == "openai-responses" else "messages": [] if protocol == "openai-completions" else "hello",
        "stream": False,
        "future_top_level": {"unknown": [None, {"nested": True}]},
    }

    status, headers, payload = await _request(provider, path, body, headers={
        "OpenAI-Organization": "org_client",
        "OpenAI-Project": "project_client",
        "Idempotency-Key": "idem_1",
        "Authorization": "Bearer local-secret",
        "x-api-key": "local-secret",
    })

    assert status == 200
    assert headers["x-request-id"] == "req_native"
    assert headers["retry-after"] == "2"
    assert json.loads(payload) == provider.response.json()
    assert provider.normalized_calls == []
    model, request, mode = provider.requests[0]
    assert (model, mode, request.protocol) == ("client-model", "complete", protocol)
    assert request.json() == body
    assert dict(request.headers) == {
        "openai-organization": "org_client",
        "openai-project": "project_client",
        "idempotency-key": "idem_1",
    }


@pytest.mark.parametrize(
    ("path", "protocol", "named"),
    [
        ("/v1/responses", "openai-responses", True),
        ("/v1/chat/completions", "openai-completions", False),
    ],
)
async def test_native_stream_preserves_responses_and_chat_framing(
    path: str,
    protocol: str,
    named: bool,
) -> None:
    provider = NativeProvider((protocol,))
    body = {
        "model": "client-model",
        "input" if named else "messages": "hello" if named else [],
        "stream": True,
    }

    status, headers, payload = await _request(provider, path, body)

    assert status == 200
    assert headers["content-type"] == "text/event-stream"
    assert headers["x-request-id"] == "req_stream"
    assert payload == b"".join(event.encode() for event in provider.events)
    if named:
        assert b"event: response.created" in payload
        assert b"event: response.completed" in payload
        assert b"[DONE]" not in payload
    else:
        assert b"event:" not in payload
        assert payload.endswith(b"data: [DONE]\n\n")


async def test_native_stream_preserves_upstream_success_status() -> None:
    provider = NativeProvider(("openai-responses",))

    async def request_stream(model: str, request: ProtocolRequest) -> NativeEventStream:
        async def events() -> AsyncIterator[NativeSSEEvent]:
            yield NativeSSEEvent(
                "response.completed",
                b'{"type":"response.completed","response":{"model":"client-model"}}',
            )

        async def close() -> None:
            return None

        return NativeEventStream(
            NativeResponseStart(202, (("content-type", "text/event-stream"),)),
            events(),
            close,
        )

    provider.request_stream = request_stream  # type: ignore[method-assign]

    status, _, _ = await _request(provider, "/v1/responses", {
        "model": "client-model",
        "input": "hello",
        "stream": True,
    })

    assert status == 202


async def test_responses_without_native_support_uses_strict_fallback() -> None:
    provider = NativeProvider(("openai-completions",))

    status, headers, payload = await _request(provider, "/v1/responses", {
        "model": "client-model",
        "input": "hello",
    })

    assert status == 200
    assert headers["x-xdog-upstream-protocol"] == "best-effort"
    assert json.loads(payload)["output"][0]["content"][0]["text"] == "fallback"
    assert provider.requests == []
    assert provider.normalized_calls == ["complete"]


async def test_responses_fallback_rejects_unrepresentable_future_field() -> None:
    provider = NativeProvider(("openai-completions",))

    status, _, payload = await _request(provider, "/v1/responses", {
        "model": "client-model",
        "input": "hello",
        "future_top_level": True,
    })

    assert status == 400
    assert json.loads(payload)["error"]["param"] == "future_top_level"
    assert provider.requests == []
    assert provider.normalized_calls == []


async def test_chat_without_native_support_returns_model_error_without_fallback() -> None:
    provider = NativeProvider(("openai-responses",))

    status, _, payload = await _request(provider, "/v1/chat/completions", {
        "model": "client-model",
        "messages": [],
    })

    assert status == 400
    assert json.loads(payload)["error"] == {
        "message": "Model 'client-model' does not support native Chat Completions",
        "type": "invalid_request_error",
        "param": "model",
        "code": None,
    }
    assert provider.requests == []
    assert provider.normalized_calls == []


@pytest.mark.parametrize(
    ("path", "body", "param"),
    [
        ("/v1/responses", {"model": "", "input": "hello"}, "model"),
        ("/v1/responses", {"model": "m", "stream": "true"}, "stream"),
        ("/v1/chat/completions", {"model": "m", "messages": {}}, "messages"),
    ],
)
async def test_native_outer_validation_happens_before_provider(
    path: str,
    body: dict[str, Any],
    param: str,
) -> None:
    provider = NativeProvider(("openai-responses", "openai-completions"))

    status, _, payload = await _request(provider, path, body)

    assert status == 400
    assert json.loads(payload)["error"]["param"] == param
    assert provider.requests == []


@pytest.mark.parametrize("path", ["/v1/responses", "/v1/chat/completions"])
async def test_chunked_request_is_rejected_before_provider(path: str) -> None:
    provider = NativeProvider(("openai-responses", "openai-completions"))
    body = {"model": "client-model", "input": "hello", "messages": []}

    status, _, payload = await _request(provider, path, body, chunked=True)

    assert status == 400
    assert json.loads(payload)["error"]["param"] is None
    assert provider.requests == []
    assert provider.normalized_calls == []


@pytest.mark.parametrize(
    ("path", "protocol", "protocol_factory"),
    [
        ("/v1/responses", "openai-responses", OpenAIResponsesProtocol),
        ("/v1/chat/completions", "openai-completions", OpenAICompletionsProtocol),
    ],
)
@pytest.mark.parametrize("streaming", [False, True])
async def test_loopback_proxy_copilot_preserves_native_openai_contract(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    protocol: str,
    protocol_factory: Any,
    streaming: bool,
) -> None:
    captured: dict[str, Any] = {}

    def upstream(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        captured["headers"] = dict(request.headers)
        if streaming:
            if protocol == "openai-responses":
                content = (
                    b'event: response.created\ndata: {"type":"response.created",'
                    b'"response":{"model":"wire-model"}}\n\n'
                    b'event: future.event\ndata: {"type":"future.event",'
                    b'"response":{"model":"wire-model"},"opaque":true}\n\n'
                    b'event: response.completed\ndata: {"type":"response.completed",'
                    b'"response":{"model":"wire-model"}}\n\n'
                )
            else:
                content = (
                    b'data: {"id":"chat_1","model":"wire-model","choices":[],"opaque":true}\n\n'
                    b'data: [DONE]\n\n'
                )
            return httpx.Response(
                200,
                content=content,
                headers={"content-type": "text/event-stream", "x-request-id": "req_loopback"},
            )
        return httpx.Response(
            200,
            json={"id": "native_1", "model": "wire-model", "future": {"opaque": True}},
            headers={"content-type": "application/json", "x-request-id": "req_loopback"},
        )

    client_type = httpx.AsyncClient

    def client(**kwargs: Any) -> httpx.AsyncClient:
        return client_type(transport=httpx.MockTransport(upstream), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    provider = CopilotProvider()
    provider._model_cache["copilot/client-model"] = Model(
        id="copilot/wire-model",
        api=protocol,
        provider="copilot",
        base_url="https://upstream.invalid",
        supported_protocols=(protocol,),
        supported_generation_protocols=(protocol,),
    )
    provider._protocols[protocol] = protocol_factory()

    async def resolve_auth(model: Model, context: Context | None = None) -> AuthResult:
        return AuthResult(api_key="vendor-secret", headers={"X-Copilot": "yes"})

    monkeypatch.setattr(provider._get_vendor(), "resolve_auth", resolve_auth)
    body = {
        "model": "client-model",
        "input" if protocol == "openai-responses" else "messages": "hello"
        if protocol == "openai-responses"
        else [],
        "stream": streaming,
        "future_top_level": {"opaque": [1, 2, 3]},
    }

    status, headers, payload = await _request(
        provider,
        path,
        body,
        headers={
            "OpenAI-Organization": "org_client",
            "Authorization": "Bearer local-proxy-secret",
        },
    )

    assert status == 200
    assert headers["x-request-id"] == "req_loopback"
    assert captured["path"] == path
    assert captured["body"] == {**body, "model": "wire-model", "stream": streaming}
    assert captured["headers"]["authorization"] == "Bearer vendor-secret"
    assert captured["headers"]["openai-organization"] == "org_client"
    assert b"local-proxy-secret" not in str(captured).encode()
    if streaming:
        assert headers["content-type"] == "text/event-stream"
        assert b'"model":"client-model"' in payload
        if protocol == "openai-responses":
            assert b"event: future.event" in payload
        else:
            assert b"event:" not in payload
            assert payload.endswith(b"data: [DONE]\n\n")
    else:
        assert json.loads(payload) == {
            "id": "native_1",
            "model": "client-model",
            "future": {"opaque": True},
        }


@pytest.mark.parametrize(
    ("path", "protocol"),
    [("/v1/responses", "openai-responses"), ("/v1/chat/completions", "openai-completions")],
)
async def test_native_openai_transport_failure_before_response_is_sanitized(
    path: str,
    protocol: str,
) -> None:
    provider = NativeProvider((protocol,))

    async def fail(model: str, request: ProtocolRequest) -> NativeResponse:
        raise OSError(errno.ECONNRESET, "sensitive connection detail")

    provider.request_complete = fail  # type: ignore[method-assign]
    body = {"model": "client-model", "input": "hello", "messages": []}

    status, _, payload = await _request(provider, path, body)

    assert status == 502
    error = json.loads(payload)["error"]
    assert error == {
        "message": "Upstream connection failed; retry later",
        "type": "server_error",
        "param": None,
        "code": None,
    }
    assert b"sensitive connection detail" not in payload


@pytest.mark.parametrize(
    ("path", "protocol", "named"),
    [
        ("/v1/responses", "openai-responses", True),
        ("/v1/chat/completions", "openai-completions", False),
    ],
)
async def test_native_openai_premature_eof_emits_terminal_error(
    path: str,
    protocol: str,
    named: bool,
) -> None:
    provider = NativeProvider((protocol,))
    provider.events = (
        NativeSSEEvent(
            "response.created" if named else None,
            b'{"type":"response.created","response":{"model":"client-model"}}'
            if named
            else b'{"id":"chat_1","model":"client-model","choices":[]}',
        ),
    )
    body = {"model": "client-model", "input": "hello", "messages": [], "stream": True}

    status, _, payload = await _request(provider, path, body)

    assert status == 200
    assert payload.count(b"Upstream connection failed; retry later") == 1
    assert b"[DONE]" not in payload
    if named:
        assert payload.count(b"event: error") == 1
    else:
        assert b"event:" not in payload
        terminal = payload.decode().strip().split("\n\n")[-1]
        assert json.loads(terminal.removeprefix("data: "))["error"]["type"] == "server_error"


@pytest.mark.parametrize(
    ("path", "protocol", "named"),
    [
        ("/v1/responses", "openai-responses", True),
        ("/v1/chat/completions", "openai-completions", False),
    ],
)
async def test_native_openai_stream_failure_is_terminal_and_sanitized(
    path: str,
    protocol: str,
    named: bool,
) -> None:
    provider = NativeProvider((protocol,))

    async def request_stream(model: str, request: ProtocolRequest) -> NativeEventStream:
        async def events() -> AsyncIterator[NativeSSEEvent]:
            yield NativeSSEEvent(
                "response.created" if named else None,
                b'{"type":"response.created","response":{"model":"client-model"}}'
                if named
                else b'{"id":"chat_1","model":"client-model","choices":[]}',
            )
            raise OSError(errno.ECONNRESET, "sensitive connection detail")

        async def close() -> None:
            return None

        return NativeEventStream(
            NativeResponseStart(200, (("content-type", "text/event-stream"),)),
            events(),
            close,
        )

    provider.request_stream = request_stream  # type: ignore[method-assign]
    body = {"model": "client-model", "input": "hello", "messages": [], "stream": True}

    status, _, payload = await _request(provider, path, body)

    assert status == 200
    assert payload.count(b"Upstream connection failed; retry later") == 1
    assert b"sensitive connection detail" not in payload
    assert b"[DONE]" not in payload
    if named:
        assert payload.count(b"event: error") == 1
    else:
        assert b"event:" not in payload
        terminal = payload.decode().strip().split("\n\n")[-1]
        assert json.loads(terminal.removeprefix("data: "))["error"]["type"] == "server_error"


@pytest.mark.parametrize(
    ("path", "protocol"),
    [("/v1/responses", "openai-responses"), ("/v1/chat/completions", "openai-completions")],
)
async def test_native_openai_preserves_upstream_error(
    path: str,
    protocol: str,
) -> None:
    provider = NativeProvider((protocol,))

    async def fail(model: str, request: ProtocolRequest) -> NativeResponse:
        raise NativeHTTPError(NativeResponse(
            429,
            b'{"error":{"message":"slow","type":"rate_limit_error","param":null,"code":"rate_limit"}}',
            (("content-type", "application/json"), ("x-request-id", "req_429"), ("retry-after", "5")),
        ))

    provider.request_complete = fail  # type: ignore[method-assign]
    body = {"model": "client-model", "input": "hello", "messages": []}

    status, headers, payload = await _request(provider, path, body)

    assert status == 429
    assert headers["x-request-id"] == "req_429"
    assert headers["retry-after"] == "5"
    assert json.loads(payload)["error"]["message"] == "slow"
