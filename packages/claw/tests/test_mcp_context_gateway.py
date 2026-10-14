from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

from xdog.ai.types import AssistantMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer

FIXTURE = Path(__file__).parents[2] / "agent" / "tests" / "fixtures" / "mcp_server.py"


async def test_slow_context_read_keeps_gateway_ping_and_cancel_responsive(tmp_path):
    config = ClawConfig(model="test/model", data_dir=str(tmp_path / "data"),
                        socket_path=str(tmp_path / "context.sock"), pid_file=str(tmp_path / "context.pid"),
                        groups=(GroupDef(id="main", is_main=True),))
    server = GatewayServer(config)
    entered, canceled = asyncio.Event(), asyncio.Event()
    async def slow(*args):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            canceled.set()
    with patch("xdog.claw.core.runtime.gateway._build_model_and_options",
               return_value=("test/model", lambda *args: EventStream.empty(AssistantMessage()))):
        await server.start()
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    async def send(value):
        writer.write((json.dumps(value) + "\n").encode())
        await writer.drain()
    async def pong():
        async with asyncio.timeout(2):
            while True:
                value = json.loads(await reader.readline())
                if value["type"] == "pong":
                    return value
    try:
        await send({"type": "ping", "group_id": "main"})
        state = await pong()
        with patch.object(server._orchestrator, "mcp_context", slow):
            await send({"type": "mcp_context", "request_id": "slow", "group_id": "main",
                        "session_id": state.get("session_id", ""), "action": "read",
                        "server": "fixture", "name": "uri"})
            await asyncio.wait_for(entered.wait(), 2)
            await send({"type": "ping", "group_id": "main"})
            assert (await pong())["mcp_context"] == 1
            await send({"type": "mcp_context_cancel", "request_id": "stale"})
            await asyncio.sleep(0.02)
            assert not canceled.is_set()
            await send({"type": "mcp_context_cancel", "request_id": "slow"})
            await asyncio.wait_for(canceled.wait(), 2)
            await send({"type": "ping", "group_id": "main"})
            await pong()
    finally:
        writer.close()
        await writer.wait_closed()
        await server.stop()


def test_upgraded_client_does_not_infer_empty_legacy_payload_type_from_mime_or_metadata(tmp_path):
    from xdog.agent.mcp.context import McpBlock, McpDocument

    ui = ChatApp(str(tmp_path / 'not-connected.sock'))
    try:
        ui._editor.set_text('kept draft')
        ui._mcp_document = McpDocument('peer', 'resource', 'test://empty', (
            McpBlock('resource', text='[MCP content metadata]\n{"vendor/empty":true}',
                     mime_type='application/octet-stream'),))
        ui._mcp_owner_context = ui._mcp_context_key()
        assert not ui._mcp_content_types_supported
        ui._mcp_insert()
        assert ui._editor.get_text() == 'kept draft' and ui._mcp_document is not None
        assert 'payload type is unreported' in '\n'.join(ui._chat_log.render(120))
    finally:
        ui._request_exit()
