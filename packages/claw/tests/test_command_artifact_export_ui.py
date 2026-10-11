from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from unittest.mock import patch

from xdog.agent.extensions.command_result import read_artifact
from xdog.ai.types import AssistantMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer
from xdog.tui.keys import KeyEvent

FIXTURE = Path(__file__).parents[2] / "agent/tests/fixtures/extensions"


async def test_actual_claw_local_artifact_export_reload_owner_and_original_bytes(tmp_path):
    root = tmp_path / "extensions"
    shutil.copytree(FIXTURE, root)
    calls = []

    def stream(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())

    config = ClawConfig(
        model="test/model",
        data_dir=str(tmp_path / "data"),
        socket_path=str(tmp_path / "artifact.sock"),
        pid_file=str(tmp_path / "artifact.pid"),
        groups=(GroupDef(id="main", is_main=True),),
        extensions=("rich",),
        extension_dir=str(root),
    )
    server = GatewayServer(config)
    with patch("xdog.claw.core.runtime.gateway._build_model_and_options", return_value=("test/model", stream)):
        await server.start()
    ui = ChatApp(config.socket_path)
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    task = asyncio.create_task(ui._run_connection(reader, writer))

    async def wait(predicate):
        async with asyncio.timeout(8):
            while not predicate():
                ui._poll()
                await asyncio.sleep(0.005)

    try:
        await wait(lambda: ui._connection_ready)
        ui._editor.set_text("parent draft")
        ui._extension_command("ext-rich-show", "")
        await wait(lambda: ui._extension_pending is None)
        original = next(item for item in ui._chat_log.children if item.__class__.__name__ == "ExtensionCommand")
        target = tmp_path / "saved.json"
        ui._output_command("export", f"{target} artifact")
        await wait(lambda: ui._outputs.panel is not None)
        assert not target.exists() and ui._pending_output is None  # Local artifact, not a new gateway source fetch.
        panel = ui._outputs.panel
        panel.set_render_budget(12)
        await wait(lambda: bool(panel.render(120)) and panel._visible)
        panel.handle_input(KeyEvent("enter"))
        await wait(lambda: not ui._outputs.pending and not ui._outputs.worker.busy)
        restored = read_artifact(target)
        assert restored.result == original.result and target.stat().st_mode & 0o777 == 0o600
        ui._handle_submit("/artifact " + str(target))
        await wait(
            lambda: len([item for item in ui._chat_log.children if item.__class__.__name__ == "ExtensionCommand"]) == 2
        )
        assert ui._editor.get_text() == "parent draft" and not calls
        runtime = server._orchestrator._runtimes["main"]
        assert not runtime._active_session.agent.state.messages
        ui._output_command("export", f"{tmp_path / 'canceled.json'} artifact")
        await wait(lambda: ui._outputs.panel is not None)
        ui._output_epoch += 1
        ui._poll()
        assert ui._outputs.panel is None and not (tmp_path / "canceled.json").exists()
        ui._output_command("export", f"{tmp_path / 'wrong-owner.json'} artifact")
        assert not ui._outputs.pending and not calls
    finally:
        ui._request_exit()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        writer.close()
        await writer.wait_closed()
        await server.stop()
