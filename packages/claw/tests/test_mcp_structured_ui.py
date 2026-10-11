from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from xdog.ai.types import AssistantMessage, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer

FIXTURE = Path(__file__).parents[2] / "agent/tests/fixtures/mcp_schema_peer.py"


@pytest.mark.parametrize("allowed,choice", [(False, "array"), (True, "array"), (True, "null"),
                                           (False, "error"), (True, "error"), (False, "content"), (True, "content")])
async def test_live_claw_gateway_nullable_schema_output_policy_and_canonical_transcript(tmp_path, allowed, choice):
    log = tmp_path / "requests"
    contexts = []

    def stream(model, context, options):
        contexts.append(context)
        if len(contexts) == 1:
            tool = next(tool for tool in context.tools if tool.name.startswith("mcp_peer_"))
            return EventStream.empty(
                AssistantMessage(
                    content=(ToolCall(id="rich", name=tool.name, arguments={"value": None}),), stop_reason="toolUse"
                )
            )
        return EventStream.empty(AssistantMessage(content=(TextContent(text="done"),)))

    config = ClawConfig(
        model="test/model",
        data_dir=str(tmp_path / "data"),
        socket_path=str(tmp_path / "rich.sock"),
        pid_file=str(tmp_path / "rich.pid"),
        groups=(GroupDef(id="main", is_main=True),),
        mcp_servers={
            "peer": {
                "command": sys.executable,
                "args": [str(FIXTURE), "modern-" + choice, str(log)],
                "allow_tools": allowed,
            }
        },
    )
    server = GatewayServer(config)
    with patch("xdog.claw.core.runtime.gateway._build_model_and_options", return_value=("test/model", stream)):
        await server.start()
    ui = ChatApp(config.socket_path)
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    task = asyncio.create_task(ui._run_connection(reader, writer))

    async def wait(predicate):
        async with asyncio.timeout(10):
            while not predicate():
                ui._poll()
                await asyncio.sleep(0.005)

    try:
        await wait(lambda: ui._connection_ready)
        await server._orchestrator.manage_mcp("main", "connect", "peer")
        ui._handle_submit("run rich tool")
        await wait(lambda: len(contexts) == 2 and ui._active_run_id is None and not ui._waiting)
        calls = [
            json.loads(line) for line in log.read_text().splitlines() if json.loads(line)["method"] == "tools/call"
        ]
        result = next(message for message in contexts[-1].messages if message.role == "toolResult")
        if not allowed:
            assert not calls and result.is_error
        else:
            assert len(calls) == 1 and result.is_error == (choice == "error")
            expected = ([{"tag": "STRUCTURED-ARRAY-TAIL-987"}] if choice in ("array", "content") else
                        {"diagnostic": "ERROR-STRUCTURED-TAIL", "retry": False} if choice == "error" else None)
            assert result.details["structured_content"] == expected and result.details["structured_content_present"]
            body = "\n".join(record.body for record in ui._chat_log.transcript_records() if record.role == "tool")
            assert "[MCP structured output]" in body
            assert ("STRUCTURED-ARRAY-TAIL-987" if choice in ("array", "content") else
                    "ERROR-STRUCTURED-TAIL" if choice == "error" else "null") in body
            ui._open_transcript()
            await wait(lambda: ui._source_records is not None)
            complete = "\n".join(record.body for record in ui._source_records if record.role == "tool")
            assert "[MCP structured output]" in complete
            if choice == "content":
                assert 'MCP-CONTENT-METADATA' in body and 'MCP-IMAGE-METADATA' in body
                assert 'MCP-CONTENT-AUDIO' in body and 'MCP-CONTENT-LINK' in body
                assert result.details['mcp_content_metadata'][0]['metadata']['annotations']['priority'] == 0.25
                from xdog.claw.core.persistence.transcript_convert import transcript_to_messages
                runtime = server._orchestrator._runtimes["main"]
                saved = next(message for message in transcript_to_messages(
                    runtime.transcript_store.load_transcript(runtime._active_session.meta.session_id))
                    if message.role == "toolResult")
                assert saved.details['mcp_content_metadata'] == result.details['mcp_content_metadata']
                assert 'MCP-IMAGE-METADATA' in complete and 'MCP-CONTENT-AUDIO' in complete
            if choice == "error":
                from xdog.ai.types import ImageContent
                from xdog.claw.core.persistence.transcript_convert import transcript_to_messages
                runtime = server._orchestrator._runtimes["main"]
                saved = next(message for message in transcript_to_messages(
                    runtime.transcript_store.load_transcript(runtime._active_session.meta.session_id))
                    if message.role == "toolResult")
                assert saved.is_error and any(isinstance(part, ImageContent) for part in saved.content)
                assert saved.details["structured_content"] == expected and "RICH-MCP-ERROR" in complete
            assert ("STRUCTURED-ARRAY-TAIL-987" if choice in ("array", "content") else
                    "ERROR-STRUCTURED-TAIL" if choice == "error" else "null") in complete
        assert server._orchestrator._runtimes["main"].mcp.configs["peer"].allow_tools is allowed
    finally:
        ui._request_exit()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        writer.close()
        await writer.wait_closed()
        await server.stop()
