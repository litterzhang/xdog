from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from xdog.ai.core import AuthResult
from xdog.ai.protocols import openai_responses
from xdog.ai.types import Context, Model, StreamOptions


@pytest.mark.parametrize("cache_key", [None, "", "session-cache-key"])
async def test_responses_forwards_prompt_cache_key_only_when_set(
    monkeypatch: pytest.MonkeyPatch, cache_key: str | None,
) -> None:
    requests: list[dict[str, Any]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/responses"
        requests.append(json.loads(request.content))
        data = {"type": "response.completed", "response": {"id": "resp_test", "status": "completed"}}
        return httpx.Response(
            200, headers={"Content-Type": "text/event-stream"},
            content=f"event: response.completed\ndata: {json.dumps(data)}\n\n".encode(),
        )

    client_type = httpx.AsyncClient

    def client(**kwargs: Any) -> httpx.AsyncClient:
        return client_type(transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(openai_responses.httpx, "AsyncClient", client)
    events = [event async for event in openai_responses._stream_impl(
        Model(id="test", base_url="https://upstream.invalid"), Context(),
        StreamOptions(prompt_cache_key=cache_key), AuthResult(api_key="test"),
    )]
    assert events[-1].type == "done"
    assert len(requests) == 1
    if cache_key is None:
        assert "prompt_cache_key" not in requests[0]
    else:
        assert requests[0]["prompt_cache_key"] == cache_key
