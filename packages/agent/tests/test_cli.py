"""Pure CLI argument helpers; offline host assembly is in test_cli_capabilities."""

import json

import pytest
from xdog.agent.cli import _parse_tool_ctx


def test_none_returns_empty_dict():
    assert _parse_tool_ctx(None) == {}


def test_parses_json_object():
    ctx = _parse_tool_ctx('{"flow_output_schema": {"summary": "string"}}')
    assert ctx == {"flow_output_schema": {"summary": "string"}}


def test_reads_from_file_with_at_prefix(tmp_path):
    p = tmp_path / "ctx.json"
    p.write_text(json.dumps({"a": 1, "b": [1, 2]}), encoding="utf-8")
    ctx = _parse_tool_ctx(f"@{p}")
    assert ctx == {"a": 1, "b": [1, 2]}


def test_invalid_json_exits():
    with pytest.raises(SystemExit):
        _parse_tool_ctx("{not json}")


def test_non_object_json_exits():
    with pytest.raises(SystemExit):
        _parse_tool_ctx("[1, 2, 3]")


def test_missing_file_exits():
    with pytest.raises(SystemExit):
        _parse_tool_ctx("@/nonexistent/path/to/ctx.json")
