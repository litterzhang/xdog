from __future__ import annotations

import asyncio
import json
import os
import shutil
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

FIXTURE = Path(__file__).parents[2] / 'agent/tests/fixtures/mcp_subscription_peer.py'


@pytest.fixture
async def sdk_http_peer(request, tmp_path):
    """Own the independent HTTP peer even if application setup/assertions fail."""
    if request.node.callspec.params['transport'] != 'http':
        yield None
        return
    if not os.environ.get('XDOG_MCP_OFFICIAL_SDK_ROOT') or not shutil.which('node'):
        pytest.skip('requires independently installed pinned MCP SDK 2.3.1')
    peer = Path(__file__).parents[2] / 'agent/tests/fixtures/mcp_official_sdk/peer.mjs'
    process = await asyncio.create_subprocess_exec(shutil.which('node'), str(peer), 'http',
        str(tmp_path / 'messages'), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        async with asyncio.timeout(5):
            assert process.stdout is not None
            port = json.loads(await process.stdout.readline())['port']
        yield {'transport': 'http', 'url': f'http://127.0.0.1:{port}/mcp'}
    finally:
        if process.returncode is None:
            process.terminate()
        await asyncio.wait_for(process.wait(), 5)


@pytest.mark.parametrize('mode,transport', [('modern', 'stdio'), ('legacy', 'stdio'),
                                          ('official-sdk', 'stdio'), ('official-sdk', 'http')])
async def test_claw_actual_gateway_watch_notice_unwatch_and_connection_view_cleanup(
    tmp_path, mode, transport, sdk_http_peer,
):
    if mode == 'official-sdk' and (not os.environ.get('XDOG_MCP_OFFICIAL_SDK_ROOT') or not shutil.which('node')):
        pytest.skip('requires independently installed pinned MCP SDK 2.3.1')
    peer = (Path(__file__).parents[2] / 'agent/tests/fixtures/mcp_official_sdk/peer.mjs'
            if mode == 'official-sdk' else FIXTURE)
    executable = shutil.which('node') if mode == 'official-sdk' else sys.executable
    peer_mode = 'stdio' if mode == 'official-sdk' else mode
    server_config = sdk_http_peer or {
        'command': executable, 'args': [str(peer), peer_mode, str(tmp_path / 'messages')]}
    log, calls = tmp_path / 'messages', []
    def stream(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())
    config = ClawConfig(model='test/model', data_dir=str(tmp_path / 'data'),
        socket_path=str(tmp_path / 'watch.sock'), pid_file=str(tmp_path / 'watch.pid'),
        groups=(GroupDef(id='main', is_main=True),), mcp_servers={'peer': server_config})
    server = GatewayServer(config)
    with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=('test/model', stream)):
        await server.start()
    ui = ChatApp(config.socket_path)
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    task = asyncio.create_task(ui._run_connection(reader, writer))
    async def wait(predicate):
        async with asyncio.timeout(5):
            while not predicate():
                ui._poll()
                await asyncio.sleep(0.003)
    try:
        await wait(lambda: ui._connection_ready)
        assert ui._mcp_watch_supported
        await server._orchestrator.manage_mcp('main', 'connect', 'peer')
        manager = server._orchestrator._runtimes['main'].mcp
        ui._editor.set_text('parent draft')
        ui._open_mcp()
        panel = ui._mcp_panel
        await wait(lambda: not panel._busy)
        panel.handle_input(KeyEvent('u'))
        await wait(lambda: not panel._busy and panel._kind == 'resources')
        panel.handle_input(KeyEvent('w'))
        await wait(lambda: not panel._busy
                   and panel._watches.get(('peer', '', 'test://watched'), {}).get('state') == 'active')
        await manager._bridge(manager._clients['peer'].request('tools/call', {'name': 'tick', 'arguments': {}},
                **({'mirrors': {'Mcp-Name': 'tick'}} if transport == 'http' else {}))
            if mode == 'official-sdk' else manager._clients['peer'].request('fixture/update', {}))
        await wait(lambda: panel._watches['peer', '', 'test://watched']['changes'] == 1)
        assert ui._mcp_document is None and not calls and 'resources/read' not in log.read_text()
        if mode == 'modern':
            await manager._bridge(manager._clients['peer'].request('fixture/end', {}))
            await wait(lambda: panel._watches['peer', '', 'test://watched']['state'] == 'ended')
            assert log.read_text().count('subscriptions/listen') == 1
            panel.handle_input(KeyEvent('w'))  # Only the user requests a fresh watch.
            await wait(lambda: not panel._busy and panel._watches['peer', '', 'test://watched']['state'] == 'active')
        if mode == 'official-sdk':
            await manager._bridge(manager._clients['peer'].request(
                'tools/call', {'name': 'finish', 'arguments': {}},
                **({'mirrors': {'Mcp-Name': 'finish'}} if transport == 'http' else {})))
            await wait(lambda: panel._watches['peer', '', 'test://watched']['state'] == 'ended')
            assert 'resource-read' not in log.read_text()
        old_view = ui._mcp_watch_view
        ui._close_mcp()
        await wait(lambda: not manager._resource_watches)
        ui._handle_response({'type': 'mcp_resource_watch_notice', 'view': old_view,
            'group_id': 'main', 'session_id': ui._gateway_session_id, 'data': {'state': 'active', 'changes': 999}})
        assert ui._mcp_panel is None and ui._editor.get_text() == 'parent draft'
    finally:
        ui._request_exit()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        writer.close()
        await writer.wait_closed()
        await server.stop()
