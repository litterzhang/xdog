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


@pytest.mark.parametrize('capability', [None, 0, True])
async def test_old_or_boolean_name_capability_cannot_borrow_inspection_or_navigation_support(tmp_path, capability):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/namer', root / 'namer')
    config = ClawConfig(model='offline/model', data_dir=str(tmp_path / 'data'),
        socket_path=str(tmp_path / 'old.sock'), pid_file=str(tmp_path / 'old.pid'),
        groups=(GroupDef(id='main', is_main=True),), extensions=('namer',), extension_dir=str(root))
    calls = []
    def stream(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())
    server = GatewayServer(config)
    with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=(config.model, stream)):
        await server.start()
    runtime = server._orchestrator._runtimes['main']
    original = runtime.transcript_store.create_session('main')
    runtime.transcript_store.rename_session('main', original.session_id, 'Original')
    reader, writer = await asyncio.open_unix_connection(config.socket_path, limit=4 * 1024 * 1024)
    async def send(data):
        writer.write((json.dumps(data) + '\n').encode())
        await writer.drain()
    async def result(kind):
        async with asyncio.timeout(8):
            while True:
                reply = json.loads(await reader.readline())
                assert reply['type'] != 'extension_dialog', 'Unsupported name must not degrade into a dialog'
                if reply['type'] == kind:
                    return reply
    try:
        await send({'type': 'ping', 'group_id': 'main', 'extension_session': 1,
            'extension_session_inspection': 1, 'extension_navigation': 1,
            **({'extension_session_name': capability} if capability is not None else {})})
        state = await result('pong')
        assert state['extension_session_name'] == 1
        for command, args in [('inspect', ''), ('rename', '{"name":"Never apply"}')]:
            await send({'type': 'extension_control', 'request_id': command, 'group_id': 'main',
                'session_id': original.session_id, 'action': 'command', 'name': 'ext-namer-' + command, 'args': args})
            reply = await result('extension_result')
            assert reply['success']
            if command == 'inspect':
                assert reply['output'] == 'NAME-INSPECT {"name": null}'
            else:
                assert '"accepted": false' in reply['output']
        assert runtime.transcript_store.get_active_session('main').label == 'Original' and not calls
    finally:
        writer.close()
        await writer.wait_closed()
        await server.stop()


def test_name_queue_cannot_reuse_withdrawn_or_foreign_command_owner():
    ui = ChatApp('/tmp/unused-extension-name.sock')
    try:
        ui._handle_response({'type': 'pong', 'session_controls': 1, 'session_rename': 1,
            'extension_session': 1, 'extension_session_name': 1, 'session_id': 'session'})
        context = ui._mcp_context_key()
        target = {'action': 'rename', 'name': 'Never apply', 'expected_name': ''}
        ui._extension_navigation = 'old', context, target
        assert not ui._finish_extension_navigation('new', True)
        ui._extension_navigation = 'owner', context, target
        ui._handle_response({'type': 'pong', 'session_controls': 1, 'session_rename': 1,
                             'extension_session': 1, 'session_id': 'session'})
        assert not ui._finish_extension_navigation('owner', True)
        assert ui._send_queue.empty()
    finally:
        ui._request_exit()
