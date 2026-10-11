"""Actual Claw root-control socket/UI leases, negotiated independently from OAuth."""
from __future__ import annotations

import asyncio
import json
import sys
from unittest.mock import patch

import pytest
from xdog.ai.types import AssistantMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer
from xdog.tui.keys import KeyEvent

from packages.agent.tests.test_mcp_roots import ROOTS, write_peer


async def setup(tmp_path):
    peer, log = tmp_path / 'peer.py', tmp_path / 'frames'
    write_peer(peer)
    config = ClawConfig(model='offline/model', data_dir=str(tmp_path / 'data'),
        socket_path=str(tmp_path / 'gateway.sock'), pid_file=str(tmp_path / 'gateway.pid'),
        groups=(GroupDef(id='main', is_main=True, workspace=str(tmp_path / 'workspace')),),
        mcp_servers={'owned': {'command': sys.executable, 'args': [str(peer), str(log), 'legacy'], 'roots': ROOTS}})
    calls = []
    def stream(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())
    server = GatewayServer(config)
    with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=('offline/model', stream)):
        await server.start()
    manager = server._orchestrator._runtimes['main'].mcp
    await manager.connect('owned')
    return server, config, manager, calls


async def receive(reader):
    async with asyncio.timeout(5):
        return json.loads(await reader.readline())


@pytest.mark.parametrize('fault', ['none', 'unnegotiated', 'foreign', 'withdraw', 'disconnect', 'used-id'])
async def test_real_gateway_root_review_exact_connection_withdrawal_and_no_replay(tmp_path, fault):
    server, config, manager, calls = await setup(tmp_path)
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    other_reader, other_writer = await asyncio.open_unix_connection(config.socket_path)
    view = 'a' * 32
    scope = server._orchestrator.source_lease('main')[0]
    async def send(request, target=writer, source=reader):
        target.write((json.dumps(request) + '\n').encode())
        await target.drain()
        return await receive(source)
    envelope = {'type': 'mcp_roots_control', 'group_id': 'main', 'session_id': scope,
                'view': view, 'server': 'owned'}
    try:
        if fault != 'unnegotiated':
            assert (await send({'type': 'ping', 'group_id': 'main', 'mcp_roots': 1}))['mcp_roots'] == 1
        prepared = await send({**envelope, 'request_id': 'prepare', 'action': 'prepare', 'roots': []})
        if fault == 'unnegotiated':
            assert not prepared['success'] and manager._clients['owned'].root_capability.roots
            return
        assert prepared['success'], prepared
        assert prepared['data']['roots'] == [] and manager._clients['owned'].root_capability.roots
        proof = prepared['data']['review_id']
        if fault == 'foreign':
            await send({'type': 'ping', 'group_id': 'main', 'mcp_roots': 1}, other_writer, other_reader)
            reply = await send({**envelope, 'request_id': 'foreign', 'action': 'commit', 'review_id': proof},
                               other_writer, other_reader)
            assert not reply['success']
        elif fault == 'withdraw':
            reply = await send({'type': 'ping', 'group_id': 'main', 'mcp_roots': 0})
            assert reply['mcp_roots'] == 0
            reply = await send({**envelope, 'request_id': 'commit', 'action': 'commit', 'review_id': proof})
            assert not reply['success']
        elif fault == 'disconnect':
            writer.close()
            await writer.wait_closed()
            async with asyncio.timeout(5):
                while manager._root_consents:
                    await asyncio.sleep(.005)
        else:
            rid = 'prepare' if fault == 'used-id' else 'commit'
            reply = await send({**envelope, 'request_id': rid, 'action': 'commit', 'review_id': proof})
            assert reply['success'] == (fault == 'none'), reply
            if fault == 'none':
                assert manager._clients['owned'].root_capability.roots == ()
                retry = await send({**envelope, 'request_id': 'retry', 'action': 'commit', 'review_id': proof})
                assert not retry['success']
        if fault != 'none':
            assert manager._clients['owned'].root_capability.roots
        assert not calls and not manager.tools()
    finally:
        writer.close()
        other_writer.close()
        await asyncio.gather(writer.wait_closed(), other_writer.wait_closed())
        await server.stop()


async def test_actual_claw_tui_full_painted_roots_review_commits_and_preserves_composer(tmp_path):
    server, config, manager, calls = await setup(tmp_path)
    ui = ChatApp(config.socket_path)
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    task = asyncio.create_task(ui._run_connection(reader, writer))
    async def wait(predicate):
        async with asyncio.timeout(8):
            while True:
                ui._poll()
                if predicate():
                    return
                await asyncio.sleep(.003)
    try:
        await wait(lambda: ui._connection_ready and ui._mcp_roots_supported)
        ui._editor.set_text('PARENT UNSENT ROOTS')
        ui._open_mcp()
        await wait(lambda: not ui._mcp_panel._busy)
        panel = ui._mcp_panel
        panel.handle_input(KeyEvent('o'))
        panel.handle_input(KeyEvent('enter'))
        await wait(lambda: panel.roots_review is not None)
        review = panel.roots_review
        review.handle_input(KeyEvent('enter'))
        assert manager._clients['owned'].root_capability.roots
        for _ in range(100):
            panel.render(80)
            if review._visible:
                break
            review.handle_input(KeyEvent('pagedown'))
        assert review._visible
        review.handle_input(KeyEvent('enter'))
        await wait(lambda: panel.roots_review is None and not panel._busy)
        assert manager._clients['owned'].root_capability.roots == ()
        ui._close_mcp()
        assert ui._editor.get_text() == 'PARENT UNSENT ROOTS' and not calls
    finally:
        ui._request_exit()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        writer.close()
        await writer.wait_closed()
        await server.stop()


async def test_root_inflight_cancel_keeps_reader_responsive_and_releases_group_after_real_settlement(tmp_path):
    import threading
    server, config, manager, calls = await setup(tmp_path)
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    entered, released = threading.Event(), threading.Event()
    view = 'b' * 32
    scope = server._orchestrator.source_lease('main')[0]
    async def send(value):
        writer.write((json.dumps(value) + '\n').encode())
        await writer.drain()
        return await receive(reader)
    envelope = {'type': 'mcp_roots_control', 'group_id': 'main', 'session_id': scope,
                'view': view, 'server': 'owned'}
    client = manager._clients['owned']
    original = client.notify
    async def held(method, params=None):
        if method == 'notifications/roots/list_changed':
            entered.set()
            try:
                await asyncio.sleep(30)
            finally:
                released.set()
        else:
            await original(method, params)
    try:
        await send({'type': 'ping', 'group_id': 'main', 'mcp_roots': 1})
        prepared = await send({**envelope, 'request_id': 'prepare', 'action': 'prepare', 'roots': []})
        assert prepared['success']
        client.notify = held
        writer.write((json.dumps({**envelope, 'request_id': 'commit', 'action': 'commit',
                                 'review_id': prepared['data']['review_id']}) + '\n').encode())
        await writer.drain()
        assert await asyncio.to_thread(entered.wait, 3)
        assert client.root_capability.roots == ()  # Publication precedes remote notice; no false rollback.
        assert (await send({'type': 'ping', 'group_id': 'main'}))['type'] == 'pong'
        canceled = await send({**envelope, 'request_id': 'cancel', 'action': 'cancel'})
        assert canceled['success']
        async with asyncio.timeout(5):
            while not released.is_set() or manager._root_consents or server._orchestrator.is_group_running('main'):
                await asyncio.sleep(.005)
        assert not calls and client.root_capability.roots == ()
    finally:
        client.notify = original
        writer.close()
        await writer.wait_closed()
        await server.stop()


async def test_root_cancel_remains_available_after_connection_request_budget_exhausted(tmp_path):
    server, config, manager, calls = await setup(tmp_path)
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    scope = server._orchestrator.source_lease('main')[0]
    envelope = {'type': 'mcp_roots_control', 'group_id': 'main', 'session_id': scope,
                'view': 'c' * 32, 'server': 'owned'}
    async def send(value):
        writer.write((json.dumps(value) + '\n').encode())
        await writer.drain()
        return await receive(reader)
    try:
        await send({'type': 'ping', 'group_id': 'main', 'mcp_roots': 1})
        prepared = await send({**envelope, 'request_id': 'prepare', 'action': 'prepare', 'roots': []})
        assert prepared['success'] and manager._root_consents
        for index in range(255):
            # Invalid receipt consumes an admission ID but cannot change roots.
            reply = await send({**envelope, 'request_id': str(index), 'action': 'commit', 'review_id': 'invalid'})
            assert not reply['success']
        exhausted = await send({**envelope, 'request_id': 'exhausted', 'action': 'prepare', 'roots': []})
        assert not exhausted['success']
        canceled = await send({**envelope, 'request_id': 'prepare', 'action': 'cancel'})
        assert canceled['success']
        assert manager._clients['owned'].root_capability.roots and not calls
    finally:
        writer.close()
        await writer.wait_closed()
        await server.stop()
