from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
from xdog.ai.types import AssistantMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer
from xdog.tui.keys import KeyEvent, KeyEventType
from xdog.tui.prompt_keymap import PromptKeymap

FIXTURE = Path(__file__).parents[2] / "agent/tests/fixtures/extensions"


@pytest.mark.parametrize("remap", [False, True])
async def test_live_claw_shortcut_command_snapshot_negotiation_remap_unload_and_viewer_priority(tmp_path, remap):
    root = tmp_path / "extensions"
    shutil.copytree(FIXTURE, root)
    calls = []

    def stream(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())

    config = ClawConfig(
        model="test/model",
        data_dir=str(tmp_path / "data"),
        socket_path=str(tmp_path / "shortcuts.sock"),
        pid_file=str(tmp_path / "shortcuts.pid"),
        groups=(GroupDef(id="main", is_main=True),),
        extensions=("keyed",),
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
        assert ui._extension_shortcuts_supported
        ui._editor.set_text("parent draft")
        assert not ui._extension_shortcut_input(KeyEvent("k", alt=True))
        ui._extension_command("extensions", "")
        await wait(lambda: ui._extension_pending is None)
        assert ui._extension_snapshot.get("shortcuts")
        if remap:
            ui._keymap = PromptKeymap.from_overrides({"ext-keyed-compose": "alt+x"})
            ui._editor.set_keymap(ui._keymap)
        event = KeyEvent("x" if remap else "k", alt=True)
        ui._extension_shortcuts_supported = False
        assert not ui._extension_shortcut_input(event)
        assert "updated shortcut gateway required" in ui._effective_extension_shortcuts().help_text()
        ui._extension_shortcuts_supported = True
        assert not ui._extension_shortcut_input(KeyEvent(event.key, alt=True, event_type=KeyEventType.REPEAT))
        ui._handle_global_input(KeyEvent("o", ctrl=True))
        assert not ui._extension_shortcut_input(event)
        ui._close_details()
        assert ui._handle_global_input(event) == {"consume": True}
        assert not ui._extension_shortcut_input(event)
        await wait(lambda: ui._extension_pending is None)
        assert ui._editor.get_text() == "parent draft SHORTCUT-DRAFT" and not calls
        assert not server._orchestrator._runtimes["main"]._active_session.agent.state.messages
        ui._show_keybindings()
        assert "DISABLED" in "\n".join(ui._chat_log.render(120))
        ui._extension_command("extensions", "unload keyed")
        await wait(lambda: ui._extension_pending is None)
        assert not ui._extension_shortcut_input(event)
    finally:
        ui._request_exit()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        writer.close()
        await writer.wait_closed()
        await server.stop()
