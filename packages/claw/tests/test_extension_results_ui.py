from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from xdog.ai.types import AssistantMessage, ImageContent, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer

FIXTURE = Path(__file__).parents[2] / "agent/tests/fixtures/extensions"


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
        session.agent.set_tools((*session.agent.local_tools, tool))
        events = []
        await session.run_turn(UserInput(content="large image", channel="tui"), on_display_event=events.append)
        finished = next(event for event in events if isinstance(event, ToolFinished))
        assert not finished.images and "omitted from bounded live preview" in finished.result
        assert "original image data remains in the transcript" in finished.result
        result = next(message for message in session.agent.state.messages if message.role == "toolResult")
        assert any(isinstance(part, ImageContent) and part.data == data for part in result.content)
    finally:
        await server.stop()
