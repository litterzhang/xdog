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
from xdog.tui.keys import KeyEvent

FIXTURE = Path(__file__).parents[2] / "agent/tests/fixtures/extensions"


@pytest.mark.parametrize("change", [False, True])
async def test_actual_claw_command_editor_cas_owner_negotiation_and_no_model_turn(tmp_path, change):
    root = tmp_path / "extensions"
    shutil.copytree(FIXTURE, root)
    calls = []

    def stream(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())

    config = ClawConfig(
        model="test/model",
        data_dir=str(tmp_path / "data"),
        socket_path=str(tmp_path / "editor.sock"),
        pid_file=str(tmp_path / "editor.pid"),
        groups=(GroupDef(id="main", is_main=True),),
        extensions=("editor",),
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
        assert ui._extension_editor_supported
        ui._editor.set_text("parent draft")
        ui._extension_command("ext-editor-compose", "pause")
        await wait(lambda: ui._extension_dialog_panel is not None)
        panel = ui._extension_dialog_panel
        assert panel.dialog["kind"] == "confirm"
        if change:
            ui._editor.set_text("user changed draft")
        panel.handle_input(KeyEvent("down"))
        panel.render(80)
        panel.handle_input(KeyEvent("enter"))
        await wait(lambda: ui._extension_pending is None)
        expected = "user changed draft" if change else "parent draft EXTENSION-COMPOSED"
        assert ui._editor.get_text() == expected and not calls
        assert not server._orchestrator._runtimes["main"]._active_session.agent.state.messages
    finally:
        ui._request_exit()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        writer.close()
        await writer.wait_closed()
        await server.stop()


async def test_older_viewer_editor_command_returns_unavailable_and_never_receives_control_request(tmp_path):
    root = tmp_path / 'extensions'
    shutil.copytree(FIXTURE, root)
    config = ClawConfig(model='test/model', data_dir=str(tmp_path / 'data'),
        socket_path=str(tmp_path / 'oldeditor.sock'), pid_file=str(tmp_path / 'oldeditor.pid'),
        groups=(GroupDef(id='main', is_main=True),), extensions=('editor',), extension_dir=str(root))
    server = GatewayServer(config)
    with patch('xdog.claw.core.runtime.gateway._build_model_and_options',
               return_value=('test/model', lambda *args: EventStream.empty(AssistantMessage()))):
        await server.start()
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    try:
        import json
        writer.write(b'{"type":"ping","group_id":"main"}\n')
        await writer.drain()
        state = json.loads(await reader.readline())
        writer.write((json.dumps({'type': 'extension_control', 'request_id': 'old', 'group_id': 'main',
            'session_id': state.get('session_id', ''), 'action': 'command', 'name': 'ext-editor-compose', 'args': ''})
                      + '\n').encode())
        await writer.drain()
        events = []
        async with asyncio.timeout(8):
            while True:
                value = json.loads(await reader.readline())
                events.append(value)
                if value['type'] == 'extension_result':
                    break
        assert value['success'] and value['output'] == 'EDITOR-UNAVAILABLE'
        assert not any(event['type'] == 'extension_dialog' for event in events)
    finally:
        writer.close()
        await writer.wait_closed()
        await server.stop()
