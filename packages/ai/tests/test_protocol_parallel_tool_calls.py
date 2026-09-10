from __future__ import annotations

import json
import sys
from collections.abc import AsyncIterator
from types import ModuleType, SimpleNamespace
from typing import Any

import httpx
import pytest
from xdog.ai.core import AuthResult
from xdog.ai.protocols import anthropic_messages, openai_completions, openai_responses
from xdog.ai.types import Context, Model, StreamOptions, TextVerbosity, Tool


@pytest.mark.parametrize("protocol", ["openai-responses", "anthropic-messages", "openai-completions"])
@pytest.mark.parametrize("parallel", [None, False, True])
@pytest.mark.parametrize("with_tools", [False, True])
@pytest.mark.parametrize("verbosity", [None, "low", "medium", "high"])
async def test_parallel_tool_calls_wire_mapping(
    monkeypatch: pytest.MonkeyPatch, protocol: str, parallel: bool | None, with_tools: bool,
    verbosity: TextVerbosity | None,
) -> None:
    requests: list[dict[str, Any]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        if protocol == "openai-responses":
            assert request.url.path == "/v1/responses"
            frames = [{"type": "response.completed", "response": {"id": "resp_test", "status": "completed"}}]
        else:
            assert request.url.path == "/v1/messages"
            frames = [
                {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
                {"type": "content_block_stop", "index": 0},
                {"type": "message_delta", "delta": {"stop_reason": "end_turn"}},
                {"type": "message_stop"},
            ]
        return httpx.Response(
            200, headers={"Content-Type": "text/event-stream"},
            content="".join(f"event: {frame['type']}\ndata: {json.dumps(frame)}\n\n" for frame in frames).encode(),
        )

    client_type = httpx.AsyncClient

    def client(**kwargs: Any) -> httpx.AsyncClient:
        return client_type(transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)

    async def chunks() -> AsyncIterator[Any]:
        yield SimpleNamespace(id="completion_test", choices=[SimpleNamespace(finish_reason="stop", delta=None)])

    async def create(**kwargs: Any) -> AsyncIterator[Any]:
        requests.append(kwargs)
        return chunks()

    # The Chat Completions adapter uses the optional OpenAI SDK. A small stub
    # exercises its actual request builder without requiring that dependency.
    sdk = ModuleType("openai")
    monkeypatch.setattr(sdk, "AsyncOpenAI", lambda **kwargs: SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
    ), raising=False)
    monkeypatch.setitem(sys.modules, "openai", sdk)

    model = Model(id="test", api=protocol, base_url="https://upstream.invalid")
    context = Context(tools=(Tool(name="weather"),) if with_tools else None)
    options = StreamOptions(parallel_tool_calls=parallel, verbosity=verbosity)
    auth = AuthResult(api_key="test")
    if protocol == "openai-completions":
        events = [event async for event in openai_completions.stream(model, context, options, auth)]
    else:
        adapter = openai_responses if protocol == "openai-responses" else anthropic_messages
        events = [event async for event in adapter._stream_impl(model, context, options, auth)]
    assert events[-1].type == "done"
    assert len(requests) == 1
    body = requests[0]
    if protocol == "anthropic-messages":
        assert "parallel_tool_calls" not in body
        if parallel is None or not with_tools:
            assert "tool_choice" not in body
        else:
            assert body["tool_choice"] == {"type": "auto", "disable_parallel_tool_use": not parallel}
    elif parallel is None or (protocol == "openai-completions" and not with_tools):
        assert "parallel_tool_calls" not in body
    else:
        assert body["parallel_tool_calls"] is parallel

    if verbosity is None or protocol == "anthropic-messages":
        assert "text" not in body and "verbosity" not in body
    elif protocol == "openai-responses":
        assert body["text"] == {"verbosity": verbosity}
        assert "verbosity" not in body
    else:
        assert body["verbosity"] == verbosity
        assert "text" not in body
