from __future__ import annotations

import math

import pytest
from xdog.ai.native import NativeOperation, NativeSSEEvent, ProtocolRequest


def test_protocol_request_filters_anthropic_headers() -> None:
    request = ProtocolRequest.from_json(
        "anthropic-messages",
        {"model": "claude"},
        headers={
            "Anthropic-Version": " 2023-06-01 ",
            "Anthropic-Beta": "feature-1",
            "Anthropic-Workspace-ID": "workspace",
            "Anthropic-User-Profile-ID": "profile",
            "Authorization": "Bearer local-secret",
            "X-API-Key": "local-secret",
            "Host": "example.invalid",
            "Content-Length": "12",
            "Unrelated": "drop-me",
        },
    )

    assert request.headers == (
        ("anthropic-version", "2023-06-01"),
        ("anthropic-beta", "feature-1"),
        ("anthropic-workspace-id", "workspace"),
        ("anthropic-user-profile-id", "profile"),
    )


@pytest.mark.parametrize("protocol", ["openai-responses", "openai-completions"])
def test_protocol_request_filters_openai_headers(protocol: str) -> None:
    request = ProtocolRequest.from_json(
        protocol,
        {"model": "gpt"},
        headers={
            "OpenAI-Organization": " org_123 ",
            "OpenAI-Project": "project_123",
            "OpenAI-Beta": "responses=v1",
            "Idempotency-Key": "idem_123",
            "Authorization": "Bearer local-secret",
            "X-API-Key": "local-secret",
            "Anthropic-Beta": "drop-me",
            "Connection": "keep-alive",
            "Transfer-Encoding": "chunked",
        },
    )

    assert request.headers == (
        ("openai-organization", "org_123"),
        ("openai-project", "project_123"),
        ("openai-beta", "responses=v1"),
        ("idempotency-key", "idem_123"),
    )


def test_protocol_request_unknown_protocol_drops_headers() -> None:
    request = ProtocolRequest.from_json(
        "future-protocol",
        {"model": "future"},
        headers={"OpenAI-Project": "project", "Anthropic-Beta": "beta"},
    )

    assert request.headers == ()


def test_protocol_request_rejects_invalid_allowed_header() -> None:
    with pytest.raises(ValueError, match="Invalid HTTP header"):
        ProtocolRequest.from_json(
            "openai-responses",
            {"model": "gpt"},
            headers={"OpenAI-Project": "safe\r\ninjected: value"},
        )


def test_protocol_request_body_is_immutable_snapshot() -> None:
    body = {"model": "gpt", "nested": {"items": [1]}}
    request = ProtocolRequest.from_json("openai-responses", body)

    body["nested"]["items"].append(2)

    assert request.json() == {"model": "gpt", "nested": {"items": [1]}}


def test_native_operation_is_publicly_exported() -> None:
    from xdog.ai import NativeOperation as PublicNativeOperation

    assert PublicNativeOperation is NativeOperation


def test_protocol_request_defaults_to_generate_operation() -> None:
    request = ProtocolRequest.from_json("anthropic-messages", {"model": "claude"})

    assert request.operation is NativeOperation.GENERATE


def test_protocol_request_preserves_count_operation_outside_json() -> None:
    source = {"model": "claude", "messages": [], "future": {"opaque": True}}
    request = ProtocolRequest.from_json(
        "anthropic-messages",
        source,
        operation=NativeOperation.COUNT_TOKENS,
    )

    assert request.operation is NativeOperation.COUNT_TOKENS
    assert request.json() == source
    assert "operation" not in request.json()


def test_protocol_request_positional_construction_remains_compatible() -> None:
    request = ProtocolRequest("anthropic-messages", b'{"model":"claude"}', ())

    assert request.operation is NativeOperation.GENERATE


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_protocol_request_rejects_non_finite_json(value: float) -> None:
    with pytest.raises(ValueError):
        ProtocolRequest.from_json("openai-responses", {"temperature": value})


def test_named_sse_event_preserves_existing_encoding() -> None:
    event = NativeSSEEvent("response.output_text.delta", b"first\nsecond")

    assert event.encode() == (
        b"event: response.output_text.delta\n"
        b"data: first\n"
        b"data: second\n\n"
    )


def test_unnamed_sse_event_omits_event_field() -> None:
    event = NativeSSEEvent(None, b'{"id":"chatcmpl_123"}')

    assert event.encode() == b'data: {"id":"chatcmpl_123"}\n\n'


def test_chat_done_sse_event_is_preserved_exactly() -> None:
    event = NativeSSEEvent(None, b"[DONE]")

    assert event.encode() == b"data: [DONE]\n\n"


async def test_native_event_stream_closes_once() -> None:
    from collections.abc import AsyncIterator

    from xdog.ai.native import NativeEventStream, NativeResponseStart

    closed = 0

    async def events() -> AsyncIterator[NativeSSEEvent]:
        yield NativeSSEEvent(None, b"first")

    async def close() -> None:
        nonlocal closed
        closed += 1

    stream = NativeEventStream(NativeResponseStart(200), events(), close)
    assert [event.data async for event in stream] == [b"first"]
    await stream.aclose()

    assert closed == 1
