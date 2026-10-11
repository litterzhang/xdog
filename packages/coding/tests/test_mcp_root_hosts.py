"""Configured root consent wired through actual hosts, no model or cwd disclosure."""
from __future__ import annotations

import asyncio
import json
import sys
from unittest.mock import patch

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.ai.types import AssistantMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.permissions import PermissionManager
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.settings_manager import SettingsManager

from packages.agent.tests.test_mcp_roots import ROOTS, write_peer


@pytest.mark.parametrize('kind', ['coding', 'claw'])
@pytest.mark.parametrize('consent', [False, True])
async def test_actual_host_explicit_connect_root_inventory_and_disconnect_revoke(tmp_path, kind, consent):
    peer, log = tmp_path / 'peer.py', tmp_path / 'frames'
    write_peer(peer)
    configuration = {'command': sys.executable, 'args': [str(peer), str(log), 'legacy'],
                     **({'roots': ROOTS} if consent else {})}
    calls = []
    def stream(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())
    session = server = writer = None
    if kind == 'coding':
        settings = tmp_path / 'settings.json'
        settings.write_text(json.dumps({'mcp_servers': {'owned': configuration}}))
        catalog = SessionManager(tmp_path / 'sessions')
        session = AgentSession(agent=Agent(stream, config=AgentConfig(model='offline/model')),
            session_data=catalog.create_session(working_dir=tmp_path), session_manager=catalog,
            settings=SettingsManager(global_path=settings), working_dir=tmp_path, tool_registry=None,
            bash=None, permissions=PermissionManager('deny'))
        manager = session.mcp
        async def control(action):
            return await session.manage_mcp(action, 'owned')
    else:
        config = ClawConfig(model='offline/model', data_dir=str(tmp_path / 'data'),
            socket_path=str(tmp_path / 'gateway.sock'), pid_file=str(tmp_path / 'gateway.pid'),
            groups=(GroupDef(id='main', is_main=True, workspace=str(tmp_path / 'workspace')),),
            mcp_servers={'owned': configuration})
        server = GatewayServer(config)
        with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=('offline/model', stream)):
            await server.start()
        manager = server._orchestrator._runtimes['main'].mcp
        reader, writer = await asyncio.open_unix_connection(config.socket_path)
        async def control(action):
            writer.write((json.dumps({'type': 'mcp_control', 'request_id': action, 'group_id': 'main',
                                     'server': 'owned', 'action': action}) + '\n').encode())
            await writer.drain()
            async with asyncio.timeout(5):
                while True:
                    row = json.loads(await reader.readline())
                    if row['type'] == 'mcp_result':
                        assert row['success'], row
                        return row
    try:
        assert not log.exists()  # Config alone is not peer startup or disclosure.
        await control('connect')
        client = manager._clients['owned']
        await manager._bridge(client.request('ping', {}))
        records = [json.loads(row) for row in log.read_text().splitlines()]
        response = next(row for row in records if row.get('id') == 'roots-query')
        if consent:
            assert response['result']['roots'] == ROOTS
        else:
            assert response['error']['code'] == -32601
        assert str(tmp_path) not in json.dumps(response)  # No inherited cwd root.
        assert manager.status()[0]['client_capabilities'] == ({'roots': {'listChanged': True}} if consent else {})
        assert not calls and not manager.tools()
        await control('disconnect')
        assert not client.root_capability.active
        assert manager.status()[0]['client_capabilities'] == {}
        if session is not None:
            assert session.permissions.mode == 'deny' and not session.messages
    finally:
        if writer is not None:
            writer.close()
            await writer.wait_closed()
        if server is not None:
            await server.stop()
        if session is not None:
            await session.children.supervisor.aclose()
            await session.extensions.aclose()
            await session.mcp.aclose()
            session.dispose()
