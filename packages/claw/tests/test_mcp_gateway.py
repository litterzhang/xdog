from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from xdog.agent import AgentContext, BeforeToolCallContext
from xdog.ai.types import AssistantMessage, ToolCall
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer

FIXTURE = Path(__file__).parents[2] / "agent" / "tests" / "fixtures" / "mcp_server.py"


async def receive(reader):
    while True:
        raw = await asyncio.wait_for(reader.readline(), 5)
        assert raw
        value = json.loads(raw)
        if value["type"] == "mcp_result":
            return value


@pytest.mark.parametrize("allowed", [False, True])
async def test_gateway_owned_explicit_mcp_controls_tool_registration_policy_and_busy_guard(tmp_path, allowed):
    config = ClawConfig(model="test/model", data_dir=str(tmp_path / "data"), socket_path=str(tmp_path / "gateway.sock"),
                        pid_file=str(tmp_path / "gateway.pid"), groups=(GroupDef(id="main", is_main=True),),
                        mcp_servers={"fixture": {"command": sys.executable,
                                     "args": [str(FIXTURE), "modern", str(tmp_path / "requests")],
                                     "allow_tools": allowed}})
    server = GatewayServer(config)
    with patch("xdog.claw.core.runtime.gateway._build_model_and_options",
               return_value=("test/model", lambda *args: EventStream.empty(AssistantMessage()))):
        await server.start()
        reader, writer = await asyncio.open_unix_connection(config.socket_path)
        async def control(action):
            writer.write((json.dumps({"type": "mcp_control", "request_id": action, "group_id": "main",
                                      "server": "fixture", "action": action}) + "\n").encode())
            await writer.drain()
            return await receive(reader)
        try:
            assert not (tmp_path / "requests").exists()
            runtime = server._orchestrator._runtimes["main"]
            session = runtime.get_or_create_session()
            assert (await control("connect"))["success"]
            tool = runtime.mcp.tools()[0]
            assert any(value.name == tool.name for value in session.agent.state.tools)
            decision = await session._before_tool_call(BeforeToolCallContext(
                tool_call=ToolCall(id="call", name=tool.name, arguments={"value": "test"}),
                assistant_message=AssistantMessage(), args={"value": "test"}, context=AgentContext(),
            ))
            assert (decision is None) == allowed
            assert server._orchestrator.try_reserve_group("main")
            try:
                assert not (await control("disconnect"))["success"]
                assert (await control("list"))["success"]
            finally:
                server._orchestrator.release_group_reservation("main")
            assert (await control("disconnect"))["success"]
            assert not any(value.name == tool.name for value in session.agent.state.tools)
        finally:
            writer.close()
            await writer.wait_closed()
            await server.stop()


async def test_http_server_config_enters_group_manager_and_existing_policy(tmp_path):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    requests = []
    class Peer(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(request)
            if request["method"] == "server/discover":
                result = {"supportedVersions": ["2026-07-28"], "capabilities": {"tools": {}}}
            elif request["method"] == "tools/list":
                result = {"tools": [{"name": "remote", "inputSchema": {"type": "object"}}]}
            else:
                result = {"content": [{"type": "text", "text": "not automatically invoked"}]}
            data = json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
    peer = ThreadingHTTPServer(("127.0.0.1", 0), Peer)
    thread = threading.Thread(target=peer.serve_forever, daemon=True)
    thread.start()
    config = ClawConfig(model="test/model", data_dir=str(tmp_path / "data"), socket_path=str(tmp_path / "http.sock"),
                        pid_file=str(tmp_path / "http.pid"), groups=(GroupDef(id="main", is_main=True),),
                        mcp_servers={"http": {"transport": "http",
                                             "url": f"http://127.0.0.1:{peer.server_address[1]}/mcp"}})
    server = GatewayServer(config)
    with patch("xdog.claw.core.runtime.gateway._build_model_and_options",
               return_value=("test/model", lambda *args: EventStream.empty(AssistantMessage()))):
        await server.start()
        try:
            runtime = server._orchestrator._runtimes["main"]
            assert not requests
            await runtime.manage_mcp("connect", "http")
            session = runtime.get_or_create_session()
            tool = runtime.mcp.tools()[0]
            assert any(item.name == tool.name for item in session.agent.state.tools)
            result = await session._before_tool_call(BeforeToolCallContext(
                assistant_message=AssistantMessage(), tool_call=ToolCall(id="call", name=tool.name, arguments={}),
                args={}, context=AgentContext()))
            assert result.block
            assert not any(request["method"] == "tools/call" for request in requests)
            await runtime.manage_mcp("disconnect", "http")
        finally:
            await server.stop()
            peer.shutdown()
            peer.server_close()
            thread.join(3)
