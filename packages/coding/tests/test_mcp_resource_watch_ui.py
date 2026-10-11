from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from pathlib import Path

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.ai.types import AssistantMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.permissions import PermissionManager
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.settings_manager import SettingsManager
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
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
async def test_coding_actual_watch_notice_unwatch_and_view_close_without_reads_or_model_input(
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
    log, settings = tmp_path / 'messages', tmp_path / 'settings.json'
    settings.write_text(json.dumps({'mcp_servers': {'peer': server_config}}))
    calls = []
    def stream(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())
    store = SessionManager(tmp_path / 'sessions')
    session = AgentSession(agent=Agent(stream, config=AgentConfig(model='test/model')),
        session_data=store.create_session(working_dir=tmp_path), session_manager=store,
        settings=SettingsManager(global_path=settings), working_dir=tmp_path,
        tool_registry=None, bash=None, permissions=PermissionManager('ask'))
    ui = InteractiveMode(session)
    async def wait(predicate):
        async with asyncio.timeout(5):
            while not predicate():
                ui._poll()
                await asyncio.sleep(0.003)
    try:
        await session.manage_mcp('connect', 'peer')
        ui._editor.set_text('parent draft')
        ui._open_mcp()
        panel = ui._mcp_panel
        await wait(lambda: not panel._busy)
        panel.handle_input(KeyEvent('u'))
        await wait(lambda: not panel._busy and panel._kind == 'resources')
        panel.handle_input(KeyEvent('w'))
        await wait(lambda: not panel._busy
                   and panel._watches.get(('peer', '', 'test://watched'), {}).get('state') == 'active')
        await session.mcp._bridge(session.mcp._clients['peer'].request('tools/call', {'name': 'tick', 'arguments': {}},
                **({'mirrors': {'Mcp-Name': 'tick'}} if transport == 'http' else {}))
            if mode == 'official-sdk' else session.mcp._clients['peer'].request('fixture/update', {}))
        await wait(lambda: panel._watches['peer', '', 'test://watched']['changes'] == 1)
        assert 'no automatic read' in panel._message and ui._mcp_document is None
        if mode == 'modern':
            await session.mcp._bridge(session.mcp._clients['peer'].request('fixture/end', {}))
            await wait(lambda: panel._watches['peer', '', 'test://watched']['state'] == 'ended')
            panel.handle_input(KeyEvent('w'))  # Explicit restart, never an automatic re-subscribe.
            await wait(lambda: not panel._busy and panel._watches['peer', '', 'test://watched']['state'] == 'active')
        panel.handle_input(KeyEvent('w'))
        await wait(lambda: not panel._busy and panel._watches['peer', '', 'test://watched']['state'] == 'stopped')
        panel.handle_input(KeyEvent('w'))
        await wait(lambda: not panel._busy and panel._watches['peer', '', 'test://watched']['state'] == 'active')
        if mode == 'official-sdk':
            await session.mcp._bridge(session.mcp._clients['peer'].request(
                'tools/call', {'name': 'finish', 'arguments': {}},
                **({'mirrors': {'Mcp-Name': 'finish'}} if transport == 'http' else {})))
            await wait(lambda: panel._watches['peer', '', 'test://watched']['state'] == 'ended')
            assert 'resource-read' not in log.read_text()
        old_lease = ui._mcp_watch_lease
        ui._close_mcp()
        await wait(lambda: not session.mcp._resource_watches)
        ui._handle_ui_event({'type': 'mcp_watch_notice', 'lease': old_lease, 'context': ui._mcp_context_key(),
                             'data': {'state': 'active', 'changes': 999}})
        assert ui._mcp_panel is None and ui._editor.get_text() == 'parent draft' and not calls
        assert 'resources/read' not in log.read_text()
        assert not session.agent.messages and session.permissions.mode == 'ask'
    finally:
        ui._request_exit()
        await session.mcp.aclose()
