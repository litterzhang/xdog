"""Encrypted reasoning and its upstream item ID must survive a proxy round trip."""
from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from xdog.ai.core import AuthResult
from xdog.ai.protocols import openai_responses
from xdog.ai.proxy_responses import (
    InvalidRequest,
    _client_reasoning_id,
    format_response,
    parse_request,
    stream_to_sse,
)
from xdog.ai.types import (
    AssistantMessage,
    DoneEvent,
    ErrorEvent,
    Model,
    TextContent,
    TextDeltaEvent,
    TextDoneEvent,
    TextStartEvent,
    ThinkingContent,
    ThinkingDeltaEvent,
    ThinkingDoneEvent,
    ThinkingStartEvent,
)


def _signature(item_id: str) -> str:
    # Stand-in for the upstream's opaque encrypted content. Binding it to the ID
    # makes this test reject the old proxy's mismatched pair on the SECOND turn.
    return hmac.new(b"test-only-key", item_id.encode(), hashlib.sha256).hexdigest()


def _reasoning(item_id: str = "rs_upstream_signed") -> ThinkingContent:
    return ThinkingContent(thinking="Plan", thinking_signature=json.dumps({
        "type": "reasoning", "id": item_id, "summary": [{"type": "summary_text", "text": "Plan"}],
        "encrypted_content": _signature(item_id),
    }))


def _decode(body: bytes) -> list[dict[str, Any]]:
    events = [json.loads(chunk.split(b"\ndata: ", 1)[1]) for chunk in body.strip().split(b"\n\n")]
    assert [event["sequence_number"] for event in events] == list(range(len(events)))
    return events


async def _events(events: list[Any]) -> AsyncIterator[Any]:
    for event in events:
        yield event


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("id_style", ["native", "provisional", "opaque"])
async def test_upstream_encrypted_reasoning_verifies_on_second_turn(
    monkeypatch: pytest.MonkeyPatch, stream: bool, id_style: str,
) -> None:
    requests: list[dict[str, Any]] = []

    def original_id(turn: int) -> str:
        return f"opaque+/signed-{turn}=" if id_style == "opaque" else f"rs_upstream_signed_{turn}"

    def starting_id(turn: int) -> str:
        return original_id(turn) if id_style == "native" else f"provisional+/item-{turn}="

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        for item in body["input"]:
            if item.get("type") == "reasoning":
                if item.get("encrypted_content") != _signature(item["id"]):
                    return httpx.Response(400, json={"error": {
                        "message": "Encrypted content item_id did not match the target item id.",
                        "type": "server_error",
                    }})
        item_id = original_id(len(requests))
        # Real Copilot responses can carry encrypted but provisional IDs at
        # item.added, replacing both the ID and ciphertext at item.done.
        initial = json.loads(_reasoning(starting_id(len(requests))).thinking_signature or "{}")
        initial["summary"] = []
        frames = [
            {"type": "response.output_item.added", "output_index": 0,
             "item": initial},
            {"type": "response.reasoning_summary_text.delta", "delta": "Plan"},
            {"type": "response.output_item.done", "output_index": 0,
             "item": json.loads(_reasoning(item_id).thinking_signature or "{}")},
            {"type": "response.completed", "response": {"id": "resp_upstream", "status": "completed"}},
        ]
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, content="".join(
            f"event: {frame['type']}\ndata: {json.dumps(frame)}\n\n" for frame in frames
        ).encode())

    real_client = httpx.AsyncClient
    monkeypatch.setattr(openai_responses.httpx, "AsyncClient", lambda **kwargs: real_client(
        transport=httpx.MockTransport(handle), **kwargs,
    ))
    model = Model(id="test", api="openai-responses", base_url="https://upstream.invalid")
    request: dict[str, Any] = {"model": "test", "input": "Hi", "stream": stream}
    first_output: list[dict[str, Any]] = []
    for turn in range(2):
        _, context, options, _ = parse_request(request)
        upstream = openai_responses._stream_impl(model, context, options, AuthResult(api_key="test"))
        if stream:
            events = _decode(b"".join([chunk async for chunk in stream_to_sse(upstream, "test", request)]))
            assert events[-1]["type"] == "response.completed"
            result = events[-1]["response"]
            for event in events:
                if event["type"] in ("response.output_item.added", "response.output_item.done"):
                    assert event["item"]["id"] == _client_reasoning_id(original_id(turn + 1))
                if "item_id" in event:
                    assert event["item_id"] == _client_reasoning_id(original_id(turn + 1))
        else:
            raw_events = [event async for event in upstream]
            assert isinstance(raw_events[0], ThinkingStartEvent) or raw_events[0].type == "start"
            start = next(event for event in raw_events if isinstance(event, ThinkingStartEvent))
            assert start.partial and isinstance(start.partial.content[0], ThinkingContent)
            start_id = json.loads(start.partial.content[0].thinking_signature or "{}")["id"]
            assert start_id == starting_id(turn + 1)
            terminal = raw_events[-1]
            assert isinstance(terminal, DoneEvent) and terminal.message
            result = format_response(terminal.message, "test", request)
        item = result["output"][0]
        assert item["id"] == _client_reasoning_id(original_id(turn + 1))
        assert item["encrypted_content"] == _signature(original_id(turn + 1))
        if turn == 0:
            first_output = result["output"]
        request = {**request, "input": [*result["output"], {"role": "user", "content": "Continue"}]}
    replayed = next(item for item in requests[1]["input"] if item.get("type") == "reasoning")
    assert (replayed["id"], replayed["encrypted_content"]) == (
        original_id(1), first_output[0]["encrypted_content"],
    )
    # Prove the mock verifier catches exactly the corruption reported by the user.
    bad_input = [{**first_output[0], "id": "rs_proxy_generated"}]
    _, context, options, _ = parse_request({"model": "test", "input": bad_input})
    rejected = [event async for event in openai_responses._stream_impl(
        model, context, options, AuthResult(api_key="test"),
    )]
    assert isinstance(rejected[-1], ErrorEvent) and "item_id did not match" in rejected[-1].error


@pytest.mark.parametrize("metadata_at", ["start", "block_done", "terminal"])
async def test_reasoning_id_stable_with_interleaved_events_and_late_metadata(metadata_at: str) -> None:
    reasoning = _reasoning()
    msg = AssistantMessage(content=(reasoning, TextContent(text="Hello")))
    start_partial = AssistantMessage(content=(reasoning,)) if metadata_at == "start" else None
    block_partial = msg if metadata_at == "block_done" else None
    raw = [
        ThinkingStartEvent(index=0, partial=start_partial), ThinkingDeltaEvent(index=0, delta="Plan"),
        TextStartEvent(index=1), TextDeltaEvent(index=1, delta="Hello"),
        ThinkingDoneEvent(index=0, thinking="Plan", partial=block_partial),
        TextDoneEvent(index=1, text="Hello"), DoneEvent(message=msg),
    ]
    events = _decode(b"".join([chunk async for chunk in stream_to_sse(_events(raw), "test")]))
    assert events[-1]["type"] == "response.completed"
    output = events[-1]["response"]["output"]
    assert [item["type"] for item in output] == ["reasoning", "message"]
    assert output[0]["id"] == "rs_upstream_signed"
    assert output[0]["encrypted_content"] == _signature("rs_upstream_signed")
    for event in events:
        if event.get("output_index") == 0:
            if "item" in event:
                assert event["item"]["id"] == "rs_upstream_signed"
            if "item_id" in event:
                assert event["item_id"] == "rs_upstream_signed"


async def test_reasoning_waits_for_final_id_but_not_for_response_completion() -> None:
    advanced = False
    completed = False

    async def upstream() -> AsyncIterator[Any]:
        nonlocal advanced, completed
        partial = AssistantMessage(content=(ThinkingContent(thinking_signature=json.dumps({
            "type": "reasoning", "id": "rs_upstream_signed", "summary": [],
        })),))
        yield ThinkingStartEvent(partial=partial)
        advanced = True
        yield ThinkingDeltaEvent(delta="Plan")
        yield ThinkingDoneEvent(thinking="Plan", thinking_signature=_reasoning().thinking_signature)
        completed = True
        yield DoneEvent(message=AssistantMessage(content=(_reasoning(),)))

    stream = stream_to_sse(upstream(), "test")
    try:
        chunks = [await anext(stream) for _ in range(3)]
        assert b"response.output_item.added" in chunks[-1] and b"rs_upstream_signed" in chunks[-1]
        assert advanced  # Wait for the block-done metadata, not the provisional ID.
        assert not completed  # Subsequent text/tool generation can still stream.
        chunks.extend([chunk async for chunk in stream])
        assert _decode(b"".join(chunks))[-1]["type"] == "response.completed"
    finally:
        await stream.aclose()


async def test_inconsistent_upstream_identity_never_emits_mismatched_ciphertext() -> None:
    start = AssistantMessage(content=(_reasoning("rs_first"),))
    raw = [ThinkingStartEvent(partial=start), ThinkingDeltaEvent(delta="Plan"),
           ThinkingDoneEvent(thinking="Plan", thinking_signature=_reasoning("rs_first").thinking_signature),
           DoneEvent(message=AssistantMessage(content=(_reasoning("rs_second"),)))]
    events = _decode(b"".join([chunk async for chunk in stream_to_sse(_events(raw), "test")]))
    assert events[-1]["type"] == "response.failed"
    assert "after finalization" in events[-1]["response"]["error"]["message"]
    for event in events:
        for item in [event.get("item", {}), *event.get("response", {}).get("output", [])]:
            if "encrypted_content" in item:
                assert item["encrypted_content"] == _signature(item["id"])


@pytest.mark.parametrize("signature", [None, "anthropic-opaque-signature"])
async def test_unsigned_non_responses_reasoning_still_completes(signature: str | None) -> None:
    part = ThinkingContent(thinking="Plan", thinking_signature=signature)
    msg = AssistantMessage(content=(part,), api="anthropic-messages")
    events = _decode(b"".join([chunk async for chunk in stream_to_sse(_events([
        ThinkingStartEvent(), ThinkingDeltaEvent(delta="Plan"),
        ThinkingDoneEvent(thinking="Plan", partial=msg), DoneEvent(message=msg),
    ]), "test")]))
    assert events[-1]["type"] == "response.completed"
    item = events[-1]["response"]["output"][0]
    assert item["summary"][0]["text"] == "Plan" and "encrypted_content" not in item


@pytest.mark.parametrize("item_id", [None, "", 12])
def test_encrypted_reasoning_cannot_be_attached_to_generated_id(item_id: Any) -> None:
    raw = {"type": "reasoning", "id": item_id, "summary": [], "encrypted_content": "opaque"}
    with pytest.raises(InvalidRequest) as exc:
        parse_request({"model": "test", "input": [raw]})
    assert exc.value.param == "input[0].id"
    with pytest.raises(ValueError, match="original item id"):
        format_response(AssistantMessage(content=(ThinkingContent(thinking_signature=json.dumps(raw)),)), "test")


@pytest.mark.parametrize("original_id", [
    "rs_native", "opaque+/Copilot-id=", "rs_xdog_v1_reserved", "rs_invalid/chars", "unicode-雪-id",
    "opaque+" * 100,
])
def test_reasoning_id_codec_preserves_signed_identity(original_id: str) -> None:
    part = _reasoning(original_id)
    output = format_response(AssistantMessage(content=(part,)), "test")["output"][0]
    assert output["id"].startswith("rs_")
    assert output["encrypted_content"] == _signature(original_id)
    if original_id != "rs_native":
        assert output["id"].startswith("rs_xdog_v1_")
    _, context, _, _ = parse_request({"model": "test", "input": [output]})
    replay = openai_responses.context_to_responses_input(context, Model())
    assert replay[0]["id"] == original_id
    assert replay[0]["encrypted_content"] == _signature(original_id)


@pytest.mark.parametrize("item_id", ["rs_xdog_v1_", "rs_xdog_v1_!", "rs_xdog_v1_abc===", "rs_xdog_v1__w"])
def test_malformed_encoded_reasoning_id_rejected(item_id: str) -> None:
    with pytest.raises(InvalidRequest) as exc:
        parse_request({"model": "test", "input": [{
            "type": "reasoning", "id": item_id, "summary": [], "encrypted_content": "opaque",
        }]})
    assert exc.value.param == "input[0].id"
