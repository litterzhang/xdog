from __future__ import annotations

import base64
from pathlib import Path

import pytest
from xdog.agent.core import AgentToolResult
from xdog.agent.extensions.api import ExtensionAPI
from xdog.agent.extensions.tool_result import normalize_result
from xdog.ai.types import ImageContent, TextContent

FIXTURE = Path(__file__).parent / "fixtures/extensions"
PNG = base64.b64encode(b"\x89PNG\r\n\x1a\nexample").decode()


@pytest.mark.parametrize("value", [None, False, 0, "", [], {"nested": "中文"}])
def test_result_builder_preserves_json_types_presence_and_snapshot(value):
    api = ExtensionAPI({})
    result = api.result(structured=value, details=value)
    assert "structuredContent" in result and "details" in result
    assert result["structuredContent"] == value and result["details"] == value
    if isinstance(value, dict):
        value["nested"] = "changed"
        assert result["details"]["nested"] == "中文"
    assert normalize_result("legacy text") == {"content": [{"type": "text", "text": "legacy text"}]}


def test_agent_tool_result_conversion_preserves_image_and_json_metadata_without_paths():
    result = AgentToolResult(
        content=(TextContent(text="text"), ImageContent(data=PNG, mime_type="image/png")),
        details={"zero": 0, "disabled": False},
    )
    wire = normalize_result(result)
    assert wire["content"][1] == {"type": "image", "data": PNG, "mimeType": "image/png"}
    assert wire["details"] == {"zero": 0, "disabled": False}


@pytest.mark.parametrize(
    "value",
    [
        None,
        [],
        True,
        {"content": []},
        {"content": {}, "details": {}},
        {"content": [{"type": "text", "text": True}]},
        {"content": [{"type": "text", "text": "x", "extra": "ignored"}]},
        {"content": [{"type": "audio", "data": "AA==", "mimeType": "audio/wav"}]},
        {"content": [{"type": "image", "data": "bad", "mimeType": "image/png"}]},
        {"content": [{"type": "image", "data": PNG, "mimeType": "image/jpeg"}]},
        {"structuredContent": float("nan")},
        {"structuredContent": 0, "details": {"x": object()}},
        {"content": [{"type": "image", "data": PNG, "mimeType": "image/png"}] * 5},
        {"content": [{"type": "text", "text": "x" * (1024 * 1024 + 1)}]},
        {"content": [{"type": "text", "text": "x"}], "isError": "true"},
    ],
)
def test_invalid_or_unsupported_rich_results_are_not_coerced_or_silently_dropped(value):
    with pytest.raises((ValueError, RuntimeError)):
        normalize_result(value)


def test_tool_wire_codecs_are_absent_but_command_rich_data_is_retained():
    from xdog.agent.extensions import tool_result
    assert not hasattr(tool_result, 'host_result')
    assert not hasattr(tool_result, 'worker_result')
    result = ExtensionAPI({}).result([{'type': 'image', 'data': PNG, 'mimeType': 'image/png'}],
                                    structured=None, details=False)
    from xdog.agent.extensions.command_result import normalize_command_result
    validated = normalize_command_result(result)
    assert validated['details'] is False and validated['structuredContent'] is None
