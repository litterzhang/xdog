from __future__ import annotations

import asyncio
import json
import runpy
from pathlib import Path
from unittest.mock import patch

import pytest
from xdog.ai.types import AssistantMessage, DoneEvent, StartEvent, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer

ResumePeer = runpy.run_path(str(Path(__file__).parents[2] / "agent" / "tests" / "fixtures" / "mcp_resume_peer.py"))[
    "ResumePeer"]


@pytest.mark.parametrize("allowed", [False, True])
async def test_actual_gateway_sdk_policy_and_transcript_survive_resumed_mcp_tool_result(tmp_path, allowed):
    model_calls = []
    def stream(_model, context, _options=None):
        model_calls.append(context)
        if context.messages[-1].role == "toolResult":
            text = "".join(part.text for part in context.messages[-1].content if isinstance(part, TextContent))
            message = AssistantMessage(content=(TextContent(text=f"GATEWAY-{text}"),))
        else:
            name = next(tool.name for tool in context.tools if tool.name.startswith("mcp_fixture_"))
            message = AssistantMessage(content=(ToolCall(id="remote", name=name, arguments={"value": "gateway"}),),
                                       stop_reason="toolUse")
        async def generate():
            yield StartEvent(partial=AssistantMessage())
            yield DoneEvent(message=message, stop_reason=message.stop_reason)
        future = asyncio.get_running_loop().create_future()
        future.set_result(message)
        return EventStream.from_async_generator(generate(), result_future=future)

    with ResumePeer() as peer:
        config = ClawConfig(model="test/model", data_dir=str(tmp_path / "data"),
                            socket_path=str(tmp_path / "gateway.sock"), pid_file=str(tmp_path / "gateway.pid"),
                            groups=(GroupDef(id="main", is_main=True, workspace=str(tmp_path / "workspace")),),
                            mcp_servers={"fixture": {"transport": "http", "url": peer.url, "allow_tools": allowed}})
        server = GatewayServer(config)
        with patch("xdog.claw.core.runtime.gateway._build_model_and_options", return_value=("test/model", stream)):
            await server.start()
        reader, writer = await asyncio.open_unix_connection(config.socket_path)
        async def send(value):
            writer.write((json.dumps(value) + "\n").encode())
            await writer.drain()
        async def receive(kinds):
            async with asyncio.timeout(5):
                while True:
                    raw = await reader.readline()
                    assert raw
                    value = json.loads(raw)
                    if value["type"] in kinds:
                        return value
        try:
            assert not peer.requests
            await send({"type": "mcp_control", "request_id": "connect", "group_id": "main",
                        "server": "fixture", "action": "connect"})
            assert (await receive({"mcp_result"}))["success"]
            assert not any(request.get("method") == "tools/call" for request, _headers in peer.requests)
            await send({"type": "message", "run_id": "run", "group_id": "main", "content": "mcp-call"})
            await receive({"run_ack"})
            reply = await receive({"response", "final", "error"})
            assert reply["type"] != "error" and len(model_calls) == 2
            calls = [request for request, _headers in peer.requests if request.get("method") == "tools/call"]
            assert len(calls) == int(allowed)
            assert sum("Last-Event-ID" in headers for headers in peer.gets) == int(allowed)
            info = server._orchestrator.get_session_info("main")
            results = [entry for entry in info["history"] if entry["role"] == "toolResult"]
            assert len(results) == 1
            if allowed:
                assert "RESUMED-gateway" in json.dumps(reply)
                assert "RESUMED-gateway" in json.dumps(results)
            else:
                assert "mcp tool use is disabled" in json.dumps(reply).lower()
        finally:
            writer.close()
            await writer.wait_closed()
            await server.stop()
