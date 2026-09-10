from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from xdog.ai.core import AuthResult, BaseProtocol
from xdog.ai.native import NativeHTTPError, NativeOperation, NativeSSEEvent, ProtocolRequest
from xdog.ai.protocols.openai_completions import OpenAICompletionsProtocol
from xdog.ai.protocols.openai_responses import OpenAIResponsesProtocol
from xdog.ai.types import Model


@dataclass(frozen=True)
class ProtocolCase:
    protocol: str
    endpoint: str
    factory: Callable[[], BaseProtocol]
    named_sse: bool


_CASES = (
    ProtocolCase("openai-responses", "/v1/responses", OpenAIResponsesProtocol, True),
    ProtocolCase("openai-completions", "/v1/chat/completions", OpenAICompletionsProtocol, False),
)

_RESPONSES_BODY: dict[str, Any] = {
    "model": "client/gpt-test",
    "background": False,
    "context_management": [{"type": "compaction", "compact_threshold": 120000}],
    "conversation": "conv_123",
    "include": ["reasoning.encrypted_content", "message.output_text.logprobs"],
    "input": [
        {
            "role": "developer",
            "content": [{"type": "input_text", "text": "system", "cache_control": {"type": "ephemeral"}}],
        },
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": "hello"},
                {"type": "input_image", "image_url": "https://example.invalid/image.png", "detail": "high"},
                {"type": "input_file", "file_id": "file_123"},
            ],
        },
        {"type": "reasoning", "id": "rs_123", "encrypted_content": "opaque"},
        {"type": "compaction", "encrypted_content": "encrypted-compaction"},
        {"type": "future_input", "opaque": {"preserve": True}},
    ],
    "instructions": "respond",
    "max_output_tokens": 0,
    "max_tool_calls": 9,
    "metadata": {"key": "value"},
    "moderation": {"type": "openai", "model": "omni-moderation-latest"},
    "parallel_tool_calls": False,
    "previous_response_id": "resp_previous",
    "prompt": {"id": "pmpt_123", "version": "4", "variables": {"name": "Ada"}},
    "prompt_cache_key": "cache-key",
    "prompt_cache_options": {"mode": "explicit", "ttl": "30m"},
    "prompt_cache_retention": "24h",
    "reasoning": {"effort": "max", "summary": "detailed", "context": "opaque", "mode": "auto"},
    "safety_identifier": "safe_123",
    "service_tier": "priority",
    "store": True,
    "stream": False,
    "stream_options": {"include_obfuscation": False},
    "temperature": 0,
    "text": {"format": {"type": "json_schema", "name": "result", "schema": {"type": "object"}}, "verbosity": "high"},
    "tool_choice": {"type": "allowed_tools", "mode": "required", "tools": [{"type": "function", "name": "lookup"}]},
    "tools": [
        {
            "type": "function",
            "name": "lookup",
            "description": "Lookup",
            "parameters": {"type": "object"},
            "strict": True,
        },
        {"type": "web_search", "search_context_size": "high"},
        {"type": "mcp", "server_label": "docs", "server_url": "https://example.invalid/mcp"},
        {"type": "shell"},
        {"type": "apply_patch"},
        {"type": "programmatic_tool_calling"},
        {"type": "future_tool", "opaque": [1, 2, 3]},
    ],
    "top_logprobs": 5,
    "top_p": 0.95,
    "truncation": "auto",
    "user": "deprecated-user",
    "future_top_level": {"unknown": [None, "value", {"nested": True}]},
}

_CHAT_BODY: dict[str, Any] = {
    "model": "client/gpt-test",
    "messages": [
        {"role": "developer", "content": "system"},
        {"role": "system", "content": [{"type": "text", "text": "legacy system"}]},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "hello"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAE=", "detail": "high"}},
                {"type": "input_audio", "input_audio": {"data": "AAE=", "format": "wav"}},
                {"type": "file", "file": {"file_id": "file_123"}},
            ],
        },
        {
            "role": "assistant",
            "content": [{"type": "refusal", "refusal": "no"}],
            "audio": {"id": "audio_123"},
            "tool_calls": [{"id": "call_123", "type": "function", "function": {"name": "lookup", "arguments": "{}"}}],
            "function_call": {"name": "legacy", "arguments": "{}"},
        },
        {"role": "tool", "tool_call_id": "call_123", "content": "result"},
        {"role": "function", "name": "legacy", "content": "result"},
        {"role": "future", "content": {"opaque": True}},
    ],
    "audio": {"format": "wav", "voice": "alloy"},
    "frequency_penalty": 0,
    "function_call": "auto",
    "functions": [{"name": "legacy", "parameters": {"type": "object"}}],
    "logit_bias": {"42": -1},
    "logprobs": True,
    "max_completion_tokens": 0,
    "max_tokens": 0,
    "metadata": {"key": "value"},
    "modalities": ["text", "audio"],
    "moderation": {"type": "openai"},
    "n": 2,
    "parallel_tool_calls": False,
    "prediction": {"type": "content", "content": "known output"},
    "presence_penalty": 0,
    "prompt_cache_key": "cache-key",
    "prompt_cache_options": {"mode": "explicit", "ttl": "30m"},
    "prompt_cache_retention": "24h",
    "reasoning_effort": "max",
    "response_format": {"type": "json_schema", "json_schema": {"name": "result", "schema": {"type": "object"}}},
    "safety_identifier": "safe_123",
    "seed": 0,
    "service_tier": "priority",
    "stop": ["DONE"],
    "store": True,
    "stream": False,
    "stream_options": {"include_usage": True, "include_obfuscation": False},
    "temperature": 0,
    "tool_choice": {
        "type": "allowed_tools",
        "mode": "required",
        "tools": [{"type": "function", "function": {"name": "lookup"}}],
    },
    "tools": [
        {"type": "function", "function": {"name": "lookup", "parameters": {"type": "object"}, "strict": True}},
        {
            "type": "custom",
            "custom": {
                "name": "shell",
                "format": {
                    "type": "grammar",
                    "grammar": {"syntax": "lark", "definition": "start: WORD"},
                },
            },
        },
        {"type": "future_tool", "opaque": True},
    ],
    "top_logprobs": 5,
    "top_p": 0.95,
    "user": "deprecated-user",
    "verbosity": "high",
    "web_search_options": {"search_context_size": "high"},
    "future_top_level": {"unknown": [None, "value", {"nested": True}]},
}


def _body(case: ProtocolCase) -> dict[str, Any]:
    return _RESPONSES_BODY if case.protocol == "openai-responses" else _CHAT_BODY


def _client(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    client_type = httpx.AsyncClient

    def factory(**kwargs: Any) -> httpx.AsyncClient:
        return client_type(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)


@pytest.mark.parametrize("case", _CASES, ids=lambda case: case.protocol)
async def test_native_complete_preserves_full_body_headers_and_alias(
    monkeypatch: pytest.MonkeyPatch,
    case: ProtocolCase,
) -> None:
    captured: dict[str, Any] = {}

    def handle(request: httpx.Request) -> httpx.Response:
        captured["url"] = request.url
        captured["body"] = json.loads(request.content)
        captured["headers"] = dict(request.headers)
        return httpx.Response(
            200,
            headers={
                "content-type": "application/json",
                "x-request-id": "req_upstream",
                "retry-after": "3",
                "x-ratelimit-remaining-requests": "4",
                "set-cookie": "private=1",
                "x-internal-secret": "drop-me",
            },
            json={
                "id": "native_123",
                "model": "wire-gpt-test",
                "output" if case.named_sse else "choices": [],
                "nested": {"model": "do-not-rewrite"},
                "future_response": {"opaque": True},
            },
        )

    _client(monkeypatch, handle)
    source = _body(case)
    request = ProtocolRequest.from_json(
        case.protocol,
        source,
        headers={
            "OpenAI-Organization": "org_client",
            "OpenAI-Project": "project_client",
            "Idempotency-Key": "idem_123",
            "Authorization": "Bearer local-secret",
        },
    )
    model = Model(
        id="wire-gpt-test",
        provider="copilot",
        base_url="https://upstream.invalid",
        headers={
            "Authorization": "Bearer stale-model-secret",
            "x-api-key": "stale-model-secret",
            "OpenAI-Organization": "org_model",
            "X-Model-Header": "keep-model-metadata",
        },
    )

    result = await case.factory().request_complete(
        model,
        request,
        AuthResult(api_key="vendor-secret", headers={"X-Copilot": "yes"}),
    )

    assert captured["url"].path == case.endpoint
    assert captured["body"] == {**source, "model": "wire-gpt-test", "stream": False}
    assert captured["headers"]["authorization"] == "Bearer vendor-secret"
    assert "x-api-key" not in captured["headers"]
    assert captured["headers"]["openai-organization"] == "org_client"
    assert captured["headers"]["openai-project"] == "project_client"
    assert captured["headers"]["idempotency-key"] == "idem_123"
    assert captured["headers"]["x-model-header"] == "keep-model-metadata"
    assert source["model"] == "client/gpt-test"
    assert result.status == 200
    assert result.header("x-request-id") == "req_upstream"
    assert result.header("retry-after") == "3"
    assert result.header("x-ratelimit-remaining-requests") == "4"
    assert result.header("set-cookie") is None
    assert result.header("x-internal-secret") is None
    assert result.json()["model"] == "client/gpt-test"
    assert result.json()["nested"]["model"] == "do-not-rewrite"


@pytest.mark.parametrize("case", _CASES, ids=lambda case: case.protocol)
async def test_native_complete_drops_auth_transport_headers(
    monkeypatch: pytest.MonkeyPatch,
    case: ProtocolCase,
) -> None:
    captured: dict[str, str] = {}

    def handle(request: httpx.Request) -> httpx.Response:
        captured.update(request.headers)
        return httpx.Response(200, json={"model": "wire"})

    _client(monkeypatch, handle)
    await case.factory().request_complete(
        Model(id="wire", base_url="https://upstream.invalid"),
        ProtocolRequest.from_json(case.protocol, _body(case)),
        AuthResult(
            api_key="vendor-secret",
            headers={
                "Host": "attacker.invalid",
                "Content-Length": "999",
                "Transfer-Encoding": "chunked",
                "Content-Encoding": "gzip",
            },
        ),
    )

    assert captured["authorization"] == "Bearer vendor-secret"
    assert captured["host"] == "upstream.invalid"
    assert captured["content-length"] != "999"
    assert "transfer-encoding" not in captured
    assert "content-encoding" not in captured


@pytest.mark.parametrize("case", _CASES, ids=lambda case: case.protocol)
async def test_native_complete_preserves_upstream_http_error(
    monkeypatch: pytest.MonkeyPatch,
    case: ProtocolCase,
) -> None:
    error = {
        "error": {"message": "invalid", "type": "invalid_request_error", "param": "input", "code": "bad"},
    }

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            headers={"content-type": "application/json", "x-request-id": "req_429", "retry-after": "9"},
            json=error,
        )

    _client(monkeypatch, handle)

    with pytest.raises(NativeHTTPError) as caught:
        await case.factory().request_complete(
            Model(id="wire", base_url="https://upstream.invalid"),
            ProtocolRequest.from_json(case.protocol, _body(case)),
            AuthResult(api_key="vendor-secret"),
        )

    assert caught.value.response.status == 429
    assert caught.value.response.json() == error
    assert caught.value.response.header("x-request-id") == "req_429"
    assert caught.value.response.header("retry-after") == "9"
    assert "invalid" not in str(caught.value)


@pytest.mark.parametrize("case", _CASES, ids=lambda case: case.protocol)
async def test_native_stream_preserves_protocol_framing_and_alias(
    monkeypatch: pytest.MonkeyPatch,
    case: ProtocolCase,
) -> None:
    if case.named_sse:
        content = (
            b'event: response.created\ndata: {"type":"response.created",'
            b'"response":{"id":"resp_1","model":"wire-gpt-test"}}\n\n'
            b'event: future.event\ndata: {"type":"future.event",'
            b'"response":{"model":"wire-gpt-test"},"opaque":true}\n\n'
        )
    else:
        content = (
            b'data: {"id":"chatcmpl_1","model":"wire-gpt-test","choices":[],"future":"opaque"}\n\n'
            b'data: [DONE]\n\n'
        )

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == case.endpoint
        assert json.loads(request.content) == {**_body(case), "model": "wire-gpt-test", "stream": True}
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream", "x-request-id": "req_stream"},
            content=content,
        )

    _client(monkeypatch, handle)
    stream = await case.factory().request_stream(
        Model(id="wire-gpt-test", base_url="https://upstream.invalid"),
        ProtocolRequest.from_json(case.protocol, _body(case)),
        AuthResult(api_key="vendor-secret"),
    )
    events = [event async for event in stream]
    await stream.aclose()

    assert stream.start.status == 200
    assert stream.start.header("x-request-id") == "req_stream"
    assert all(isinstance(event, NativeSSEEvent) for event in events)
    if case.named_sse:
        assert [event.event for event in events] == ["response.created", "future.event"]
        assert [event.json()["response"]["model"] for event in events] == [
            "client/gpt-test",
            "client/gpt-test",
        ]
    else:
        assert [event.event for event in events] == [None, None]
        assert events[0].json()["model"] == "client/gpt-test"
        assert events[1].data == b"[DONE]"
        assert events[1].encode() == b"data: [DONE]\n\n"


@pytest.mark.parametrize("case", _CASES, ids=lambda case: case.protocol)
@pytest.mark.parametrize("method", ["request_complete", "request_stream"])
async def test_native_openai_rejects_count_operation_before_io(
    monkeypatch: pytest.MonkeyPatch,
    case: ProtocolCase,
    method: str,
) -> None:
    def fail_client(**kwargs: Any) -> httpx.AsyncClient:
        raise AssertionError("HTTP client must not be constructed")

    monkeypatch.setattr(httpx, "AsyncClient", fail_client)
    request = ProtocolRequest.from_json(
        case.protocol,
        _body(case),
        operation=NativeOperation.COUNT_TOKENS,
    )

    with pytest.raises(ValueError, match="does not support operation"):
        await getattr(case.factory(), method)(
            Model(id="wire", base_url="https://upstream.invalid"),
            request,
            AuthResult(api_key="vendor-secret"),
        )


@pytest.mark.parametrize("case", _CASES, ids=lambda case: case.protocol)
async def test_native_transport_rejects_protocol_mismatch_before_io(
    monkeypatch: pytest.MonkeyPatch,
    case: ProtocolCase,
) -> None:
    called = False

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={})

    _client(monkeypatch, handle)
    wrong = "openai-completions" if case.protocol == "openai-responses" else "openai-responses"

    with pytest.raises(ValueError, match="Expected"):
        await case.factory().request_complete(
            Model(id="wire", base_url="https://upstream.invalid"),
            ProtocolRequest.from_json(wrong, _body(case)),
            AuthResult(api_key="vendor-secret"),
        )

    assert called is False
