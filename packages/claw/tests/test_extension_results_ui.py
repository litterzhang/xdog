from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
from xdog.ai.types import AssistantMessage, ImageContent, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer

FIXTURE = Path(__file__).parents[2] / "agent/tests/fixtures/extensions"


@pytest.mark.parametrize("allowed", [False, True])
async def test_live_claw_rich_extension_result_wire_image_metadata_and_permission_gate(tmp_path, allowed):
    root = tmp_path / "extensions"
    shutil.copytree(FIXTURE, root)
    contexts = []

    def stream(model, context, options):
        contexts.append(context)
        if len(contexts) == 1:
            tool = next(tool for tool in context.tools if tool.name.startswith("ext_rich_"))
            return EventStream.empty(
                AssistantMessage(content=(ToolCall(id="rich", name=tool.name, arguments={}),), stop_reason="toolUse")
            )
        return EventStream.empty(AssistantMessage(content=(TextContent(text="done"),)))

    config = ClawConfig(
        model="test/model",
        data_dir=str(tmp_path / "data"),
        socket_path=str(tmp_path / "rich.sock"),
        pid_file=str(tmp_path / "rich.pid"),
        groups=(GroupDef(id="main", is_main=True),),
        extensions=("rich",),
        extension_dir=str(root),
        extension_allow_tools=("rich",) if allowed else (),
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
        ui._handle_submit("run extension")
        await wait(lambda: len(contexts) == 2 and ui._active_run_id is None and not ui._waiting)
        result = next(message for message in contexts[-1].messages if message.role == "toolResult")
        if not allowed:
            assert result.is_error and not any(isinstance(part, ImageContent) for part in result.content)
        else:
            assert any(isinstance(part, ImageContent) for part in result.content)
            assert result.details["extension_details"] == {"count": 0, "enabled": False, "value": None}
            assert result.details["structured_content"] == [False, 0, None]
            assert not any(
                "POST-MUST-NOT-DROP-IMAGE" in part.text for part in result.content if isinstance(part, TextContent)
            )
            tool = next(child for child in ui._chat_log.children if child.__class__.__name__ == "ToolMessage")
            assert any(child.__class__.__name__ == "Image" for child in tool.children)
            runtime = server._orchestrator._runtimes["main"]
            from xdog.agent.messages import dict_to_message

            entries = runtime.transcript_store.load_transcript(runtime.get_new_session_id())
            saved = dict_to_message(next(entry for entry in entries if entry.get("role") == "toolResult"))
            assert any(isinstance(part, ImageContent) for part in saved.content)
            assert saved.details["extension_details"] == result.details["extension_details"]
    finally:
        ui._request_exit()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        writer.close()
        await writer.wait_closed()
        await server.stop()


async def test_claw_large_tool_images_preserve_canonical_data_without_oversized_preview_frame(tmp_path):
    # This exercises generic tool images too, not just the extension's stricter result budget.
    from xdog.agent import AgentTool, AgentToolResult
    from xdog.claw.core.runtime.display_events import ToolFinished
    from xdog.claw.core.types import UserInput

    data = "A" * (3 * 1024 * 1024 + 1)

    async def output(*args, **kwargs):
        return AgentToolResult(
            content=(TextContent(text="large result"), ImageContent(data=data, mime_type="image/png"))
        )

    tool = AgentTool(name="large_image_fixture", parameters={"type": "object"}, execute=output)
    calls = []

    def stream(model, context, options):
        calls.append(context)
        if len(calls) == 1:
            return EventStream.empty(
                AssistantMessage(content=(ToolCall(id="large", name=tool.name, arguments={}),), stop_reason="toolUse")
            )
        return EventStream.empty(AssistantMessage(content=(TextContent(text="done"),)))

    config = ClawConfig(
        model="test/model",
        data_dir=str(tmp_path / "data"),
        socket_path=str(tmp_path / "large.sock"),
        pid_file=str(tmp_path / "large.pid"),
        groups=(GroupDef(id="main", is_main=True),),
    )
    server = GatewayServer(config)
    with patch("xdog.claw.core.runtime.gateway._build_model_and_options", return_value=("test/model", stream)):
        await server.start()
    try:
        session = server._orchestrator._runtimes["main"].get_or_create_session()
        session.agent.set_tools((*session.agent.state.tools, tool))
        events = []
        await session.run_turn(UserInput(content="large image", channel="tui"), on_display_event=events.append)
        finished = next(event for event in events if isinstance(event, ToolFinished))
        assert not finished.images and "omitted from bounded live preview" in finished.result
        assert "original image data remains in the transcript" in finished.result
        result = next(message for message in session.agent.state.messages if message.role == "toolResult")
        assert any(isinstance(part, ImageContent) and part.data == data for part in result.content)
    finally:
        await server.stop()
