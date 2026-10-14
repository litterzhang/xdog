from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import patch

from xdog.ai.types import AssistantMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer

PEER = Path(__file__).parents[2] / 'agent/tests/fixtures/mcp_subscription_peer.py'


async def test_claw_catalog_capability_withdrawal_cleans_only_catalog_and_rejects_older_viewer_request(tmp_path):
    log = tmp_path / 'requests'
    config = ClawConfig(model='test/model', data_dir=str(tmp_path / 'data'),
        socket_path=str(tmp_path / 'caps.sock'), pid_file=str(tmp_path / 'caps.pid'),
        groups=(GroupDef(id='main', is_main=True),), mcp_servers={'peer': {
            'command': sys.executable, 'args': [str(PEER), 'modern', str(log)]}})
    server = GatewayServer(config)
    with patch('xdog.claw.core.runtime.gateway._build_model_and_options',
               return_value=('test/model', lambda *args: EventStream.empty(AssistantMessage()))):
        await server.start()
    await server._orchestrator.manage_mcp('main', 'connect', 'peer')
    manager = server._orchestrator._runtimes['main'].mcp
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    async def send(value):
        writer.write((json.dumps(value) + '\n').encode())
        await writer.drain()
    async def receive(kind):
        async with asyncio.timeout(5):
            while True:
                value = json.loads(await reader.readline())
                if value['type'] == kind:
                    return value
    try:
        await send({'type': 'ping', 'group_id': 'main', 'mcp_catalog_watch': 1})
        state = await receive('pong')
        view = 'a' * 32
        base = {'type': 'mcp_resource_watch', 'group_id': 'main', 'session_id': state.get('session_id', ''),
                'view': view, 'server': 'peer', 'action': 'watch'}
        await send({**base, 'request_id': 'resource', 'uri': 'test://watched'})
        assert (await receive('mcp_resource_watch_result'))['success']
        await send({**base, 'request_id': 'catalog', 'uri': '', 'catalog': 'tools'})
        assert (await receive('mcp_resource_watch_result'))['success']
        assert len(manager._resource_watches) == 2
        await send({'type': 'ping', 'mcp_catalog_watch': 0})
        await receive('pong')
        async with asyncio.timeout(5):
            while len(manager._resource_watches) != 1:
                await asyncio.sleep(0.003)
        assert next(iter(manager._resource_watches))[2] == 'resource'
        await send({**base, 'request_id': 'old', 'uri': '', 'catalog': 'prompts'})
        assert not (await receive('mcp_resource_watch_result'))['success']
    finally:
        writer.close()
        await writer.wait_closed()
        await server.stop()
