from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from xdog.agent.mcp import McpError, McpManager
from xdog.agent.mcp.context import prompt_blocks, resource_blocks

FIXTURE = Path(__file__).parent / "fixtures" / "mcp_server.py"


@pytest.mark.parametrize("messages", [
    [{"role": "system", "content": {"type": "text", "text": "hidden"}}],
    [{"role": "user", "content": {"type": "unknown"}}],
    [{"role": "user", "content": {"type": "image", "data": "bad!", "mimeType": "image/png"}}],
])
def test_invalid_prompt_blocks_are_not_silently_inserted(messages):
    with pytest.raises(McpError):
        prompt_blocks(messages)


def test_resource_requires_exactly_one_payload_and_valid_blob():
    with pytest.raises(McpError):
        resource_blocks([{"uri": "fixture://text", "text": "x", "blob": "AA=="}])
    with pytest.raises(McpError):
        resource_blocks([{"uri": "fixture://text", "blob": "bad!"}])


async def test_resource_and_prompt_only_servers_connect_and_refresh_without_tools(tmp_path):
    for mode, kind, name in (("context-only", "resource", "fixture://text"), ("prompts-only", "prompt", "review")):
        log = tmp_path / mode
        manager = McpManager({"fixture": {"command": sys.executable,
                             "args": [str(FIXTURE), mode, str(log)]}}, tmp_path)
        try:
            await manager.connect("fixture")
            await manager.refresh("fixture")
            assert manager.status()[0]["state"] == "connected" and manager.status()[0]["tools"] == 0
            assert (await manager.catalog("fixture", kind))[0].name == name
            frames = [json.loads(line) for line in log.read_text().splitlines()]
            assert not any(frame["method"].startswith("tools/") for frame in frames)
        finally:
            await manager.aclose()
