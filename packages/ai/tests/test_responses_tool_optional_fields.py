"""Responses function tools preserve optional action fields on the actual HTTP wire."""
from __future__ import annotations

import copy
import json
from typing import Any

import httpx
import pytest
from xdog.ai.core import AuthResult
from xdog.ai.protocols import openai_completions, openai_responses
from xdog.ai.types import Context, Model, OpenAICompletionsCompat, StreamOptions, Tool


def action_schema() -> dict[str, Any]:
    # Domain-neutral action dispatch: create needs title, inspect/update need id.
    # These branch-specific fields must not all become globally required.
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["action"],
        "properties": {
            "action": {"type": "string", "enum": ["create", "inspect", "update"]},
            "title": {"type": "string"},
            "id": {"type": "string"},
            "config": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "options": {
                        "type": "object", "additionalProperties": False, "required": [],
                        "properties": {"verbose": {"type": "boolean"}},
                    },
                },
            },
            "evidence": {
                "type": "array",
                "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["path"],
                    "properties": {"path": {"type": "string"}, "note": {"type": "string"}},
                },
            },
        },
    }


@pytest.fixture
def wire_requests(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    requests: list[dict[str, Any]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url == "https://upstream.invalid/v1/responses"
        requests.append(json.loads(request.content))
        frame = {"type": "response.completed", "response": {"id": "resp_test", "status": "completed"}}
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            content=f"event: response.completed\ndata: {json.dumps(frame)}\n\n".encode(),
        )

    client_type = httpx.AsyncClient

    def client(**kwargs: Any) -> httpx.AsyncClient:
        return client_type(transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    return requests


async def send_responses_request(tools: tuple[Tool, ...]) -> None:
    # Exercise the real builder, JSON serialization and HTTP stream, not just
    # _convert_tools; MockTransport is the only replacement at the wire boundary.
    events = [event async for event in openai_responses._stream_impl(
        Model(id="test", api="openai-responses", base_url="https://upstream.invalid"),
        Context(tools=tools), StreamOptions(), AuthResult(api_key="test"),
    )]
    assert events[-1].type == "done"
    assert not any(event.type == "error" for event in events)


@pytest.mark.parametrize("required", [["action"], [], None], ids=["action-required", "none-required", "omitted"])
async def test_responses_wire_disables_strict_without_rewriting_optional_schema(wire_requests, required):
    schema = action_schema()
    if required is None:
        del schema["required"]
    else:
        schema["required"] = required
    original = copy.deepcopy(schema)
    tool = Tool(name="work_items", description="Manage work using an explicit action", parameters=schema)

    await send_responses_request((tool,))

    assert len(wire_requests) == 1
    wire = wire_requests[0]["tools"]
    assert len(wire) == 1
    assert wire[0]["strict"] is False  # Absent/None is not sufficient for Responses.
    assert wire[0] == {
        "type": "function", "name": tool.name, "description": tool.description,
        "strict": False, "parameters": original,
    }
    parameters = wire[0]["parameters"]
    assert parameters.get("required") == required
    assert set(parameters["properties"]) == {"action", "title", "id", "config", "evidence"}
    config = parameters["properties"]["config"]
    assert "required" not in config and "additionalProperties" not in config
    assert config["properties"]["options"]["required"] == []
    evidence = parameters["properties"]["evidence"]["items"]
    assert evidence["required"] == ["path"] and "note" in evidence["properties"]
    assert schema == original and tool.parameters == original


@pytest.mark.parametrize("schema", [
    {},
    {"type": "object", "properties": {}},
    {"type": "object", "properties": {}, "required": []},
], ids=["default-empty-schema", "empty-object", "empty-required"])
async def test_responses_empty_schema_tools_are_also_explicitly_non_strict(wire_requests, schema):
    original = copy.deepcopy(schema)
    tool = Tool(name="ping", parameters=schema)

    await send_responses_request((tool, Tool(name="default_ping")))

    assert len(wire_requests) == 1
    empty, default = wire_requests[0]["tools"]
    assert empty == {
        "type": "function", "name": "ping", "description": "", "strict": False,
        "parameters": original or {"type": "object", "properties": {}},
    }
    assert default == {
        "type": "function", "name": "default_ping", "description": "", "strict": False,
        "parameters": {"type": "object", "properties": {}},
    }
    assert empty["strict"] is False and default["strict"] is False
    assert schema == original and tool.parameters == original


async def test_responses_reused_shared_schema_is_not_mutated_across_requests(wire_requests):
    schema = action_schema()
    original = copy.deepcopy(schema)
    tools = (Tool(name="work_items", parameters=schema), Tool(name="helpers", parameters=schema))

    for _ in range(2):
        await send_responses_request(tools)
        assert schema == original
        assert all(tool.parameters == original for tool in tools)

    assert len(wire_requests) == 2 and wire_requests[0] == wire_requests[1]
    for request in wire_requests:
        assert [tool["name"] for tool in request["tools"]] == ["work_items", "helpers"]
        assert all(tool["strict"] is False and tool["parameters"] == original for tool in request["tools"])


@pytest.mark.parametrize("supports_strict_mode", [False, True])
def test_chat_completions_existing_optional_schema_mapping_is_unchanged(supports_strict_mode):
    schema = action_schema()
    original = copy.deepcopy(schema)
    tool = Tool(name="work_items", description="Manage work", parameters=schema)
    body = openai_completions.context_to_openai(
        Context(tools=(tool,)),
        Model(id="test", api="openai-completions",
              compat=OpenAICompletionsCompat(supports_strict_mode=supports_strict_mode)),
    )
    expected = {"name": tool.name, "description": tool.description, "parameters": original}
    if supports_strict_mode:
        expected["strict"] = False
    assert body["tools"] == [{"type": "function", "function": expected}]
    if supports_strict_mode:
        assert body["tools"][0]["function"]["strict"] is False
    assert schema == original and tool.parameters == original
