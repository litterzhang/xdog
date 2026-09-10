from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from xdog.ai.core import AuthResult
from xdog.ai.protocols import anthropic_messages
from xdog.ai.types import (
    Context,
    ImageContent,
    Model,
    StreamOptions,
    TextContent,
    ThinkingContent,
    ToolResultMessage,
)


def _mock_anthropic(monkeypatch: pytest.MonkeyPatch, frames: list[dict[str, Any]], captured: dict[str, Any]) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        content = "".join(
            f"event: {frame['type']}\ndata: {json.dumps(frame)}\n\n"
            for frame in frames
        ).encode()
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=content)

    client_type = httpx.AsyncClient

    def client(**kwargs: Any) -> httpx.AsyncClient:
        return client_type(transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(anthropic_messages.httpx, "AsyncClient", client)


async def test_normalized_anthropic_preserves_zero_tokens_and_rich_tool_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    _mock_anthropic(monkeypatch, [
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}},
        {"type": "message_stop"},
    ], captured)
    context = Context(messages=(ToolResultMessage(
        tool_call_id="toolu_1",
        is_error=True,
        content=(
            TextContent(text="failed"),
            ImageContent(data="AAAA", mime_type="image/png"),
        ),
    ),))

    events = [event async for event in anthropic_messages._stream_impl(
        Model(id="claude-test", base_url="https://upstream.invalid"),
        context,
        StreamOptions(max_tokens=0),
        AuthResult(api_key="test"),
    )]

    assert events[-1].type == "done"
    assert captured["body"]["max_tokens"] == 0
    block = captured["body"]["messages"][0]["content"][0]
    assert block == {
        "type": "tool_result",
        "tool_use_id": "toolu_1",
        "is_error": True,
        "content": [
            {"type": "text", "text": "failed"},
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"}},
        ],
    }


async def test_normalized_anthropic_maps_sparse_wire_indices_to_dense_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    _mock_anthropic(monkeypatch, [
        {"type": "content_block_start", "index": 0, "content_block": {"type": "server_tool_use", "id": "srv"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "content_block_start", "index": 4, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 4, "delta": {"type": "text_delta", "text": "hello"}},
        {"type": "content_block_stop", "index": 4},
        {"type": "message_delta", "delta": {"stop_reason": "pause_turn"}},
        {"type": "message_stop"},
    ], captured)

    events = [event async for event in anthropic_messages._stream_impl(
        Model(id="claude-test", base_url="https://upstream.invalid"),
        Context(),
        StreamOptions(),
        AuthResult(api_key="test"),
    )]

    deltas = [event for event in events if event.type == "text_delta"]
    assert [(event.index, event.delta) for event in deltas] == [(0, "hello")]
    assert events[-1].type == "done"
    assert events[-1].message is not None
    assert events[-1].message.content == (TextContent(text="hello"),)
    assert events[-1].message.stop_reason == "stop"


async def test_normalized_anthropic_preserves_redacted_thinking_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    _mock_anthropic(monkeypatch, [
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "redacted_thinking", "data": "opaque-redacted"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}},
        {"type": "message_stop"},
    ], captured)

    events = [event async for event in anthropic_messages._stream_impl(
        Model(id="claude-test", base_url="https://upstream.invalid"),
        Context(),
        StreamOptions(),
        AuthResult(api_key="test"),
    )]
    message = events[-1].message
    assert message is not None
    assert message.content == (ThinkingContent(thinking="opaque-redacted", redacted=True),)
    done = next(event for event in events if event.type == "thinking_done")
    assert done.thinking == "opaque-redacted"
    assert done.redacted is True


async def test_normalized_anthropic_requires_message_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    _mock_anthropic(monkeypatch, [
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "partial"}},
        {"type": "content_block_stop", "index": 0},
    ], captured)

    events = [event async for event in anthropic_messages._stream_impl(
        Model(id="claude-test", base_url="https://upstream.invalid"),
        Context(),
        StreamOptions(),
        AuthResult(api_key="test"),
    )]

    assert events[-1].type == "error"
    assert not any(event.type == "done" for event in events)
