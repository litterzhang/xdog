from __future__ import annotations

import asyncio
import json
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

FIXTURE = Path(__file__).parents[2] / "agent" / "tests" / "fixtures" / "extensions"


async def test_live_gateway_command_dialogs_leave_socket_controls_responsive_and_fence_other_clients(tmp_path):
    root = tmp_path / "extensions"
    shutil.copytree(FIXTURE, root)
    calls = []
    def stream(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())
    config = ClawConfig(model="test/model", data_dir=str(tmp_path / "data"),
                        socket_path=str(tmp_path / "gateway.sock"), pid_file=str(tmp_path / "gateway.pid"),
                        groups=(GroupDef(id="main", is_main=True),), extensions=("dialogue",), extension_dir=str(root))
    server = GatewayServer(config)
    with patch("xdog.claw.core.runtime.gateway._build_model_and_options", return_value=("test/model", stream)):
        await server.start()
    ui = ChatApp(config.socket_path)
    ui._io_generation = 1
    ui._handle_response({"type": "_connection_opened", "generation": 1})
    reader, writer = await asyncio.open_unix_connection(config.socket_path, limit=4 * 1024 * 1024)
    task = asyncio.create_task(ui._run_connection(reader, writer))
    other_reader, other_writer = await asyncio.open_unix_connection(config.socket_path)
    async def wait(predicate):
        async with asyncio.timeout(6):
            while True:
                ui._poll()
                if predicate():
                    return
                await asyncio.sleep(0.005)
    try:
        await wait(lambda: ui._connection_ready)
        ui._editor.set_text("preserved draft")
        ui._extension_command("ext-dialogue-ask", "")
        await wait(lambda: ui._extension_dialog_panel is not None)
        panel = ui._extension_dialog_panel
        key = panel.dialog["id"]
        # A second connected client cannot answer a guessed/stolen dialog ID.
        other_writer.write((json.dumps({"type": "extension_dialog_answer", "id": key,
                                       "value": "Blue"}) + "\n").encode())
        await other_writer.drain()
        value = json.loads(await asyncio.wait_for(other_reader.readline(), 3))
        assert value["type"] == "extension_dialog_ack" and value["accepted"] is False
        assert ui._extension_pending is not None
        ui._queue_request({"type": "ping", "group_id": "main"})
        await wait(lambda: ui._gateway_session_id == server._orchestrator.get_new_session_id("main"))
        assert not ui._session_context_stale
        panel.handle_input(KeyEvent("down"))
        panel.render(80)
        panel.handle_input(KeyEvent("enter"))
        await wait(lambda: ui._extension_dialog_panel is not None
                   and ui._extension_dialog_panel.dialog["kind"] == "input")
        panel = ui._extension_dialog_panel
        panel._input.set_text("user")
        panel.render(80)
        panel.handle_input(KeyEvent("enter"))
        await wait(lambda: ui._extension_dialog_panel is not None
                   and ui._extension_dialog_panel.dialog["kind"] == "confirm")
        panel = ui._extension_dialog_panel
        panel.handle_input(KeyEvent("down"))
        panel.render(80)
        panel.handle_input(KeyEvent("enter"))
        await wait(lambda: ui._extension_pending is None)
        assert ui._extension_dialog_panel is None and ui._tui._focused is ui._editor
        assert ui._editor.get_text() == "preserved draft" and not calls
    finally:
        ui._request_exit()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        writer.close()
        other_writer.close()
        await asyncio.gather(writer.wait_closed(), other_writer.wait_closed())
        await server.stop()


async def test_live_claw_typed_form_validation_connection_lease_and_no_model_turn(tmp_path):
    root = tmp_path / 'extensions'
    shutil.copytree(FIXTURE, root)
    calls = []
    def stream(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())
    config = ClawConfig(model='test/model', data_dir=str(tmp_path / 'data'),
        socket_path=str(tmp_path / 'forms.sock'), pid_file=str(tmp_path / 'forms.pid'),
        groups=(GroupDef(id='main', is_main=True),), extensions=('dialogue',), extension_dir=str(root))
    server = GatewayServer(config)
    with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=('test/model', stream)):
        await server.start()
    ui = ChatApp(config.socket_path)
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    other_reader, other_writer = await asyncio.open_unix_connection(config.socket_path)
    task = asyncio.create_task(ui._run_connection(reader, writer))
    async def wait(predicate):
        async with asyncio.timeout(8):
            while not predicate():
                ui._poll()
                await asyncio.sleep(0.005)
    try:
        await wait(lambda: ui._connection_ready)
        assert ui._extension_forms_supported
        ui._editor.set_text('parent draft')
        ui._extension_command('ext-dialogue-form', '')
        await wait(lambda: ui._extension_dialog_panel is not None)
        wrapper = ui._extension_dialog_panel
        key = wrapper.dialog['id']
        other_writer.write((json.dumps({'type': 'extension_dialog_answer', 'id': key,
                                       'value': {'name': 'foreign'}}) + '\n').encode())
        await other_writer.drain()
        assert not json.loads(await other_reader.readline())['accepted']
        writer.write((json.dumps({'type': 'extension_dialog_answer', 'id': key,
                                  'value': {'name': 'Alice', 'count': True}}) + '\n').encode())
        await writer.drain()
        await asyncio.sleep(0.03)
        ui._poll()
        assert ui._extension_pending is not None and wrapper._form._phase == 'edit'
        wrapper.handle_paste('Alice')
        wrapper.handle_input(KeyEvent('enter'))
        for _ in range(60):
            wrapper.render(80)
            if wrapper._form._visible:
                break
            wrapper.handle_input(KeyEvent('pagedown'))
        assert wrapper._form._visible
        wrapper.handle_input(KeyEvent('enter'))
        await wait(lambda: ui._extension_pending is None)
        assert ui._extension_dialog_panel is None and ui._editor.get_text() == 'parent draft'
        assert ui._tui._focused is ui._editor and not calls
    finally:
        ui._request_exit()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        writer.close()
        other_writer.close()
        await asyncio.gather(writer.wait_closed(), other_writer.wait_closed())
        await server.stop()


async def test_older_socket_viewer_cancels_form_instead_of_receiving_unknown_rendering(tmp_path):
    root = tmp_path / 'extensions'
    shutil.copytree(FIXTURE, root)
    config = ClawConfig(model='test/model', data_dir=str(tmp_path / 'data'),
        socket_path=str(tmp_path / 'oldforms.sock'), pid_file=str(tmp_path / 'oldforms.pid'),
        groups=(GroupDef(id='main', is_main=True),), extensions=('dialogue',), extension_dir=str(root))
    server = GatewayServer(config)
    with patch('xdog.claw.core.runtime.gateway._build_model_and_options',
               return_value=('test/model', lambda *args: EventStream.empty(AssistantMessage()))):
        await server.start()
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    try:
        writer.write(b'{"type":"ping","group_id":"main"}\n')  # No client form capability.
        await writer.drain()
        state = json.loads(await reader.readline())
        writer.write((json.dumps({'type': 'extension_control', 'request_id': 'old', 'group_id': 'main',
            'session_id': state.get('session_id', ''), 'action': 'command', 'name': 'ext-dialogue-form', 'args': ''})
                      + '\n').encode())
        await writer.drain()
        seen = []
        async with asyncio.timeout(6):
            while True:
                response = json.loads(await reader.readline())
                seen.append(response)
                if response['type'] == 'extension_result':
                    break
        assert not any(item['type'] == 'extension_dialog' for item in seen)
        assert response['success'] and response['output'] == 'FORM-RESULT-null'
    finally:
        writer.close()
        await writer.wait_closed()
        await server.stop()


@pytest.mark.parametrize('capability', [None, 0, True, 1])
async def test_form_capable_old_viewer_requires_explicit_multiselect_capability_and_withdrawal_settles(
    tmp_path, capability,
):
    root = tmp_path / 'extensions'
    shutil.copytree(FIXTURE, root)
    config = ClawConfig(model='test/model', data_dir=str(tmp_path / 'data'),
        socket_path=str(tmp_path / 'oldmany.sock'), pid_file=str(tmp_path / 'oldmany.pid'),
        groups=(GroupDef(id='main', is_main=True),), extensions=('dialogue',), extension_dir=str(root))
    server = GatewayServer(config)
    with patch('xdog.claw.core.runtime.gateway._build_model_and_options',
               return_value=('test/model', lambda *args: EventStream.empty(AssistantMessage()))):
        await server.start()
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    try:
        hello = {'type': 'ping', 'group_id': 'main', 'extension_forms': 1}
        if capability is not None:
            hello['extension_multi_forms'] = capability
        writer.write((json.dumps(hello) + '\n').encode())
        await writer.drain()
        state = json.loads(await reader.readline())
        assert state['extension_multi_forms'] == 1
        writer.write((json.dumps({'type': 'extension_control', 'request_id': 'many', 'group_id': 'main',
            'session_id': state.get('session_id', ''), 'action': 'command', 'name': 'ext-dialogue-many', 'args': ''})
                      + '\n').encode())
        await writer.drain()
        seen = []
        async with asyncio.timeout(8):
            while True:
                reply = json.loads(await reader.readline())
                seen.append(reply)
                if reply['type'] == 'extension_dialog':
                    assert type(capability) is int and capability == 1
                    assert reply['dialog']['fields'][0]['kind'] == 'multi_choice'
                    writer.write(b'{"type":"ping","extension_multi_forms":0}\n')
                    await writer.drain()
                if reply['type'] == 'extension_result':
                    break
        assert reply['success'] and reply['output'] == 'MANY-RESULT-null'
        if type(capability) is not int or capability != 1:
            assert not any(row['type'] == 'extension_dialog' for row in seen)
    finally:
        writer.close()
        await writer.wait_closed()
        await server.stop()
