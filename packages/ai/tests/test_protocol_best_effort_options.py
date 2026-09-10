from __future__ import annotations

import json
import sys
from collections.abc import AsyncIterator
from types import ModuleType, SimpleNamespace
from typing import Any

import httpx
import pytest
from xdog.ai.core import AuthResult
from xdog.ai.protocols import openai_completions, openai_responses
from xdog.ai.types import (
    Context,
    JsonSchemaFormat,
    Model,
    StreamOptions,
    Tool,
    ToolChoice,
)


def _options() -> StreamOptions:
    return StreamOptions(
        top_p=0.75,
        stop_sequences=("STOP", "DONE"),
        tool_choice=ToolChoice(type="tool", name="weather"),
        response_format=JsonSchemaFormat.from_schema(
            {"type": "object", "properties": {"answer": {"type": "string"}}},
            name="answer",
            description="Structured answer",
            strict=True,
        ),
        metadata=(("user_id", "user-1"),),
        service_tier="auto",
    )


async def test_chat_completions_maps_best_effort_controls(monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[dict[str, Any]] = []

    async def chunks() -> AsyncIterator[Any]:
        yield SimpleNamespace(id="completion_test", choices=[SimpleNamespace(finish_reason="stop", delta=None)])

    async def create(**kwargs: Any) -> AsyncIterator[Any]:
        requests.append(kwargs)
        return chunks()

    sdk = ModuleType("openai")
    monkeypatch.setattr(sdk, "AsyncOpenAI", lambda **kwargs: SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
    ), raising=False)
    monkeypatch.setitem(sys.modules, "openai", sdk)

    stream = openai_completions.stream(
        Model(id="test", base_url="https://upstream.invalid"),
        Context(tools=(Tool(name="weather"),)),
        _options(),
        AuthResult(api_key="test"),
    )
    events = [event async for event in stream]

    assert events[-1].type == "done"
    body = requests[0]
    assert body["top_p"] == 0.75
    assert body["stop"] == ["STOP", "DONE"]
    assert body["tool_choice"] == {"type": "function", "function": {"name": "weather"}}
    assert body["response_format"] == {
        "type": "json_schema",
        "json_schema": {
            "name": "answer",
            "description": "Structured answer",
            "schema": {"type": "object", "properties": {"answer": {"type": "string"}}},
            "strict": True,
        },
    }
    assert body["metadata"] == {"user_id": "user-1"}
    assert body["service_tier"] == "auto"


async def test_responses_maps_best_effort_controls(monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[dict[str, Any]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        data = {"type": "response.completed", "response": {"id": "resp_test", "status": "completed"}}
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=f"event: response.completed\ndata: {json.dumps(data)}\n\n".encode(),
        )

    client_type = httpx.AsyncClient

    def client(**kwargs: Any) -> httpx.AsyncClient:
        return client_type(transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(openai_responses.httpx, "AsyncClient", client)
    events = [event async for event in openai_responses._stream_impl(
        Model(id="test", base_url="https://upstream.invalid"),
        Context(tools=(Tool(name="weather"),)),
        _options(),
        AuthResult(api_key="test"),
    )]

    assert events[-1].type == "done"
    body = requests[0]
    assert body["top_p"] == 0.75
    assert "stop" not in body
    assert body["tool_choice"] == {"type": "function", "name": "weather"}
    assert body["text"]["format"] == {
        "type": "json_schema",
        "name": "answer",
        "description": "Structured answer",
        "schema": {"type": "object", "properties": {"answer": {"type": "string"}}},
        "strict": True,
    }
    assert body["metadata"] == {"user_id": "user-1"}
    assert body["service_tier"] == "auto"


@pytest.mark.parametrize("kind,chat,responses", [
    ("auto", "auto", "auto"),
    ("any", "required", "required"),
    ("none", "none", "none"),
])
def test_tool_choice_wire_values(kind: str, chat: str, responses: str) -> None:
    choice = ToolChoice(type=kind)  # type: ignore[arg-type]
    assert openai_completions._tool_choice(choice) == chat
    assert openai_responses._tool_choice(choice) == responses


async def test_responses_clamps_reasoning_effort_to_model_support(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[dict[str, Any]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        data = {"type": "response.completed", "response": {"id": "resp_test", "status": "completed"}}
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=f"event: response.completed\ndata: {json.dumps(data)}\n\n".encode(),
        )

    client_type = httpx.AsyncClient

    def client(**kwargs: Any) -> httpx.AsyncClient:
        return client_type(transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(openai_responses.httpx, "AsyncClient", client)
    options = StreamOptions(thinking="xhigh")
    events = [event async for event in openai_responses._stream_impl(
        Model(
            id="test",
            base_url="https://upstream.invalid",
            reasoning=True,
            supported_efforts=("low", "medium", "high"),
        ),
        Context(),
        options,
        AuthResult(api_key="test"),
    )]

    assert events[-1].type == "done"
    assert requests[0]["reasoning"]["effort"] == "high"
