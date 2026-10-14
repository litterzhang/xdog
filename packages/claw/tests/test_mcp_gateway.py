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


@pytest.mark.parametrize("allowed", [False])
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
