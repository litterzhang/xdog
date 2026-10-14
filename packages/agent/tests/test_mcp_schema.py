from __future__ import annotations

import asyncio

import pytest
from xdog.agent.mcp.schema import compile_schema, json_value
from xdog.agent.mcp.schema_owner import SchemaOwner
from xdog.agent.mcp.stdio import McpError


@pytest.mark.parametrize(
    "schema,value",
    [
        ({"type": "array", "items": {"type": "integer"}}, [1, 2]),
        ({"type": "null"}, None),
        ({"type": "boolean"}, False),
        ({"type": "number"}, 0),
        ({"type": "string"}, ""),
        (True, [1, None, False]),
    ],
)
def test_modern_output_schemas_accept_all_json_types_and_boolean_schema(schema, value):
    compile_schema(schema).validate(value, label="output")


def test_dialects_boolean_properties_conditionals_and_local_bundled_ids():
    old = {
        "$schema": "http://json-schema.org/draft-04/schema#",
        "type": "object",
        "properties": {"x": {"type": "number", "minimum": 2, "exclusiveMinimum": True}},
        "required": ["x"],
    }
    compiled = compile_schema(old, input_schema=True)
    compiled.validate({"x": 3}, label="arguments")
    with pytest.raises(McpError):
        compiled.validate({"x": 2}, label="arguments")
    schema = {
        "$id": "https://schemas.invalid/root",
        "type": "object",
        "$ref": "args",
        "$defs": {
            "args": {
                "$id": "args",
                "type": "object",
                "properties": {"value": {"type": ["string", "null"]}, "forbidden": False},
                "additionalProperties": False,
            }
        },
    }
    compiled = compile_schema(schema, input_schema=True)
    compiled.validate({"value": None}, label="arguments")
    assert compiled.model_parameters == schema
    for value in ({"forbidden": "x"}, {"value": 5}, {"extra": False}):
        with pytest.raises(McpError):
            compiled.validate(value, label="arguments")
    conditional = {
        "type": "object",
        "properties": {"kind": {"enum": ["a", "b"]}, "count": {"type": "integer"}},
        "if": {"properties": {"kind": {"const": "a"}}},
        "then": {"required": ["count"]},
    }
    with pytest.raises(McpError):
        compile_schema(conditional, input_schema=True).validate({"kind": "a"}, label="arguments")


@pytest.mark.parametrize(
    "schema",
    [
        {"$ref": "https://not-fetched.invalid/schema"},
        {"$id": "https://base.invalid", "$ref": "#missing"},
        {"$id": "https://base.invalid", "type": "object", "properties": {"x": {"$ref": "child"}}},
        {"$schema": "https://not-fetched.invalid/dialect"},
        {"$vocabulary": {"https://unknown.invalid/vocabulary": True}},
        {"type": "nonsense"},
        {"$ref": 5},
    ],
)
def test_unsupported_dialects_refs_vocabulary_and_invalid_schemas_fail_before_fetch(schema, monkeypatch):
    import socket
    import urllib.request

    monkeypatch.setattr(socket, "create_connection", lambda *args, **kwargs: pytest.fail("No schema network"))
    monkeypatch.setattr(urllib.request, "urlopen", lambda *args, **kwargs: pytest.fail("No schema URL lookup"))
    with pytest.raises(McpError):
        compile_schema(schema)


def test_examples_are_data_not_ref_fetches_and_dynamic_local_refs_are_supported():
    schema = {
        "$id": "https://schemas.invalid/node",
        "$dynamicAnchor": "node",
        "type": "object",
        "properties": {"next": {"$dynamicRef": "#node"}},
        "examples": [{"$ref": "https://example.invalid/inert"}],
        "default": {"$schema": "https://example.invalid/inert"},
    }
    compiled = compile_schema(schema, input_schema=True)
    compiled.validate({"next": {"next": {}}}, label="arguments")


@pytest.mark.parametrize("value", [float("nan"), float("inf"), {1: "not a string key"}, {"x": (1, 2)}, object()])
def test_non_json_or_coerced_values_are_refused(value):
    with pytest.raises(McpError):
        json_value(value)


def test_json_shape_depth_and_recursive_refs_fail_without_exposing_instance_values():
    value = "PRIVATE-INSTANCE-SECRET"
    for _ in range(66):
        value = [value]
    with pytest.raises(McpError) as caught:
        json_value(value)
    assert "PRIVATE-INSTANCE-SECRET" not in str(caught.value)
    with pytest.raises(McpError) as caught:
        compile_schema(False).validate("PRIVATE-INSTANCE-SECRET", label="output")
    assert "PRIVATE-INSTANCE-SECRET" not in str(caught.value)


def test_implicit_and_union_object_inputs_keep_raw_constraints_with_object_model_projection():
    schema = {
        "$ref": "#/$defs/args",
        "$defs": {
            "args": {"type": "object", "required": ["value"], "properties": {"value": {"type": ["string", "null"]}}}
        },
    }
    compiled = compile_schema(schema, input_schema=True)
    assert compiled.schema == schema and compiled.model_parameters == {**schema, "type": "object"}
    compiled.validate({"value": None}, label="arguments")
    with pytest.raises(McpError):
        compiled.validate({}, label="arguments")
    union = compile_schema({"type": ["object", "null"]}, input_schema=True)
    assert union.model_parameters == {"type": "object"} and union.schema == {"type": ["object", "null"]}
    with pytest.raises(McpError):
        compile_schema({"type": "array"}, input_schema=True)


@pytest.mark.parametrize(
    "target",
    [{"$schema": "https://unknown.invalid/dialect", "type": "object"}, {"$ref": "https://not-fetched.invalid/remote"}],
)
def test_explicitly_referenced_default_data_becomes_schema_and_cannot_hide_remote_or_unknown_dialect(target):
    with pytest.raises(McpError):
        compile_schema({"type": "object", "$ref": "#/default", "default": target}, input_schema=True)


@pytest.mark.parametrize("value", [None, False, 0, "", [], {"tag": "中文"}])
def test_json_tool_details_persist_typed_presence_and_runtime_handles_remain_transient(value):
    from xdog.agent.messages import dict_to_message, message_to_dict
    from xdog.ai.types import TextContent, ToolResultMessage

    details = {"mcp_server": "peer", "structured_content_present": True, "structured_content": value}
    result = ToolResultMessage(
        tool_call_id="call", tool_name="rich", content=(TextContent(text="canonical JSON"),), details=details
    )
    restored = dict_to_message(message_to_dict(result))
    assert restored.details == details and restored.details["structured_content_present"] is True
    result.details["structured_content"] = "changed after serialize"
    assert restored.details["structured_content"] == value
    transient = ToolResultMessage(tool_call_id="call", tool_name="local", details={"live": object()})
    assert "details" not in message_to_dict(transient)


async def test_canceled_child_creation_retains_and_reaps_the_actual_spawn_owner(monkeypatch):
    original = asyncio.create_subprocess_exec
    entered, release = asyncio.Event(), asyncio.Event()
    processes = []

    async def slow(*args, **kwargs):
        process = await original(*args, **kwargs)
        processes.append(process)
        entered.set()
        await release.wait()
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", slow)
    owner = SchemaOwner()
    task = asyncio.create_task(owner.compile({"type": "object"}, input_schema=True))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
        assert processes[0].returncode is not None and owner._process is None
    finally:
        release.set()
        await owner.close()
