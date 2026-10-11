from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
from xdog.ai.types import AssistantMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer


@pytest.mark.parametrize('capability', [None, 0, True])
async def test_old_or_boolean_command_menu_capability_cannot_borrow_session_or_other_catalog_support(
    tmp_path, capability,
):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/commandreader', root / 'commandreader')
    config = ClawConfig(model='offline/model', data_dir=str(tmp_path / 'data'),
        socket_path=str(tmp_path / 'old.sock'), pid_file=str(tmp_path / 'old.pid'),
        groups=(GroupDef(id='main', is_main=True),), extensions=('commandreader',), extension_dir=str(root))
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
            'extension_session_inspection': 1, 'extension_navigation': 1, 'extension_session_name': 1,
            **({'extension_command_catalog': capability} if capability is not None else {})})
        state = await result('pong')
        assert state['extension_command_catalog'] == 1
        for command, args in [('inspect', '')]:
            await send({'type': 'extension_control', 'request_id': command, 'group_id': 'main',
                'session_id': original.session_id, 'action': 'command',
                'name': 'ext-commandreader-' + command, 'args': args})
            reply = await result('extension_result')
            assert reply['success']
            assert reply['output'] == 'COMMAND-MENU-UNAVAILABLE'
        assert runtime.transcript_store.get_active_session('main').label == 'Original' and not calls
    finally:
        writer.close()
        await writer.wait_closed()
        await server.stop()
