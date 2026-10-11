from __future__ import annotations

import base64
import shutil
from pathlib import Path

import pytest
from xdog.agent.core import AgentToolResult
from xdog.agent.extensions.api import ExtensionAPI
from xdog.agent.extensions.runtime import ExtensionRuntime
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


async def test_real_extension_worker_returns_typed_content_json_and_separate_metadata(tmp_path):
    root = tmp_path / "extensions"
    shutil.copytree(FIXTURE, root)
    runtime = ExtensionRuntime(["rich"], root, tmp_path)
    try:
        await runtime.initialize()
        tool = runtime.tools()[0]
        mixed = await tool.execute("mixed", {"value": "mixed"})
        assert any(isinstance(part, ImageContent) for part in mixed.content)
        assert mixed.content[0].text == "RICH-EXTENSION-RESULT"
        assert mixed.details["structured_content"] == [False, 0, None]
        assert mixed.details["extension_details"] == {"count": 0, "enabled": False, "value": None}
        assert mixed.details["extension_details_present"] and mixed.details["extension"] == "rich"
        typed = await tool.execute("typed", {"value": "typed"})
        assert typed.details["extension_details"] == {"typed": True}
        assert any(isinstance(part, ImageContent) for part in typed.content)
        structured = await tool.execute("structured", {"value": "structured"})
        assert structured.content[0].text == "null"
        assert structured.details["structured_content_present"] and structured.details["extension_details"] is False
        failed = await tool.execute("error", {"value": "error"})
        assert failed.is_error and any(isinstance(part, ImageContent) for part in failed.content)
        assert failed.details["structured_content"] == {"diagnostic": "ERROR-STRUCTURED-TAIL", "retry": False}
        assert failed.details["extension_details"] == {"failed": True}
        assert runtime.snapshot()["extensions"][0]["state"] == "active"
    finally:
        await runtime.aclose()


@pytest.mark.parametrize("value", ["malformed", "unsupported"])
async def test_real_invalid_worker_result_fails_without_callback_replay(tmp_path, value):
    root = tmp_path / "extensions"
    shutil.copytree(FIXTURE, root)
    runtime = ExtensionRuntime(["rich"], root, tmp_path)
    try:
        await runtime.initialize()
        with pytest.raises(RuntimeError):
            await runtime.tools()[0].execute("bad", {"value": value})
        assert runtime.snapshot()["extensions"][0]["state"] == "failed"
    finally:
        await runtime.aclose()


def test_host_revalidates_rich_result_version_image_limits_and_typed_metadata():
    from xdog.agent.extensions.tool_result import host_result, worker_result
    wire = worker_result(ExtensionAPI({}).result([{'type': 'image', 'data': PNG, 'mimeType': 'image/png'}],
                                               structured=None, details=False))
    assert wire['xdog_rich_result'] == 1
    validated = host_result(wire)
    assert validated['xdog_result_details'] is False and 'structuredContent' in validated
    for bad in ({**wire, 'xdog_rich_result': True}, {**wire, 'xdog_rich_result': 2},
                {**wire, 'content': [{'type': 'image', 'data': 'bad', 'mimeType': 'image/png'}]},
                {**wire, 'xdog_result_details': float('nan')}):
        with pytest.raises((ValueError, RuntimeError)):
            host_result(bad)
    assert worker_result('legacy') == {'content': [{'type': 'text', 'text': 'legacy'}]}
