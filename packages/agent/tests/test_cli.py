"""Pure CLI argument helpers; offline host assembly is in test_cli_capabilities."""

import json

import pytest
from xdog.agent.cli import _parse_tool_ctx


def test_none_returns_empty_dict():
    assert _parse_tool_ctx(None) == {}


def test_parses_json_object():
    ctx = _parse_tool_ctx('{"result_schema": {"summary": "string"}}')
    assert ctx == {"result_schema": {"summary": "string"}}


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


async def test_cli_context_roundtrip_uses_generic_schema_and_shared_sink(tmp_path, capsys):
    from xdog.agent.cli import _print_tool_ctx
    from xdog.agent.tools import create_submit_result_tool

    path = tmp_path / "ctx.json"
    path.write_text(json.dumps({"result_schema": {"summary": "string"}, "result_sink": {}}), encoding="utf-8")
    ctx = _parse_tool_ctx(f"@{path}")
    await create_submit_result_tool().execute("submit", {"result": {"summary": "done"}}, ctx=ctx)
    _print_tool_ctx(ctx, verbose=False)
    captured = capsys.readouterr()
    assert captured.out == ""
    printed = json.loads(captured.err[captured.err.index("{"):])
    assert printed == {"result_schema": {"summary": "string"}, "result_sink": {"result": {"summary": "done"}}}


def test_chat_help_uses_generic_result_context(monkeypatch, capsys):
    from xdog.agent.cli import main

    monkeypatch.setattr("sys.argv", ["xdog-agent", "chat", "--help"])
    with pytest.raises(SystemExit) as caught:
        main()
    assert caught.value.code == 0
    help_text = capsys.readouterr().out
    assert '"result_schema"' in help_text
    assert "flow_output_schema" not in help_text
    assert "flow_result_sink" not in help_text
