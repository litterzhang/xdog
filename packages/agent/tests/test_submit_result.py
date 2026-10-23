"""Tests for the submit_result built-in tool (schema validation + ctx handoff)."""

import pytest
from xdog.agent.tools import ToolRegistry, create_submit_result_tool
from xdog.ai.types import TextContent


def _text(result) -> str:
    """Extract plain text from an AgentToolResult."""
    return "\n".join(p.text for p in result.content if isinstance(p, TextContent))


_SCHEMA = {"category": "string", "count": "integer", "active": "boolean"}


@pytest.mark.asyncio
async def test_valid_payload_accepted_and_stored_in_sink():
    tool = create_submit_result_tool()
    sink: dict = {}
    ctx = {"result_schema": _SCHEMA, "result_sink": sink}
    payload = {"category": "depin", "count": 3, "active": True}
    result = await tool.execute("c1", {"result": payload}, ctx=ctx)
    assert "accepted" in _text(result).lower()
    assert sink["result"] == payload


@pytest.mark.asyncio
async def test_missing_field_rejected_and_sink_unset():
    tool = create_submit_result_tool()
    sink: dict = {}
    ctx = {"result_schema": _SCHEMA, "result_sink": sink}
    result = await tool.execute("c1", {"result": {"category": "depin", "count": 3}}, ctx=ctx)
    assert _text(result).startswith("Error:")
    assert "active" in _text(result)
    assert "result" not in sink


@pytest.mark.asyncio
async def test_wrong_type_rejected():
    tool = create_submit_result_tool()
    sink: dict = {}
    ctx = {"result_schema": _SCHEMA, "result_sink": sink}
    payload = {"category": "depin", "count": "three", "active": True}
    result = await tool.execute("c1", {"result": payload}, ctx=ctx)
    assert _text(result).startswith("Error:")
    assert "count" in _text(result)
    assert "result" not in sink


@pytest.mark.asyncio
async def test_boolean_rejected_where_integer_expected():
    tool = create_submit_result_tool()
    sink: dict = {}
    ctx = {"result_schema": {"count": "integer"}, "result_sink": sink}
    result = await tool.execute("c1", {"result": {"count": True}}, ctx=ctx)
    assert _text(result).startswith("Error:")
    assert "result" not in sink


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [{"anything": "goes", "n": 1}, [1, "two"], "text", 3.5, True, None])
async def test_no_schema_accepts_any_json_value(payload):
    tool = create_submit_result_tool()
    sink: dict = {}
    ctx = {"result_sink": sink}
    result = await tool.execute("c1", {"result": payload}, ctx=ctx)
    assert "accepted" in _text(result).lower()
    assert sink["result"] == payload


@pytest.mark.asyncio
async def test_non_object_result_rejected():
    tool = create_submit_result_tool()
    sink: dict = {}
    ctx = {"result_schema": _SCHEMA, "result_sink": sink}
    result = await tool.execute("c1", {"result": "not an object"}, ctx=ctx)
    assert _text(result).startswith("Error:")
    assert "result" not in sink


@pytest.mark.asyncio
async def test_none_ctx_does_not_crash():
    tool = create_submit_result_tool()
    result = await tool.execute("c1", {"result": {"x": 1}})
    assert "accepted" in _text(result).lower()


def test_submit_result_is_registered():
    assert "submit_result" in ToolRegistry().registered_names()


def test_tool_schema_leaves_result_untyped():
    """`result` must accept any JSON value, not just an object.

    The argument check runs before the caller's ``result_schema``. Declaring
    ``"type": "object"`` would reject valid arrays/scalars before validation.

    An untyped property is JSON Schema for "unconstrained", which leaves the
    per-call schema as the only validator -- which is where the contract
    actually lives.
    """
    tool = create_submit_result_tool()
    assert tool.name == "submit_result"
    props = tool.parameters["properties"]
    assert "type" not in props["result"], (
        "a concrete type here rejects valid values before the real schema is "
        "ever applied"
    )
    assert "result" in tool.parameters["required"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "schema,value",
    [
        ({"type": "array", "items": {"type": "string"}}, ["a", "b"]),
        ({"type": "string"}, "just text"),
        ({"type": "number"}, 3.5),
        ({"type": "object"}, {"x": 1}),
    ],
    ids=["array", "string", "number", "object"],
)
async def test_a_non_object_result_is_accepted_when_the_schema_allows_it(
    schema, value
):
    """Every one of these except the last was impossible before."""
    sink: dict[str, object] = {}
    tool = create_submit_result_tool()
    out = await tool.execute(
        "c1",
        {"result": value},
        ctx={"result_schema": schema, "result_sink": sink},
    )

    assert "accepted" in _text(out).lower(), _text(out)
    assert sink["result"] == value


@pytest.mark.parametrize("old_key", ["flow_output_schema", "flow_result_sink"])
@pytest.mark.parametrize("old_value", [None, {}, {"count": "integer"}])
@pytest.mark.parametrize("with_generic_keys", [False, True])
async def test_retired_context_keys_are_rejected_without_writing_any_sink(old_key, old_value, with_generic_keys):
    """Even empty legacy settings or mixed contracts must not bypass validation."""
    sink = {"result": "previous"}
    ctx = {old_key: old_value}
    if with_generic_keys:
        ctx.update(result_schema={"type": "object"}, result_sink=sink)
    out = await create_submit_result_tool().execute("c1", {"result": {}}, ctx=ctx)
    assert _text(out).startswith("Error: unsupported context key")
    assert old_key in _text(out)
    assert ("result_schema" if old_key == "flow_output_schema" else "result_sink") in _text(out)
    assert sink == {"result": "previous"}
    assert ctx[old_key] == old_value


async def test_retired_flow_contract_cannot_succeed_as_an_unconstrained_submission():
    sink = {}
    out = await create_submit_result_tool().execute(
        "c1", {"result": {"count": "not an integer"}},
        ctx={"flow_output_schema": {"count": "integer"}, "flow_result_sink": sink},
    )
    assert _text(out).startswith("Error: unsupported context key")
    assert not sink


async def test_neutral_sdk_agent_validates_corrects_and_hands_off_to_caller_sink():
    """A plain SDK caller needs no workflow objects or Flow runtime context."""
    from xdog.agent import Agent, AgentConfig
    from xdog.ai.types import AssistantMessage, ToolCall, ToolResultMessage
    from xdog.ai.utils.event_stream import EventStream

    sink = {}
    ctx = {"result_schema": {"type": "array", "items": {"type": "integer"}}, "result_sink": sink}
    payload = [1, 2]
    responses = iter((
        AssistantMessage(content=(ToolCall(id="invalid", name="submit_result", arguments={"result": ["bad"]}),),
                         stop_reason="toolUse"),
        AssistantMessage(content=(ToolCall(id="valid", name="submit_result", arguments={"result": payload}),),
                         stop_reason="toolUse"),
        AssistantMessage(content=(TextContent(text="finished"),)),
    ))
    calls = []

    def stream(model, context, options):
        calls.append(context)
        if len(calls) == 2:
            assert sink == {}, "a rejected submission must not populate the caller's sink"
        return EventStream.empty(next(responses))

    agent = Agent(stream, config=AgentConfig(model="offline"),
                  tools=tuple(ToolRegistry().create_tools(["submit_result"])), tool_ctx=ctx)
    try:
        async for _ in await agent.prompt("Return two integers using submit_result."):
            pass
        assert len(calls) == 3
        results = [message for message in agent.messages if isinstance(message, ToolResultMessage)]
        assert [message.tool_call_id for message in results] == ["invalid", "valid"]
        assert _text(results[0]).startswith("Error:")
        assert _text(results[1]) == "Result accepted."
        assert sink["result"] == payload
        assert set(ctx) == {"result_schema", "result_sink"}
    finally:
        await agent.aclose()
