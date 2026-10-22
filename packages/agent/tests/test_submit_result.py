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
    ctx = {"flow_output_schema": _SCHEMA, "flow_result_sink": sink}
    payload = {"category": "depin", "count": 3, "active": True}
    result = await tool.execute("c1", {"result": payload}, ctx=ctx)
    assert "accepted" in _text(result).lower()
    assert sink["result"] == payload


@pytest.mark.asyncio
async def test_missing_field_rejected_and_sink_unset():
    tool = create_submit_result_tool()
    sink: dict = {}
    ctx = {"flow_output_schema": _SCHEMA, "flow_result_sink": sink}
    result = await tool.execute("c1", {"result": {"category": "depin", "count": 3}}, ctx=ctx)
    assert _text(result).startswith("Error:")
    assert "active" in _text(result)
    assert "result" not in sink


@pytest.mark.asyncio
async def test_wrong_type_rejected():
    tool = create_submit_result_tool()
    sink: dict = {}
    ctx = {"flow_output_schema": _SCHEMA, "flow_result_sink": sink}
    payload = {"category": "depin", "count": "three", "active": True}
    result = await tool.execute("c1", {"result": payload}, ctx=ctx)
    assert _text(result).startswith("Error:")
    assert "count" in _text(result)
    assert "result" not in sink


@pytest.mark.asyncio
async def test_boolean_rejected_where_integer_expected():
    tool = create_submit_result_tool()
    sink: dict = {}
    ctx = {"flow_output_schema": {"count": "integer"}, "flow_result_sink": sink}
    result = await tool.execute("c1", {"result": {"count": True}}, ctx=ctx)
    assert _text(result).startswith("Error:")
    assert "result" not in sink


@pytest.mark.asyncio
async def test_no_schema_accepts_any_object():
    tool = create_submit_result_tool()
    sink: dict = {}
    ctx = {"flow_result_sink": sink}
    payload = {"anything": "goes", "n": 1}
    result = await tool.execute("c1", {"result": payload}, ctx=ctx)
    assert "accepted" in _text(result).lower()
    assert sink["result"] == payload


@pytest.mark.asyncio
async def test_non_object_result_rejected():
    tool = create_submit_result_tool()
    sink: dict = {}
    ctx = {"flow_output_schema": _SCHEMA, "flow_result_sink": sink}
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

    It used to declare ``"type": "object"``. The argument check runs before the
    real schema (``flow_output_schema``) is consulted, so an agent whose single
    output port is an array -- which flow validates against that port's OWN
    schema -- had its correct array rejected with "expected object, got list".
    The tool errored, the sink stayed empty, and the run reported "agent did
    not submit a result" while the model had done exactly what it was asked.

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
        ctx={"flow_output_schema": schema, "flow_result_sink": sink},
    )

    assert "accepted" in _text(out).lower(), _text(out)
    assert sink["result"] == value
