from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
from xdog.ai.types import AssistantMessage, DoneEvent, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer

FIXTURE = Path(__file__).parents[2] / "agent" / "tests" / "fixtures" / "extensions"


@pytest.mark.parametrize("allowed", [False, True])
async def test_actual_gateway_client_extension_events_widgets_commands_and_tool_gate(tmp_path, allowed):
    root = tmp_path / "extensions"
    shutil.copytree(FIXTURE, root)
    marker = tmp_path / "marker"
    calls = []
    def stream(_model, context, _options=None):
        calls.append(context)
        if context.messages[-1].role == "user":
            tool = next(tool for tool in context.tools if tool.name.startswith("ext_sample_"))
            message = AssistantMessage(content=(ToolCall(id="ext", name=tool.name, arguments={"value": "input"}),),
                                       stop_reason="toolUse")
        else:
            message = AssistantMessage(content=(TextContent(text="done"),))
        async def generate():
            yield DoneEvent(message=message, stop_reason=message.stop_reason)
        future = asyncio.get_running_loop().create_future()
        future.set_result(message)
        return EventStream.from_async_generator(generate(), result_future=future)
    config = ClawConfig(model="test/model", data_dir=str(tmp_path / "data"),
                        socket_path=str(tmp_path / "gateway.sock"), pid_file=str(tmp_path / "gateway.pid"),
                        groups=(GroupDef(id="main", is_main=True),), extensions=("sample",),
                        extension_allow_tools=("sample",) if allowed else (), extension_dir=str(root),
                        extension_settings={"sample": {"marker": str(marker)}})
    server = GatewayServer(config)
    with patch("xdog.claw.core.runtime.gateway._build_model_and_options", return_value=("test/model", stream)):
        await server.start()
    ui = ChatApp(config.socket_path)
    ui._io_generation = 1
    ui._handle_response({"type": "_connection_opened", "generation": 1})
    reader, writer = await asyncio.open_unix_connection(config.socket_path, limit=4 * 1024 * 1024)
    task = asyncio.create_task(ui._run_connection(reader, writer))
    async def wait(predicate):
        async with asyncio.timeout(6):
            while True:
                ui._poll()
                if predicate():
                    return
                await asyncio.sleep(0.005)
    try:
        await wait(lambda: ui._connection_ready)
        assert not marker.exists()
        ui._editor.set_text("preserved draft")
        ui._extension_command("extensions", "list")
        await wait(lambda: ui._extension_pending is None and marker.exists())
        assert ui._gateway_session_id and ui._editor.get_text() == "preserved draft" and not calls
        assert "EXTENSION-WIDGET" in "\n".join(ui._extension_view.render(80))
        assert "ext-sample-hello" in ui._editor._command_provider()
        ui._extension_command("ext-sample-hello", "user")
        await wait(lambda: ui._extension_pending is None)
        assert "EXTENSION-COMMAND-STATUS" in "\n".join(ui._extension_view.render(80)) and not calls
        ui._handle_submit("external")
        await wait(lambda: len(calls) == 2 and ui._active_run_id is None)
        result = calls[1].messages[-1]
        if allowed:
            assert result.content[0].text == "EXTENSION-RESULT-input-POST"
        else:
            assert result.is_error and "disabled" in result.content[0].text
        before = ui._extension_snapshot
        ui._handle_response({"type": "extension_ui", "group_id": "main", "run_id": "stale",
                             "snapshot": {"ui": {"bad": {"lines": ["OLD"]}}}})
        assert ui._extension_snapshot is before
    finally:
        ui._request_exit()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        writer.close()
        await writer.wait_closed()
        await server.stop()
    assert marker.read_text().endswith("stopped\n")
