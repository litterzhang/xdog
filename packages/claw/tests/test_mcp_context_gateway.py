from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from xdog.ai.types import AssistantMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer
from xdog.tui.keys import KeyEvent

FIXTURE = Path(__file__).parents[2] / "agent" / "tests" / "fixtures" / "mcp_server.py"


async def test_actual_gateway_client_catalog_chunked_preview_and_manual_draft_insertion(tmp_path):
    log = tmp_path / "requests"
    config = ClawConfig(model="test/model", data_dir=str(tmp_path / "data"), socket_path=str(tmp_path / "context.sock"),
                        pid_file=str(tmp_path / "context.pid"), groups=(GroupDef(id="main", is_main=True),),
                        mcp_servers={"fixture": {"command": sys.executable,
                                     "args": [str(FIXTURE), "modern", str(log)]}})
    calls = []
    def stream(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())
    server = GatewayServer(config)
    with patch("xdog.claw.core.runtime.gateway._build_model_and_options", return_value=("test/model", stream)):
        await server.start()
        ui = ChatApp(config.socket_path)
        ui._io_generation = 1
        ui._handle_response({"type": "_connection_opened", "generation": 1})
        reader, writer = await asyncio.open_unix_connection(config.socket_path, limit=4 * 1024 * 1024)
        task = asyncio.create_task(ui._run_connection(reader, writer))
        async def wait(predicate):
            deadline = asyncio.get_running_loop().time() + 5
            while asyncio.get_running_loop().time() < deadline:
                ui._poll()
                if predicate():
                    return
                await asyncio.sleep(0.005)
            raise AssertionError("MCP context browser did not settle")
        try:
            await wait(lambda: ui._connection_ready)
            ui._editor.set_text("preserved draft")
            ui._open_mcp()
            panel = ui._mcp_panel
            await wait(lambda: not panel._busy)
            assert not log.exists()
            panel.handle_input(KeyEvent("c"))
            await wait(lambda: not panel._busy)
            panel.handle_input(KeyEvent("u"))
            await wait(lambda: not panel._busy)
            assert panel._mode == "catalog"
            panel.handle_input(KeyEvent("enter"))
            await wait(lambda: not panel._busy)
            assert len(ui._mcp_document.text) > 200000 and "RESOURCE-TAIL" in ui._mcp_document.text
            assert ui._editor.get_text() == "preserved draft" and not calls
            panel.handle_input(KeyEvent("i"))
            assert ui._mcp_panel is None and "RESOURCE-TAIL" in ui._editor.get_text()
            assert "preserved draft" in ui._editor.get_text() and not calls
            frames = [json.loads(line) for line in log.read_text().splitlines()]
            assert not any(frame["method"] == "tools/call" for frame in frames)
            ui._open_mcp()
            panel = ui._mcp_panel
            await wait(lambda: not panel._busy)
            panel.handle_input(KeyEvent("u"))
            await wait(lambda: not panel._busy)
            panel.handle_input(KeyEvent("down"))
            panel.handle_input(KeyEvent("enter"))
            await wait(lambda: not panel._busy)
            assert ui._mcp_document.blocks[0].data == "AAECAw=="
            draft = ui._editor.get_text()
            panel.handle_input(KeyEvent("i"))
            assert ui._mcp_panel is panel and ui._editor.get_text() == draft
            panel.handle_input(KeyEvent("e"))
            await wait(lambda: ui._outputs.panel is not None)
            assert "Output review" in ui._hints._text
            assert ui._layout.details is ui._outputs.panel and ui._tui._focused is ui._outputs.panel
            assert "AAECAw==" in ui._outputs._prepared.data.decode()
            assert ui._outputs._root == ui._file_completions.root
            ui._outputs.cancel()
            ui._poll()
            assert ui._layout.details is panel and ui._tui._focused is panel
            assert "MCP focused" in ui._hints._text
            old = ui._mcp_active
            ui._close_mcp()
            ui._handle_response({"type": "mcp_data_ready", "request_id": old, "records": ()})
            assert ui._mcp_panel is None and ui._mcp_document is None and not calls
        finally:
            ui._request_exit()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            writer.close()
            await writer.wait_closed()
            await server.stop()


async def test_slow_context_read_keeps_gateway_ping_and_cancel_responsive(tmp_path):
    config = ClawConfig(model="test/model", data_dir=str(tmp_path / "data"),
                        socket_path=str(tmp_path / "context.sock"), pid_file=str(tmp_path / "context.pid"),
                        groups=(GroupDef(id="main", is_main=True),))
    server = GatewayServer(config)
    entered, canceled = asyncio.Event(), asyncio.Event()
    async def slow(*args):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            canceled.set()
    with patch("xdog.claw.core.runtime.gateway._build_model_and_options",
               return_value=("test/model", lambda *args: EventStream.empty(AssistantMessage()))):
        await server.start()
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    async def send(value):
        writer.write((json.dumps(value) + "\n").encode())
        await writer.drain()
    async def pong():
        async with asyncio.timeout(2):
            while True:
                value = json.loads(await reader.readline())
                if value["type"] == "pong":
                    return value
    try:
        await send({"type": "ping", "group_id": "main"})
        state = await pong()
        with patch.object(server._orchestrator, "mcp_context", slow):
            await send({"type": "mcp_context", "request_id": "slow", "group_id": "main",
                        "session_id": state.get("session_id", ""), "action": "read",
                        "server": "fixture", "name": "uri"})
            await asyncio.wait_for(entered.wait(), 2)
            await send({"type": "ping", "group_id": "main"})
            assert (await pong())["mcp_context"] == 1
            await send({"type": "mcp_context_cancel", "request_id": "stale"})
            await asyncio.sleep(0.02)
            assert not canceled.is_set()
            await send({"type": "mcp_context_cancel", "request_id": "slow"})
            await asyncio.wait_for(canceled.wait(), 2)
            await send({"type": "ping", "group_id": "main"})
            await pong()
    finally:
        writer.close()
        await writer.wait_closed()
        await server.stop()


async def test_claw_completion_is_negotiated_chunked_reviewed_and_keeps_parent_unsent(tmp_path):
    log = tmp_path / 'completion-requests'
    calls = []
    config = ClawConfig(model='test/model', data_dir=str(tmp_path / 'data'),
                        socket_path=str(tmp_path / 'completion.sock'), pid_file=str(tmp_path / 'completion.pid'),
                        groups=(GroupDef(id='main', is_main=True),),
                        mcp_servers={'fixture': {'command': sys.executable,
                                     'args': [str(FIXTURE), 'modern', str(log)]}})
    def stream(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())
    server = GatewayServer(config)
    with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=('test/model', stream)):
        await server.start()
    ui = ChatApp(config.socket_path)
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    task = asyncio.create_task(ui._run_connection(reader, writer))
    async def wait(predicate):
        async with asyncio.timeout(6):
            while not predicate():
                ui._poll()
                await asyncio.sleep(0.005)
    try:
        await wait(lambda: ui._connection_ready)
        assert ui._mcp_completion_supported
        ui._editor.set_text('parent draft')
        ui._open_mcp()
        panel = ui._mcp_panel
        await wait(lambda: not panel._busy)
        panel.handle_input(KeyEvent('c'))
        await wait(lambda: not panel._busy)
        panel.handle_input(KeyEvent('p'))
        await wait(lambda: not panel._busy)
        panel.handle_input(KeyEvent('enter'))
        panel.handle_input(KeyEvent('space', ctrl=True))
        ui._mcp_completion_supported = False  # Older/missing gateway negotiation must not send a request.
        panel.handle_paste('co')
        panel.handle_input(KeyEvent('tab'))
        await wait(lambda: not panel._busy and panel.completion._receipt is None)
        assert 'Updated argument-completion gateway required' in panel.completion._message
        assert panel.completion._input.get_text() == 'co'
        assert not any(json.loads(line)['method'] == 'completion/complete' for line in log.read_text().splitlines())
        ui._mcp_completion_supported = True
        panel.completion._input.set_text('')
        panel.handle_input(KeyEvent('tab'))
        await wait(lambda: not panel._busy and panel.completion._receipt is None)
        assert panel.completion._values == ('completed value', 'alternative')
        from xdog.tui.components.text import Text
        protected = ui._tui.show_overlay(Text('Protected host surface'))
        assert not panel.completion.focused
        panel.render(80)
        panel.handle_paste('wrong editor')
        panel.handle_input(KeyEvent('enter'))
        assert panel._mode == 'completion' and panel._arguments.get_text() == '{}'
        assert not panel.completion._visible and panel.completion._input.get_text() == ''
        protected.hide()
        panel.handle_input(KeyEvent('enter'))
        assert panel._mode == 'completion'
        panel.render(80)
        panel.handle_input(KeyEvent('enter'))
        assert json.loads(panel._arguments.get_text()) == {'topic': 'completed value'}
        assert not calls and ui._editor.get_text() == 'parent draft' and ui._mcp_document is None
        panel.handle_input(KeyEvent('escape'))
        await wait(lambda: not panel._busy)
        panel.handle_input(KeyEvent('t'))
        await wait(lambda: not panel._busy)
        panel.handle_input(KeyEvent('enter'))
        panel.handle_input(KeyEvent('space', ctrl=True))
        panel.handle_input(KeyEvent('tab'))
        await wait(lambda: not panel._busy and panel.completion._receipt is None)
        assert panel.completion._values
        panel.render(80)
        panel.handle_input(KeyEvent('enter'))
        assert panel._arguments.get_text() == 'fixture://document/completed%20value'
        frames = [json.loads(line) for line in log.read_text().splitlines()]
        assert sum(frame['method'] == 'completion/complete' for frame in frames) == 2
        assert not any(frame['method'] in ('tools/call', 'resources/read', 'prompts/get') for frame in frames)
        assert not calls and ui._editor.get_text() == 'parent draft'
        ui._close_mcp()
        assert ui._tui._focused is ui._editor
    finally:
        ui._request_exit()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        writer.close()
        await writer.wait_closed()
        await server.stop()


@pytest.mark.parametrize('completion_only', [False, True])
async def test_claw_opaque_completion_negotiates_separately_and_never_reads_selected_value(tmp_path, completion_only):
    peer = FIXTURE.with_name('mcp_completion_peer.py') if completion_only else FIXTURE
    mode = 'completion-only' if completion_only else 'modern'
    expected = 'completed value' if completion_only else 'OPAQUE-ARGUMENT-VALUE'
    log = tmp_path / 'opaque-requests'
    calls = []
    config = ClawConfig(model='test/model', data_dir=str(tmp_path / 'data'),
        socket_path=str(tmp_path / 'opaque.sock'), pid_file=str(tmp_path / 'opaque.pid'),
        groups=(GroupDef(id='main', is_main=True),), mcp_servers={'fixture': {'command': sys.executable,
        'args': [str(peer), mode, str(log)]}})
    def stream(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())
    server = GatewayServer(config)
    with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=('test/model', stream)):
        await server.start()
    ui = ChatApp(config.socket_path)
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    task = asyncio.create_task(ui._run_connection(reader, writer))
    async def wait(predicate):
        async with asyncio.timeout(6):
            while not predicate():
                ui._poll()
                await asyncio.sleep(0.005)
    try:
        await wait(lambda: ui._connection_ready)
        assert ui._mcp_resource_completion_supported and ui._mcp_completion_supported
        ui._editor.set_text('parent draft')
        ui._open_mcp()
        panel = ui._mcp_panel
        await wait(lambda: not panel._busy)
        panel.handle_input(KeyEvent('c'))
        await wait(lambda: not panel._busy)
        if not completion_only:
            panel.handle_input(KeyEvent('u'))
            await wait(lambda: not panel._busy)
        panel.handle_input(KeyEvent('v'))
        panel._arguments.set_text(json.dumps({'uri': 'fixture://text', 'argument': 'opaque-field',
                                             'value': 'pre', 'context': {'language': '中文'}}))
        panel.handle_input(KeyEvent('space', ctrl=True))
        ui._mcp_resource_completion_supported = False
        panel.handle_input(KeyEvent('tab'))
        await wait(lambda: not panel._busy and panel.completion._receipt is None)
        assert 'opaque-resource-completion gateway required' in panel.completion._message
        assert panel.completion._input.get_text() == 'pre'
        assert not any(json.loads(line)['method'] == 'completion/complete' for line in log.read_text().splitlines())
        ui._mcp_resource_completion_supported = True
        panel.handle_input(KeyEvent('tab'))
        await wait(lambda: not panel._busy and panel.completion._receipt is None)
        assert panel.completion._values[0] == expected
        panel.render(80)
        panel.handle_input(KeyEvent('enter'))
        assert ui._mcp_document is None and ui._editor.get_text() == 'parent draft'
        panel.handle_input(KeyEvent('enter'))
        assert panel._mode == 'preview' and ui._mcp_document.kind == 'resource-completion'
        frames = [json.loads(line) for line in log.read_text().splitlines()]
        assert len([frame for frame in frames if frame['method'] == 'completion/complete']) == 1
        assert not any(frame['method'] in ('resources/read', 'prompts/get', 'tools/call') for frame in frames)
        assert not calls and ui._editor.get_text() == 'parent draft'
        panel.handle_input(KeyEvent('i'))
        assert ui._mcp_panel is None and 'parent draft' in ui._editor.get_text()
        assert expected in ui._editor.get_text() and not calls
    finally:
        ui._request_exit()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        writer.close()
        await writer.wait_closed()
        await server.stop()


async def test_live_claw_empty_binary_typed_preview_rejects_draft_and_preserves_export(tmp_path):
    log = tmp_path / 'requests'
    config = ClawConfig(model='test/model', data_dir=str(tmp_path / 'data'),
        socket_path=str(tmp_path / 'empty.sock'), pid_file=str(tmp_path / 'empty.pid'),
        groups=(GroupDef(id='main', is_main=True),), mcp_servers={'fixture': {
            'command': sys.executable, 'args': [str(FIXTURE), 'empty-content', str(log)]}})
    calls = []
    def stream(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())
    server = GatewayServer(config)
    with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=('test/model', stream)):
        await server.start()
    ui = ChatApp(config.socket_path)
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    task = asyncio.create_task(ui._run_connection(reader, writer))
    async def wait(predicate):
        async with asyncio.timeout(8):
            while not predicate():
                ui._poll()
                await asyncio.sleep(0.003)
    try:
        await wait(lambda: ui._connection_ready)
        assert ui._mcp_content_types_supported
        ui._editor.set_text('kept draft')
        ui._open_mcp()
        panel = ui._mcp_panel
        await wait(lambda: not panel._busy)
        panel.handle_input(KeyEvent('c'))
        await wait(lambda: not panel._busy)
        panel.handle_input(KeyEvent('u'))
        await wait(lambda: not panel._busy)
        panel.handle_input(KeyEvent('enter'))
        await wait(lambda: not panel._busy)
        assert ui._mcp_document.blocks[0].content_type == 'resource_blob'
        assert '0 bytes' in ui._mcp_document.text and 'EMPTY-BINARY-METADATA' in ui._mcp_document.text
        panel.handle_input(KeyEvent('i'))
        assert ui._mcp_panel is panel and ui._editor.get_text() == 'kept draft'
        panel.handle_input(KeyEvent('e'))
        await wait(lambda: ui._outputs.panel is not None)
        exported = json.loads(ui._outputs._prepared.data)['records'][0]['body']
        block = json.loads(exported)['blocks'][0]
        assert block['content_type'] == 'resource_blob' and block['data'] == ''
        ui._outputs.cancel()
        ui._poll()
        ui._close_mcp()
        assert not calls and ui._editor.get_text() == 'kept draft' and ui._tui._focused is ui._editor
        methods = [json.loads(line)['method'] for line in log.read_text().splitlines()]
        assert methods.count('resources/read') == 1 and 'tools/call' not in methods
    finally:
        ui._request_exit()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        writer.close()
        await writer.wait_closed()
        await server.stop()


@pytest.mark.parametrize('capability', [None, 0, True, 1])
async def test_socket_viewer_empty_binary_requires_exact_content_type_capability_without_replay(tmp_path, capability):
    from xdog.agent.mcp.context import McpBlock, McpDocument

    config = ClawConfig(model='test/model', data_dir=str(tmp_path / 'data'),
        socket_path=str(tmp_path / 'legacy-empty.sock'), pid_file=str(tmp_path / 'legacy-empty.pid'),
        groups=(GroupDef(id='main', is_main=True),))
    server = GatewayServer(config)
    with patch('xdog.claw.core.runtime.gateway._build_model_and_options',
               return_value=('test/model', lambda *args: EventStream.empty(AssistantMessage()))):
        await server.start()
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    calls = []
    async def fetch(*args):
        calls.append(args)
        return McpDocument('peer', 'resource', 'test://empty', (
            McpBlock('resource', data='', content_type='resource_blob'),))
    async def send(value):
        writer.write((json.dumps(value) + '\n').encode())
        await writer.drain()
    try:
        hello = {'type': 'ping', 'group_id': 'main'}
        if capability is not None:
            hello['mcp_content_types'] = capability
        await send(hello)
        state = json.loads(await reader.readline())
        assert state['mcp_content_types'] == 1
        with patch.object(server._orchestrator, 'mcp_context', fetch):
            await send({'type': 'mcp_context', 'request_id': 'empty', 'group_id': 'main',
                        'session_id': state.get('session_id', ''), 'action': 'read',
                        'server': 'peer', 'name': 'test://empty', 'arguments': {}})
            frames = []
            async with asyncio.timeout(5):
                while True:
                    frame = json.loads(await reader.readline())
                    frames.append(frame)
                    if frame['type'] in ('mcp_data_end', 'mcp_data_error'):
                        break
        assert len(calls) == 1
        if type(capability) is int and capability == 1:
            from xdog.claw.channels.tui.mcp_data import McpDataReceiver
            events = []
            receiver = McpDataReceiver(events.append)
            receiver.expect('empty', 'main', state.get('session_id', ''))
            try:
                for frame in frames:
                    receiver.feed(frame)
                assert events[-1]['type'] == 'mcp_data_ready'
                assert events[-1]['records'].blocks[0].is_binary
            finally:
                receiver.close()
        else:
            assert [frame['type'] for frame in frames] == ['mcp_data_error']
            assert 'updated typed-content viewer' in frames[0]['error']
    finally:
        writer.close()
        await writer.wait_closed()
        await server.stop()


def test_upgraded_client_does_not_infer_empty_legacy_payload_type_from_mime_or_metadata(tmp_path):
    from xdog.agent.mcp.context import McpBlock, McpDocument

    ui = ChatApp(str(tmp_path / 'not-connected.sock'))
    try:
        ui._editor.set_text('kept draft')
        ui._mcp_document = McpDocument('peer', 'resource', 'test://empty', (
            McpBlock('resource', text='[MCP content metadata]\n{"vendor/empty":true}',
                     mime_type='application/octet-stream'),))
        ui._mcp_owner_context = ui._mcp_context_key()
        assert not ui._mcp_content_types_supported
        ui._mcp_insert()
        assert ui._editor.get_text() == 'kept draft' and ui._mcp_document is not None
        assert 'payload type is unreported' in '\n'.join(ui._chat_log.render(120))
    finally:
        ui._request_exit()
