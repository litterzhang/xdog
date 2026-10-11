from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.ai.types import AssistantMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.permissions import PermissionManager
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.settings_manager import SettingsManager
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
from xdog.tui.keys import KeyEvent

PEER = Path(__file__).parents[2] / 'agent/tests/fixtures/mcp_subscription_peer.py'


@pytest.mark.parametrize('kind', ['coding', 'claw'])
async def test_actual_combined_watch_edit_toggle_cleanup_without_reads_or_model(tmp_path, kind):
    mode = "modern"
    log = tmp_path / 'requests'
    servers = {'peer': {'command': sys.executable, 'args': [str(PEER), mode, str(log)]}}
    calls = []
    def stream(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())
    server = None
    task = writer = None
    if kind == 'coding':
        settings = tmp_path / 'settings.json'
        settings.write_text(json.dumps({'mcp_servers': servers}))
        store = SessionManager(tmp_path / 'sessions')
        session = AgentSession(agent=Agent(stream, config=AgentConfig(model='test/model')),
            session_data=store.create_session(working_dir=tmp_path), session_manager=store,
            settings=SettingsManager(global_path=settings), working_dir=tmp_path,
            tool_registry=None, bash=None, permissions=PermissionManager('ask'))
        ui = InteractiveMode(session)
        await session.manage_mcp('connect', 'peer')
        manager = session.mcp
    else:
        config = ClawConfig(model='test/model', data_dir=str(tmp_path / 'data'),
            socket_path=str(tmp_path / 'watch.sock'), pid_file=str(tmp_path / 'watch.pid'),
            groups=(GroupDef(id='main', is_main=True),), mcp_servers=servers)
        server = GatewayServer(config)
        with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=('test/model', stream)):
            await server.start()
        ui = ChatApp(config.socket_path)
        reader, writer = await asyncio.open_unix_connection(config.socket_path)
        task = asyncio.create_task(ui._run_connection(reader, writer))
        await server._orchestrator.manage_mcp('main', 'connect', 'peer')
        manager = server._orchestrator._runtimes['main'].mcp
    async def wait(predicate):
        async with asyncio.timeout(8):
            while not predicate():
                ui._poll()
                await asyncio.sleep(0.003)
    try:
        if kind == 'claw':
            await wait(lambda: ui._connection_ready)
            assert ui._mcp_catalog_watch_supported
        ui._editor.set_text('kept catalog draft')
        ui._open_mcp()
        panel = ui._mcp_panel
        await wait(lambda: not panel._busy)
        before = log.read_text().splitlines()
        panel.handle_input(KeyEvent('w', alt=True))
        assert panel._mode == 'subscriptions'
        filters = {'toolsListChanged': True, 'promptsListChanged': True,
                   'resourceSubscriptions': ['test://one', 'test://two']}
        from xdog.tui.mcp_subscription import filter_text
        key = ('peer', 'notifications', filter_text(filters))
        panel._arguments.set_text(json.dumps(filters))
        panel.handle_input(KeyEvent('enter'))
        await wait(lambda: not panel._busy and panel._watches.get(key, {}).get('state') == 'active')
        assert panel._arguments.get_text() == json.dumps(filters)
        await manager._bridge(manager._clients['peer'].request('fixture/update', {}))
        await wait(lambda: panel._watches[key]['changes'] == 4)
        assert 'combined notification' in panel._message and 'no automatic read' in panel._message
        assert ui._mcp_document is None and not calls
        new = [json.loads(line) for line in log.read_text().splitlines()[len(before):]]
        assert not any(frame['method'] in ('tools/list', 'resources/list', 'prompts/list',
                                           'resources/read', 'tools/call')
                       for frame in new)
        listens = [frame for frame in new if frame['method'] == 'subscriptions/listen']
        assert len(listens) == 1 and listens[0]['params']['notifications'] == filters
        panel.handle_input(KeyEvent('enter'))
        await wait(lambda: not panel._busy and panel._watches[key]['state'] == 'stopped')
        panel.handle_input(KeyEvent('enter'))
        await wait(lambda: not panel._busy and panel._watches[key]['state'] == 'active')
        ui._close_mcp()
        await wait(lambda: not manager._resource_watches)
        assert ui._editor.get_text() == 'kept catalog draft' and ui._tui._focused is ui._editor and not calls
    finally:
        ui._request_exit()
        if kind == 'coding':
            await manager.aclose()
            session.dispose()
        else:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            writer.close()
            await writer.wait_closed()
            await server.stop()


async def test_claw_combined_capability_withdrawal_preserves_resource_watch_and_refuses_old_viewer(tmp_path):
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
        await send({'type': 'ping', 'group_id': 'main'})
        state = await receive('pong')
        view = 'a' * 32
        base = {'type': 'mcp_resource_watch', 'group_id': 'main', 'session_id': state.get('session_id', ''),
                'view': view, 'server': 'peer', 'action': 'watch'}
        await send({**base, 'request_id': 'resource', 'uri': 'test://watched'})
        assert (await receive('mcp_resource_watch_result'))['success']
        await send({**base, 'request_id': 'old-first', 'uri': '', 'notifications': {'toolsListChanged': True}})
        assert not (await receive('mcp_resource_watch_result'))['success']
        await send({'type': 'ping', 'mcp_combined_watch': 1})
        await receive('pong')
        await send({**base, 'request_id': 'combined', 'uri': '', 'notifications': {
            'toolsListChanged': True, 'resourceSubscriptions': ['test://watched']}})
        assert (await receive('mcp_resource_watch_result'))['success']
        assert len(manager._resource_watches) == 2
        await send({'type': 'ping', 'mcp_combined_watch': 0})
        await receive('pong')
        async with asyncio.timeout(5):
            while len(manager._resource_watches) != 1:
                await asyncio.sleep(0.003)
        assert next(iter(manager._resource_watches))[2] == 'resource'
        await send({**base, 'request_id': 'old', 'uri': '', 'notifications': {'promptsListChanged': True}})
        assert not (await receive('mcp_resource_watch_result'))['success']
        await send({**base, 'request_id': 'null-no-downgrade', 'uri': 'test://watched', 'notifications': None})
        assert not (await receive('mcp_resource_watch_result'))['success']
    finally:
        writer.close()
        await writer.wait_closed()
        await server.stop()
