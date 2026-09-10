"""Contract tests for the inbound Responses facade, with no upstream credentials."""
from __future__ import annotations

import asyncio
import json
import ssl
from collections.abc import AsyncIterator
from typing import Any

import pytest
from xdog.ai.proxy import _handle_connection
from xdog.ai.proxy_responses import InvalidRequest, format_response, parse_request, stream_to_sse
from xdog.ai.types import (
    AssistantMessage,
    AuthExpiredError,
    DoneEvent,
    ErrorEvent,
    ImageContent,
    StartEvent,
    TextContent,
    TextDeltaEvent,
    TextDoneEvent,
    TextStartEvent,
    ThinkingContent,
    ThinkingDeltaEvent,
    ThinkingDoneEvent,
    ThinkingStartEvent,
    ToolCall,
    ToolCallDeltaEvent,
    ToolCallDoneEvent,
    ToolCallStartEvent,
    ToolResultMessage,
    Usage,
    UserMessage,
)


def test_parse_string_and_options() -> None:
    model, context, options, streaming = parse_request({
        "model": "copilot/test", "input": "Hello", "instructions": "Be concise", "stream": True,
        "max_output_tokens": 42, "temperature": 0.5, "reasoning": {"effort": "high"}, "store": False,
        "tools": [{"type": "function", "name": "weather", "parameters": {"type": "object"}}],
    })
    assert model == "copilot/test"
    assert context.messages == (UserMessage(content="Hello"),)
    assert context.system_prompt == "Be concise"
    assert context.tools and context.tools[0].name == "weather"
    assert options.max_tokens == 42 and options.temperature == 0.5 and options.thinking == "high"
    assert streaming


def test_parse_full_history_merges_parallel_calls() -> None:
    reasoning = {"type": "reasoning", "id": "rs_1", "encrypted_content": "opaque",
                 "summary": [{"type": "summary_text", "text": "Plan"}]}
    _, context, _, _ = parse_request({"model": "test", "input": [
        {"role": "system", "content": "System"},
        {"role": "developer", "content": [{"type": "input_text", "text": "Developer"}]},
        {"role": "user", "content": [
            {"type": "input_text", "text": "Look"},
            {"type": "input_image", "image_url": "data:image/png;base64,aGVsbG8="},
        ]},
        reasoning,
        {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Checking"}]},
        {"type": "function_call", "call_id": "call_a", "name": "a", "arguments": '{"x": 1}'},
        {"type": "function_call", "call_id": "call_b", "name": "b", "arguments": '{}'},
        {"type": "function_call_output", "call_id": "call_a", "output": "A"},
        {"type": "function_call_output", "call_id": "call_b", "output": [{"type": "input_text", "text": "B"}]},
    ]})
    assert context.system_prompt == "System\n\nDeveloper"
    assert len(context.messages) == 4
    user, assistant, result_a, result_b = context.messages
    assert isinstance(user, UserMessage) and user.content[1] == ImageContent(data="aGVsbG8=", mime_type="image/png")
    assert isinstance(assistant, AssistantMessage) and len(assistant.content) == 4
    thinking = assistant.content[0]
    assert isinstance(thinking, ThinkingContent) and json.loads(thinking.thinking_signature or "{}") == reasoning
    assert isinstance(result_a, ToolResultMessage) and result_a.tool_call_id == "call_a"
    assert isinstance(result_b, ToolResultMessage) and result_b.content == (TextContent(text="B"),)
    # In particular, the outbound adapter must not synthesize cancellation for call_a.
    from xdog.ai.protocols.openai_responses import context_to_responses_input
    from xdog.ai.types import Model
    wire = context_to_responses_input(context, Model())
    assert [i["output"] for i in wire if i.get("type") == "function_call_output"] == ["A", "B"]


@pytest.mark.parametrize("patch", [
    {"unknown_option": True}, {"metadata": {"key": 1}}, {"model": ""}, {"input": None}, {"input": {}},
    {"stream": "true"}, {"store": True}, {"background": True},
    {"previous_response_id": "resp_old"}, {"conversation": "conv_old"},
    {"max_output_tokens": 0}, {"max_output_tokens": True}, {"temperature": float("nan")}, {"temperature": 3},
    {"tools": [{"type": "web_search"}]}, {"tools": [{"type": "function", "name": "f", "strict": True}]},
    {"tool_choice": "required"}, {"parallel_tool_calls": "false"}, {"top_p": 0.5},
    {"text": {"format": {"type": "json_schema"}}}, {"reasoning": {"effort": "invalid"}}, {"reasoning": []},
    {"tools": [{"type": "function", "name": "f", "parameters": []}]},
    {"input": [{"type": "item_reference", "id": "msg_old"}]},
    {"input": [{"type": "function_call", "call_id": "a", "name": "f", "arguments": "not json"}]},
    {"input": [{"type": "function_call", "call_id": "a", "name": "f", "arguments": "[]"}]},
    {"input": [{"role": "user", "content": [{"type": "input_image", "image_url": "https://example.com/a.png"}]}]},
    {"input": [{"role": "user", "content": [{"type": "input_file", "file_id": "file_a"}]}]},
])
def test_reject_unsupported_or_malformed_requests(patch: dict[str, Any]) -> None:
    with pytest.raises(InvalidRequest):
        parse_request({"model": "test", "input": "hello", **patch})


def test_response_format_and_round_trip() -> None:
    msg = AssistantMessage(content=(
        ThinkingContent(thinking="Plan", thinking_signature=json.dumps({
            "type": "reasoning", "id": "rs_upstream", "encrypted_content": "opaque",
        })),
        TextContent(text="Hello"),
        ToolCall(id="call_a|fc_upstream", name="weather", arguments={"city": "Paris"}),
    ), stop_reason="toolUse", usage=Usage(input=10, output=4, cache_read=6, cache_write=2))
    result = format_response(msg, "test")
    assert result["object"] == "response" and result["status"] == "completed"
    assert result["output"][0]["encrypted_content"] == "opaque"
    assert result["output"][2]["id"] == "fc_upstream"
    assert result["output"][2]["call_id"] == "call_a"
    assert result["usage"] == {
        "input_tokens": 18, "output_tokens": 4, "total_tokens": 22,
        "input_tokens_details": {"cached_tokens": 6}, "output_tokens_details": {"reasoning_tokens": 0},
    }
    _, context, _, _ = parse_request({"model": "test", "input": result["output"]})
    assert len(context.messages) == 1
    assert isinstance(context.messages[0], AssistantMessage)
    assert context.messages[0].content[-1] == ToolCall(id="call_a", name="weather", arguments={"city": "Paris"})


def test_does_not_expose_anthropic_signatures_or_redacted_thinking() -> None:
    result = format_response(AssistantMessage(content=(
        ThinkingContent(thinking="secret", redacted=True, thinking_signature="anthropic-signature"),
    )), "test")
    assert result["output"][0]["summary"] == []
    assert "encrypted_content" not in result["output"][0]


async def _events(events: list[Any]) -> AsyncIterator[Any]:
    for event in events:
        if isinstance(event, Exception):
            raise event
        yield event


def _decode_sse(body: bytes) -> list[dict[str, Any]]:
    events = []
    for chunk in body.decode().strip().split("\n\n"):
        name, data = chunk.split("\n", 1)
        payload = json.loads(data.removeprefix("data: "))
        assert name == f"event: {payload['type']}"
        events.append(payload)
    assert [e["sequence_number"] for e in events] == list(range(len(events)))
    return events


async def _stream(events: list[Any]) -> list[dict[str, Any]]:
    return _decode_sse(b"".join([chunk async for chunk in stream_to_sse(_events(events), "test")]))


async def test_stream_text_reasoning_and_parallel_calls() -> None:
    signature = json.dumps({"type": "reasoning", "id": "rs_upstream", "encrypted_content": "opaque"})
    final = AssistantMessage(content=(
        ThinkingContent(thinking="Plan", thinking_signature=signature), TextContent(text="Hello"),
        ToolCall(id="call_a|fc_a", name="a", arguments={"x": 1}),
        ToolCall(id="call_b|fc_b", name="b", arguments={}),
    ), stop_reason="toolUse", usage=Usage(input=10, output=5, cache_read=2))
    events = await _stream([
        StartEvent(), ThinkingStartEvent(index=0), ThinkingDeltaEvent(index=0, delta="Plan"),
        ThinkingDoneEvent(index=0, thinking="Plan", thinking_signature=signature),
        TextStartEvent(index=1), TextDeltaEvent(index=1, delta="Hel"), TextDeltaEvent(index=1, delta="lo"),
        TextDoneEvent(index=1, text="Hello"),
        ToolCallStartEvent(index=2, id="call_a|fc_a", name="a"),
        ToolCallStartEvent(index=3, id="call_b|fc_b", name="b"),
        ToolCallDeltaEvent(index=2, delta='{"x":'), ToolCallDeltaEvent(index=3, delta='{}'),
        ToolCallDeltaEvent(index=2, delta=' 1}'),
        ToolCallDoneEvent(index=3, id="call_b|fc_b", name="b", arguments={}),
        ToolCallDoneEvent(index=2, id="call_a|fc_a", name="a", arguments={"x": 1}),
        DoneEvent(message=final),
    ])
    assert [e["type"] for e in events[:2]] == ["response.created", "response.in_progress"]
    assert events[0]["response"]["output"] == [] and events[0]["response"]["usage"] is None
    terminal = events[-1]
    assert terminal["type"] == "response.completed"
    response = terminal["response"]
    assert response["id"] == events[0]["response"]["id"]
    assert response["created_at"] == events[0]["response"]["created_at"]
    assert response["usage"]["total_tokens"] == 17
    assert response["output"][0]["encrypted_content"] == "opaque"
    assert response["output"][0]["id"] == "rs_upstream"
    assert response["output"][1]["content"][0]["text"] == "Hello"
    assert response["output"][2]["arguments"] == '{"x": 1}'
    added = [e for e in events if e["type"] == "response.output_item.added"]
    done = [e for e in events if e["type"] == "response.output_item.done"]
    assert len(added) == len(done) == 4
    for event in added + done:
        assert event["item"]["id"] == response["output"][event["output_index"]]["id"]
    for event in done:
        assert event["item"] == response["output"][event["output_index"]]


@pytest.mark.parametrize("reason,status", [("stop", "completed"), ("length", "incomplete"), ("aborted", "failed")])
async def test_stream_final_only_and_stop_reasons(reason: Any, status: str) -> None:
    msg = AssistantMessage(content=(TextContent(text="Hello"),), stop_reason=reason)
    events = await _stream([DoneEvent(message=msg)])
    assert events[-1]["type"] == f"response.{status}"
    assert events[-1]["response"]["output"][0]["content"][0]["text"] == "Hello"
    assert any(e["type"] == "response.output_text.delta" and e["delta"] == "Hello" for e in events)
    if reason == "length":
        assert events[-1]["response"]["incomplete_details"] == {"reason": "max_output_tokens"}
    assert format_response(msg, "test")["status"] == status


@pytest.mark.parametrize("ending", [[], [ErrorEvent(error="upstream failed")], [ssl.SSLError("broken")],
                                    [ConnectionResetError("reset")]])
async def test_stream_failure_and_premature_eof(ending: list[Any]) -> None:
    events = await _stream([TextStartEvent(), TextDeltaEvent(delta="Partial"), *ending])
    assert events[-1]["type"] == "response.failed"
    assert events[-1]["response"]["error"]["message"]
    assert events[-1]["response"]["output"][0]["content"][0]["text"] == "Partial"
    assert not any(e["type"] == "response.completed" for e in events)


class Provider:
    def __init__(self, events: list[Any] | None = None) -> None:
        self.result = AssistantMessage(content=(TextContent(text="Hello"),))
        self.events = events if events is not None else [DoneEvent(message=self.result)]
        self.calls: list[Any] = []
        self.failures = 0

    def stream(self, model: str, context: Any, options: Any) -> AsyncIterator[Any]:
        self.calls.append((model, context, options))
        if self.failures:
            self.failures -= 1
            return _events([ssl.SSLError("initial transport failure")])
        return _events(self.events)

    async def complete(self, model: str, context: Any, options: Any) -> AssistantMessage:
        self.calls.append((model, context, options))
        return self.result


async def _request(provider: Provider, body: Any, *, key: str = "", auth: str = "") -> tuple[int, bytes, bytes]:
    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await _handle_connection(reader, writer, provider, api_key=key)

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", server.sockets[0].getsockname()[1])
        payload = body if isinstance(body, bytes) else json.dumps(body).encode()
        writer.write((
            "POST /v1/responses?test=1 HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\n"
            f"Authorization: Bearer {auth}\r\nContent-Length: {len(payload)}\r\n\r\n"
        ).encode() + payload)
        await writer.drain()
        raw = await asyncio.wait_for(reader.read(), 3)
        writer.close()
        await writer.wait_closed()
    finally:
        server.close()
        await server.wait_closed()
    headers, _, payload = raw.partition(b"\r\n\r\n")
    return int(headers.split()[1]), headers, payload


@pytest.mark.parametrize("stream", [False, True])
async def test_http_success_and_bearer_auth(stream: bool) -> None:
    provider = Provider()
    status, headers, body = await _request(provider, {"model": "test", "input": "Hi", "stream": stream},
                                           key="secret", auth="secret")
    assert status == 200
    assert len(provider.calls) == 1 and provider.calls[0][1].messages == (UserMessage(content="Hi"),)
    if stream:
        assert b"text/event-stream" in headers
        assert _decode_sse(body)[-1]["type"] == "response.completed"
    else:
        assert json.loads(body)["output"][0]["content"][0]["text"] == "Hello"


@pytest.mark.parametrize("body", [b"{", b"\xff", [], None, {"model": "test", "input": "Hi", "store": True}])
async def test_http_bad_request_does_not_call_provider(body: Any) -> None:
    provider = Provider()
    status, _, payload = await _request(provider, body)
    assert status == 400
    assert json.loads(payload)["error"]["type"] == "invalid_request_error"
    assert not provider.calls


async def test_http_auth_failure() -> None:
    provider = Provider()
    status, _, body = await _request(provider, {"model": "test", "input": "Hi"}, key="secret", auth="wrong")
    assert status == 401 and json.loads(body)["error"]["type"] == "authentication_error"
    assert not provider.calls


@pytest.mark.parametrize("events,status", [
    ([], 502), ([ErrorEvent(error='HTTP 429: {"error":{"message":"slow down","type":"rate_limit_error"}}')], 429),
    ([AuthExpiredError("Copilot", "xdog-ai login copilot")], 401),
])
async def test_http_initial_errors(events: list[Any], status: int) -> None:
    result_status, headers, body = await _request(Provider(events), {"model": "test", "input": "Hi", "stream": True})
    assert result_status == status and b"text/event-stream" not in headers
    payload = json.loads(body)
    assert payload["error"]["message"] and "type" not in payload


async def test_http_initial_retry_and_exhaustion() -> None:
    provider = Provider()
    provider.failures = 1
    status, _, body = await _request(provider, {"model": "test", "input": "Hi", "stream": True})
    assert status == 200 and len(provider.calls) == 2
    assert _decode_sse(body)[-1]["type"] == "response.completed"
    provider = Provider()
    provider.failures = 3
    status, _, body = await _request(provider, {"model": "test", "input": "Hi", "stream": True})
    assert status == 502 and len(provider.calls) == 3
    assert json.loads(body)["error"]["type"] == "server_error"


async def test_reasoning_signature_from_partial_snapshot() -> None:
    msg = AssistantMessage(content=(ThinkingContent(
        thinking="Plan", thinking_signature=json.dumps({
            "type": "reasoning", "id": "rs_upstream", "encrypted_content": "opaque",
        }),
    ),))
    events = await _stream([
        ThinkingStartEvent(), ThinkingDeltaEvent(delta="Plan"),
        ThinkingDoneEvent(thinking="Plan", partial=msg), DoneEvent(message=msg),
    ])
    done = next(e for e in events if e["type"] == "response.output_item.done")
    assert done["item"]["encrypted_content"] == "opaque"
    assert done["item"]["id"] == "rs_upstream"
    assert events[-1]["response"]["output"][0] == done["item"]


async def test_http_non_streaming_error_preserves_upstream_status() -> None:
    provider = Provider()
    provider.result = AssistantMessage(stop_reason="error", error_message=(
        'HTTP 400: {"error":{"message":"bad model","type":"invalid_request_error"}}'
    ))
    status, _, body = await _request(provider, {"model": "test", "input": "Hi"})
    assert status == 400
    assert json.loads(body)["error"]["message"] == "bad model"


async def test_http_failure_after_headers_uses_responses_terminal() -> None:
    provider = Provider([TextStartEvent(), TextDeltaEvent(delta="Hi"), ConnectionResetError("upstream reset")])
    status, _, body = await _request(provider, {"model": "test", "input": "Hi", "stream": True})
    assert status == 200
    assert _decode_sse(body)[-1]["type"] == "response.failed"
    assert len(provider.calls) == 1  # Never retry after emitting content.


@pytest.mark.parametrize("stream", [False, True])
async def test_http_response_echoes_supported_options(stream: bool) -> None:
    request = {
        "model": "test", "input": "Hi", "stream": stream, "instructions": "Be brief",
        "max_output_tokens": 20, "tools": [{"type": "function", "name": "weather"}],
        "metadata": {"test": "value"}, "reasoning": {"effort": "low"},
    }
    status, _, body = await _request(Provider(), request)
    assert status == 200
    response = _decode_sse(body)[-1]["response"] if stream else json.loads(body)
    for key in ("model", "instructions", "max_output_tokens", "tools", "metadata", "reasoning"):
        assert response[key] == request[key]
    assert response["store"] is False


async def test_stream_error_preserves_reported_usage() -> None:
    msg = AssistantMessage(content=(TextContent(text="Hi"),), usage=Usage(input=10, output=2))
    events = await _stream([TextStartEvent(), TextDeltaEvent(delta="Hi"), ErrorEvent(error="failed", message=msg)])
    assert events[-1]["response"]["usage"]["total_tokens"] == 12


@pytest.mark.parametrize("metadata", [None, {}, {
    "session_id": "test-session", "turn_metadata": {"turn": 1}, "opaque": "x" * 1024,
}])
@pytest.mark.parametrize("stream", [False, True])
async def test_client_metadata_is_accepted_but_not_forwarded(metadata: Any, stream: bool) -> None:
    request = {"model": "test", "input": "Hi", "stream": stream, "client_metadata": metadata}
    expected = parse_request({key: value for key, value in request.items() if key != "client_metadata"})
    assert parse_request(request) == expected
    provider = Provider()
    status, _, body = await _request(provider, request)
    assert status == 200
    assert provider.calls == [(expected[0], expected[1], expected[2])]
    response = _decode_sse(body)[-1]["response"] if stream else json.loads(body)
    assert "client_metadata" not in response
    assert response["metadata"] == {}


@pytest.mark.parametrize("metadata", ["invalid", [], 1, True])
async def test_malformed_client_metadata_returns_validation_error(metadata: Any) -> None:
    provider = Provider()
    status, _, body = await _request(provider, {"model": "test", "input": "Hi", "client_metadata": metadata})
    assert status == 400
    assert json.loads(body)["error"]["param"] == "client_metadata"
    assert not provider.calls


@pytest.mark.parametrize("cache_key", [None, "", "session-cache-key"])
@pytest.mark.parametrize("stream", [False, True])
async def test_prompt_cache_key_with_client_metadata(cache_key: str | None, stream: bool) -> None:
    provider = Provider()
    status, _, body = await _request(provider, {
        "model": "test", "input": "Hi", "stream": stream, "store": False,
        "client_metadata": {"session_id": "test-session"}, "prompt_cache_key": cache_key,
    })
    assert status == 200
    assert len(provider.calls) == 1
    _, context, options = provider.calls[0]
    assert options.prompt_cache_key == cache_key
    assert context.messages == (UserMessage(content="Hi"),)
    response = _decode_sse(body)[-1]["response"] if stream else json.loads(body)
    assert response["prompt_cache_key"] == cache_key
    assert "client_metadata" not in response


@pytest.mark.parametrize("cache_key", [1, True, [], {}])
async def test_malformed_prompt_cache_key_returns_validation_error(cache_key: Any) -> None:
    provider = Provider()
    status, _, body = await _request(provider, {"model": "test", "input": "Hi", "prompt_cache_key": cache_key})
    assert status == 400
    assert json.loads(body)["error"]["param"] == "prompt_cache_key"
    assert not provider.calls


@pytest.mark.parametrize("parallel", [False, True])
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("verbosity", ["low", "medium", "high"])
async def test_coding_client_options_together(parallel: bool, stream: bool, verbosity: str) -> None:
    provider = Provider()
    status, _, body = await _request(provider, {
        "model": "test", "input": "Hi", "stream": stream, "store": False,
        "client_metadata": {"session_id": "test-session"}, "prompt_cache_key": "session-cache-key",
        "parallel_tool_calls": parallel, "text": {"verbosity": verbosity},
        "tools": [{"type": "function", "name": "weather", "parameters": {"type": "object"}}],
    })
    assert status == 200
    assert len(provider.calls) == 1
    _, context, options = provider.calls[0]
    assert options.parallel_tool_calls is parallel
    assert options.verbosity == verbosity
    assert options.prompt_cache_key == "session-cache-key"
    assert context.tools and context.tools[0].name == "weather"
    if stream:
        responses = [e["response"] for e in _decode_sse(body) if "response" in e]
        assert all(response["parallel_tool_calls"] is parallel for response in responses)
    else:
        responses = [json.loads(body)]
        assert responses[0]["parallel_tool_calls"] is parallel
    assert all(response["text"] == {"format": {"type": "text"}, "verbosity": verbosity} for response in responses)


def test_parallel_tool_calls_default_preserves_upstream_behavior() -> None:
    _, _, options, _ = parse_request({"model": "test", "input": "Hi"})
    assert options.parallel_tool_calls is None
    assert format_response(AssistantMessage(), "test")["parallel_tool_calls"] is True


@pytest.mark.parametrize("parallel", [None, 0, 1, "false", [], {}])
async def test_parallel_tool_calls_requires_boolean(parallel: Any) -> None:
    provider = Provider()
    status, _, body = await _request(provider, {"model": "test", "input": "Hi", "parallel_tool_calls": parallel})
    assert status == 400
    assert json.loads(body)["error"]["param"] == "parallel_tool_calls"
    assert not provider.calls


@pytest.mark.parametrize("text", [None, {}, {"format": {"type": "text"}}, {"verbosity": None}])
def test_unspecified_verbosity_preserves_upstream_default(text: Any) -> None:
    _, _, options, _ = parse_request({"model": "test", "input": "Hi", "text": text})
    assert options.verbosity is None
    _, _, options, _ = parse_request({"model": "test", "input": "Hi"})
    assert options.verbosity is None


@pytest.mark.parametrize("verbosity", ["", "verbose", "LOW", 1, True, [], {}])
async def test_invalid_verbosity_returns_validation_error(verbosity: Any) -> None:
    provider = Provider()
    status, _, body = await _request(provider, {"model": "test", "input": "Hi", "text": {"verbosity": verbosity}})
    assert status == 400
    assert json.loads(body)["error"]["param"] == "text.verbosity"
    assert not provider.calls


def test_verbosity_does_not_bypass_format_validation() -> None:
    with pytest.raises(InvalidRequest) as exc:
        parse_request({"model": "test", "input": "Hi", "text": {
            "verbosity": "low", "format": {"type": "json_schema"},
        }})
    assert exc.value.param == "text.format"


def _additional_tools(*tools: dict[str, Any]) -> dict[str, Any]:
    # Wire shape from openai/codex rust-v0.154.0, core/src/client.rs:
    # build_responses_request() constructs this prefix in Responses Lite mode.
    return {"type": "additional_tools", "id": "at_test", "role": "developer", "tools": list(tools)}


def _function(name: str) -> dict[str, Any]:
    return {"type": "function", "name": name, "description": f"Run {name}", "strict": False,
            "parameters": {"type": "object", "properties": {"value": {"type": "string"}}}}


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("top_level_tools", [None, []])
async def test_codex_responses_lite_additional_tools(stream: bool, top_level_tools: Any) -> None:
    import copy

    weather = _function("weather")
    request = {
        "model": "test", "instructions": "", "tools": top_level_tools,
        "input": [_additional_tools(weather), {"role": "developer", "content": "Be helpful"},
                  {"role": "user", "content": "Hi"}],
        "stream": stream, "store": False, "tool_choice": "auto", "parallel_tool_calls": False,
        "client_metadata": {"session_id": "test"}, "prompt_cache_key": "cache-key", "text": {"verbosity": "low"},
        "reasoning": {"effort": "low", "summary": "auto", "context": "all_turns"},
        "include": ["reasoning.encrypted_content"],
    }
    original = copy.deepcopy(request)
    _, context, options, _ = parse_request(request)
    assert request == original  # Do not mutate input history or its tool schemas.
    assert context.messages == (UserMessage(content=(TextContent(text="Hi"),)),)
    assert context.system_prompt and context.system_prompt.strip() == "Be helpful"
    assert context.tools and len(context.tools) == 1
    assert context.tools[0].name == "weather" and context.tools[0].parameters == weather["parameters"]
    assert options.parallel_tool_calls is False and options.verbosity == "low"
    assert options.prompt_cache_key == "cache-key"
    provider = Provider()
    status, _, body = await _request(provider, request)
    assert status == 200
    assert len(provider.calls) == 1 and provider.calls[0][1] == context
    responses = [e["response"] for e in _decode_sse(body) if "response" in e] if stream else [json.loads(body)]
    assert all(response["tools"] == [weather] for response in responses)


def test_additional_tools_merge_deduplicate_and_preserve_history() -> None:
    from xdog.ai.protocols.anthropic_messages import context_to_anthropic
    from xdog.ai.protocols.openai_responses import _convert_tools, context_to_responses_input
    from xdog.ai.types import Model

    first, second, third = (_function(name) for name in ("first", "second", "third"))
    request = {"model": "test", "tools": [first], "input": [
        _additional_tools(first, second),
        {"role": "user", "content": "Go"},
        {"type": "function_call", "call_id": "call_1", "name": "first", "arguments": "{}"},
        _additional_tools(third),
        {"type": "function_call", "call_id": "call_2", "name": "second", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "call_1", "output": "one"},
        {"type": "function_call_output", "call_id": "call_2", "output": "two"},
    ]}
    _, context, _, _ = parse_request(request)
    assert context.tools and [tool.name for tool in context.tools] == ["first", "second", "third"]
    assert len(context.messages) == 4  # No extra role/message boundary between parallel calls.
    assert isinstance(context.messages[1], AssistantMessage) and len(context.messages[1].content) == 2
    assert [tool["name"] for tool in _convert_tools(context.tools)] == ["first", "second", "third"]
    wire = context_to_responses_input(context, Model())
    assert [item["output"] for item in wire if item.get("type") == "function_call_output"] == ["one", "two"]
    _, _, anthropic_tools = context_to_anthropic(context, Model())
    assert anthropic_tools and [tool["name"] for tool in anthropic_tools] == ["first", "second", "third"]
    assert format_response(AssistantMessage(), "test", request)["tools"] == [first, second, third]


@pytest.mark.parametrize("top_level", [{}, {"tools": None}, {"tools": []}])
def test_empty_additional_tools(top_level: dict[str, Any]) -> None:
    _, context, _, _ = parse_request({"model": "test", "input": [_additional_tools()], **top_level})
    assert context.tools is None and context.messages == ()


@pytest.mark.parametrize("patch,param", [
    ({"tools": None}, "input[0].tools"), ({"tools": {}}, "input[0].tools"),
    ({"role": "user"}, "input[0].role"), ({"role": None}, "input[0].role"),
    ({"tools": [None]}, "input[0].tools[0]"),
    ({"tools": [{"type": "function", "name": ""}]}, "input[0].tools[0].name"),
    ({"tools": [{"type": "function", "name": "f", "parameters": []}]}, "input[0].tools[0].parameters"),
    ({"tools": [{"type": "function", "name": "f", "strict": True}]}, "input[0].tools[0].strict"),
    ({"tools": [{"type": "web_search"}]}, "input[0].tools[0].type"),
])
async def test_invalid_additional_tools_are_not_silently_dropped(patch: dict[str, Any], param: str) -> None:
    provider = Provider()
    status, _, body = await _request(provider, {"model": "test", "input": [{**_additional_tools(), **patch}]})
    assert status == 400
    assert json.loads(body)["error"]["param"] == param
    assert not provider.calls


async def test_conflicting_additional_tools_are_rejected() -> None:
    provider = Provider()
    tool = _function("weather")
    status, _, body = await _request(provider, {"model": "test", "tools": [tool], "input": [
        _additional_tools({**tool, "description": "Different definition"}),
    ]})
    assert status == 400
    assert json.loads(body)["error"]["param"] == "input[0].tools[0].name"
    assert "Conflicting" in json.loads(body)["error"]["message"]
    assert not provider.calls


def _namespace(name: str, *tools: dict[str, Any]) -> dict[str, Any]:
    return {"type": "namespace", "name": name, "description": f"Tools in {name}", "tools": list(tools)}


def _custom(name: str = "apply_patch") -> dict[str, Any]:
    return {"type": "custom", "name": name, "description": "Apply a patch", "format": {
        "type": "grammar", "syntax": "lark", "definition": 'start: "*** Begin Patch" /(.|\\n)*/ "*** End Patch"',
    }}


def test_namespace_names_are_distinct_stable_and_valid_for_upstreams() -> None:
    import copy
    import re

    from xdog.ai.protocols.anthropic_messages import context_to_anthropic
    from xdog.ai.protocols.openai_responses import _convert_tools
    from xdog.ai.types import Model

    specs = [_function("run"), _namespace("first", _function("run")), _namespace("second", _function("run"))]
    request = {"model": "test", "input": [_additional_tools(*specs)]}
    original = copy.deepcopy(request)
    _, context, _, _ = parse_request(request)
    assert context.tools and len(context.tools) == 3
    names = [tool.name for tool in context.tools]
    assert len(set(names)) == 3 and names[0] == "run"
    assert all(re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", name) for name in names)
    assert "first.run" in context.tools[1].description and "Tools in first" in context.tools[1].description
    assert request == original
    # The same namespace/name is stable when another namespace disappears.
    _, smaller, _, _ = parse_request({"model": "test", "input": "Hi", "tools": [specs[1]]})
    assert smaller.tools and smaller.tools[0].name == names[1]
    assert [tool["name"] for tool in _convert_tools(context.tools)] == names
    _, _, anthropic_tools = context_to_anthropic(context, Model())
    assert anthropic_tools and [tool["name"] for tool in anthropic_tools] == names
    assert format_response(AssistantMessage(), "test", request)["tools"] == specs


@pytest.mark.parametrize("custom", [False, True])
@pytest.mark.parametrize("namespaced", [False, True])
@pytest.mark.parametrize("stream", [False, True])
async def test_namespaced_function_and_custom_calls_round_trip(custom: bool, namespaced: bool, stream: bool) -> None:
    raw_input = '*** Begin Patch\n*** Add File: demo.txt\n+"Hello\\world" 雪\n*** End Patch'
    spec = _custom("run") if custom else _function("run")
    if namespaced:
        spec = _namespace("functions", spec)
    request = {
        "model": "test", "input": [_additional_tools(spec), {"role": "user", "content": "Hi"}],
        "tools": None, "stream": stream, "parallel_tool_calls": False, "store": False,
        "client_metadata": {"session_id": "test"}, "prompt_cache_key": "cache", "text": {"verbosity": "low"},
    }
    _, context, _, _ = parse_request(request)
    assert context.tools and len(context.tools) == 1
    internal_name = context.tools[0].name
    arguments = {"input": raw_input} if custom else {"value": "Hello"}
    msg = AssistantMessage(content=(ToolCall(id="call_test|fc_upstream", name=internal_name, arguments=arguments),),
                           stop_reason="toolUse")
    serialized = json.dumps(arguments, ensure_ascii=True)
    provider = Provider([
        ToolCallStartEvent(id="call_test|fc_upstream", name=internal_name),
        # Exercise arbitrary boundaries, including in escapes and Unicode.
        *[ToolCallDeltaEvent(delta=character) for character in serialized],
        ToolCallDoneEvent(id="call_test|fc_upstream", name=internal_name, arguments=arguments),
        DoneEvent(message=msg),
    ])
    provider.result = msg
    status, _, body = await _request(provider, request)
    assert status == 200
    assert b"xdog_ns_" not in body  # Only client-visible identities leave the facade.
    if stream:
        events = _decode_sse(body)
        response = events[-1]["response"]
        assert events[-1]["type"] == "response.completed"
        items = [event["item"] for event in events if event["type"] in (
            "response.output_item.added", "response.output_item.done",
        )]
        assert len(items) == 2 and items[0]["id"] == items[1]["id"] == response["output"][0]["id"]
        assert all(item["name"] == "run" for item in items)
        assert all(item.get("namespace") == ("functions" if namespaced else None) for item in items)
        if custom:
            assert not any("function_call_arguments" in event["type"] for event in events)
            assert "".join(event["delta"] for event in events if event["type"] ==
                           "response.custom_tool_call_input.delta") == raw_input
            assert next(event["input"] for event in events if event["type"] ==
                        "response.custom_tool_call_input.done") == raw_input
        else:
            assert any(event["type"] == "response.function_call_arguments.done" for event in events)
    else:
        response = json.loads(body)
    output = response["output"][0]
    assert output["name"] == "run" and output["call_id"] == "call_test"
    assert output.get("namespace") == ("functions" if namespaced else None)
    if custom:
        assert output["type"] == "custom_tool_call" and output["input"] == raw_input
        assert "arguments" not in output
        assert context.tools[0].parameters["properties"] == {"input": {"type": "string"}}
        assert "lark grammar" in context.tools[0].description
    else:
        assert output["type"] == "function_call" and json.loads(output["arguments"]) == arguments
    assert response["tools"] == [spec]
    result_type = "custom_tool_call_output" if custom else "function_call_output"
    history = [*request["input"], output, {
        "type": result_type, "call_id": "call_test", "name": "run", "output": "Success",
    }]
    _, replay, _, _ = parse_request({**request, "input": history})
    replay_call, replay_result = replay.messages[-2:]
    assert isinstance(replay_call, AssistantMessage)
    assert replay_call.content == (ToolCall(id="call_test", name=internal_name, arguments=arguments),)
    assert isinstance(replay_result, ToolResultMessage)
    assert replay_result.tool_call_id == "call_test" and replay_result.tool_name == internal_name
    # Historical identities still resolve if the tool declaration was removed.
    _, old_history, _, _ = parse_request({"model": "test", "input": history[1:]})
    assert old_history.messages[-2:] == replay.messages[-2:]


def test_repeated_namespace_groups_merge_without_mutation() -> None:
    first = _namespace("functions", _function("first"))
    second = _namespace("functions", _function("first"), _custom("second"))
    request = {"model": "test", "tools": [first], "input": [_additional_tools(second)]}
    _, context, _, _ = parse_request(request)
    assert context.tools and len(context.tools) == 2
    assert format_response(AssistantMessage(), "test", request)["tools"] == [second]
    assert len(first["tools"]) == 1


@pytest.mark.parametrize("reverse", [False, True])
def test_namespace_internal_name_collision_is_rejected(reverse: bool) -> None:
    from xdog.ai.proxy_responses import _tool_name

    specs = [_namespace("functions", _function("run")), _function(_tool_name("run", "functions"))]
    with pytest.raises(InvalidRequest, match="collides"):
        parse_request({"model": "test", "input": "Hi", "tools": list(reversed(specs)) if reverse else specs})


def test_empty_namespace_is_valid() -> None:
    spec = _namespace("functions")
    _, context, _, _ = parse_request({"model": "test", "input": [_additional_tools(spec)]})
    assert context.tools is None


@pytest.mark.parametrize("spec,param", [
    ({"type": "namespace", "tools": []}, "tools[0].name"),
    ({"type": "namespace", "name": "functions", "tools": None}, "tools[0].tools"),
    ({"type": "namespace", "name": "functions", "description": [], "tools": []}, "tools[0].description"),
    (_namespace("functions", _namespace("inner")), "tools[0].tools[0].type"),
    (_namespace("functions", {"type": "function", "name": "run", "parameters": []}), "tools[0].tools[0].parameters"),
    (_namespace("functions", {"type": "function", "name": "run", "strict": True}), "tools[0].tools[0].strict"),
    (_namespace("functions", {"type": "web_search"}), "tools[0].tools[0].type"),
    ({"type": "custom", "name": "run", "format": {"type": "unknown"}}, "tools[0].format.type"),
    ({"type": "custom", "name": "run", "format": {"type": "grammar", "syntax": "python"}}, "tools[0].format.syntax"),
    ({"type": "custom", "name": "run", "format": {"type": "grammar", "syntax": "lark"}}, "tools[0].format.definition"),
])
async def test_namespace_and_custom_validation(spec: dict[str, Any], param: str) -> None:
    provider = Provider()
    status, _, body = await _request(provider, {"model": "test", "input": "Hi", "tools": [spec]})
    assert status == 400 and json.loads(body)["error"]["param"] == param
    assert not provider.calls


@pytest.mark.parametrize("namespace", ["", 1, [], {}])
def test_invalid_namespace_on_history_call(namespace: Any) -> None:
    with pytest.raises(InvalidRequest) as exc:
        parse_request({"model": "test", "input": [
            {"type": "function_call", "name": "run", "namespace": namespace, "call_id": "call_1", "arguments": "{}"},
        ]})
    assert exc.value.param == "input[0].namespace"


async def test_malformed_upstream_custom_call_fails_instead_of_emitting_bad_input() -> None:
    request = {"model": "test", "input": "Hi", "tools": [_custom()], "stream": True}
    provider = Provider([DoneEvent(message=AssistantMessage(content=(ToolCall(
        id="call_test", name="apply_patch", arguments={"input": {}},
    ),)))])
    status, _, body = await _request(provider, request)
    assert status == 200
    assert _decode_sse(body)[-1]["type"] == "response.failed"
    assert b"response.custom_tool_call_input.done" not in body


def _tool_search() -> dict[str, Any]:
    return {"type": "tool_search", "execution": "client", "description": "Discover deferred tools", "parameters": {
        "type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"],
    }}


@pytest.mark.parametrize("stream", [False, True])
async def test_client_tool_search_call_and_discovery_replay(stream: bool) -> None:
    spec = _tool_search()
    request = {"model": "test", "input": "Find weather tools", "tools": [spec], "stream": stream}
    _, context, _, _ = parse_request(request)
    assert context.tools and context.tools[0].name == "tool_search"
    arguments = {"query": "weather"}
    msg = AssistantMessage(content=(ToolCall(id="call_search", name="tool_search", arguments=arguments),),
                           stop_reason="toolUse")
    provider = Provider([
        ToolCallStartEvent(id="call_search", name="tool_search"), ToolCallDeltaEvent(delta=json.dumps(arguments)),
        ToolCallDoneEvent(id="call_search", name="tool_search", arguments=arguments), DoneEvent(message=msg),
    ])
    provider.result = msg
    status, _, body = await _request(provider, request)
    assert status == 200
    if stream:
        events = _decode_sse(body)
        assert events[-1]["type"] == "response.completed"
        response = events[-1]["response"]
        added = next(event["item"] for event in events if event["type"] == "response.output_item.added")
        done = next(event["item"] for event in events if event["type"] == "response.output_item.done")
        assert added["arguments"] == {} and added["id"] == done["id"]
        assert done == response["output"][0]
        assert b"response.function_call_arguments" not in body
    else:
        response = json.loads(body)
    search_call = response["output"][0]
    assert search_call["type"] == "tool_search_call" and search_call["execution"] == "client"
    assert search_call["call_id"] == "call_search" and search_call["arguments"] == arguments
    assert "name" not in search_call
    discovered = _namespace("weather", _function("forecast"))
    history = [search_call, {"type": "tool_search_output", "execution": "client", "status": "completed",
                             "call_id": "call_search", "tools": [discovered]}]
    _, replay, _, _ = parse_request({**request, "input": history})
    assert replay.tools and len(replay.tools) == 2
    assert isinstance(replay.messages[0], AssistantMessage) and replay.messages[0].content == msg.content
    result = replay.messages[1]
    assert isinstance(result, ToolResultMessage) and result.tool_call_id == "call_search"
    assert result.tool_name == "tool_search" and isinstance(result.content[0], TextContent)
    assert json.loads(result.content[0].text) == {"tools": [discovered]}
    assert "weather.forecast" in replay.tools[1].description
    response = format_response(AssistantMessage(content=(ToolCall(name=replay.tools[1].name),)), "test",
                               {**request, "input": history})
    assert response["output"][0]["namespace"] == "weather" and response["output"][0]["name"] == "forecast"
    assert response["tools"] == [spec, discovered]


@pytest.mark.parametrize("item,param", [
    ({"type": "tool_search_call", "execution": "server"}, "input[0].execution"),
    ({"type": "tool_search_call", "execution": "client", "arguments": {}}, "input[0].call_id"),
    ({"type": "tool_search_call", "execution": "client", "call_id": "s", "arguments": "{}"}, "input[0].arguments"),
    ({"type": "tool_search_output", "execution": "client", "call_id": "s", "tools": {}}, "input[0].tools"),
    ({"type": "tool_search_output", "execution": "server", "call_id": "s", "tools": []}, "input[0].execution"),
])
def test_invalid_tool_search_history_is_rejected(item: dict[str, Any], param: str) -> None:
    with pytest.raises(InvalidRequest) as exc:
        parse_request({"model": "test", "input": [item]})
    assert exc.value.param == param


def test_server_tool_search_remains_unsupported() -> None:
    with pytest.raises(InvalidRequest) as exc:
        parse_request({"model": "test", "input": "Hi", "tools": [{**_tool_search(), "execution": "server"}]})
    assert exc.value.param == "tools[0].execution"
