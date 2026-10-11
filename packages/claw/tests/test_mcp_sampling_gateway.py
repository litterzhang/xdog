"""Claw independent sampling controls on a private socket, no live model/service."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import pytest
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer

from packages.agent.tests.test_mcp_sampling_executor import Adapter
from packages.agent.tests.test_mcp_sampling_registration import config as peer_config


async def setup(tmp_path, adapter=None):
    servers, log = peer_config(tmp_path)
    adapter = adapter or Adapter()
    config = ClawConfig(model='explicit-model', data_dir=str(tmp_path / 'data'),
        socket_path=str(tmp_path / 'gateway.sock'), pid_file=str(tmp_path / 'gateway.pid'),
        groups=(GroupDef(id='main', is_main=True, workspace=str(tmp_path / 'workspace')),), mcp_servers=servers)
    server = GatewayServer(config)
    with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=('explicit-model', adapter)):
        await server.start()
    runtime = server._orchestrator._runtimes['main']
    session = runtime.get_or_create_session(allow_reset=False)
    return server, config, runtime, session, adapter, log


async def receive(reader, kind):
    async with asyncio.timeout(5):
        while True:
            packet = json.loads(await reader.readline())
            if packet['type'] == kind:
                return packet


@pytest.mark.parametrize('end', ['release', 'detach', 'withdraw', 'disconnect', 'foreign'])
async def test_actual_socket_sampling_two_phase_registration_and_revocation(tmp_path, end):
    server, config, runtime, session, adapter, log = await setup(tmp_path)
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    other_reader, other_writer = await asyncio.open_unix_connection(config.socket_path)
    view = 'a' * 32
    envelope = {'type': 'mcp_sampling_control', 'group_id': 'main', 'session_id': session.meta.session_id,
                'server': 'owned', 'view': view}
    async def send(packet, source=reader, target=writer, kind='mcp_sampling_result'):
        target.write((json.dumps(packet) + '\n').encode())
        await target.drain()
        return await receive(source, kind)
    async def answer(event, identifier, source=reader, target=writer):
        response = {key: event[key] for key in ('id', 'phase', 'receipt', 'sha256')}
        response['approve'] = True
        if event['phase'] == 'input':
            response['selection'] = {'model': 'explicit-model', 'max_tokens': 10, 'context': 'none', 'tools': []}
        return await send({**envelope, 'request_id': identifier, 'action': 'answer', 'response': response},
                          source, target)
    try:
        # Independent opt-in: a bare pong gives no sampling capability.
        pong = await send({'type': 'ping', 'group_id': 'main'}, kind='pong')
        assert pong['mcp_sampling'] == 0
        rejected = await send({**envelope, 'request_id': 'old', 'action': 'enable'})
        assert not rejected['success'] and not runtime.mcp._sampling
        pong = await send({'type': 'ping', 'group_id': 'main', 'mcp_sampling': 1}, kind='pong')
        assert pong['mcp_sampling'] == 1
        enabled = await send({**envelope, 'request_id': 'enable', 'action': 'enable'})
        assert enabled['success'], enabled
        assert not log.exists() and not adapter.calls
        await runtime.mcp.connect('owned')
        first = await receive(reader, 'mcp_sampling_event')
        assert first['view'] == view and first['event']['phase'] == 'input'
        assert not adapter.calls
        if end == 'foreign':
            await send({'type': 'ping', 'group_id': 'main', 'mcp_sampling': 1}, other_reader, other_writer, 'pong')
            rejected = await answer(first['event'], 'foreign', other_reader, other_writer)
            assert not rejected['success'] and not adapter.calls
            return
        approved = await answer(first['event'], 'input')
        assert approved['success'], approved
        output = await receive(reader, 'mcp_sampling_event')
        assert output['event']['phase'] == 'output' and len(adapter.calls) == 1
        frames = [json.loads(row) for row in log.read_text().splitlines()]
        assert not any(row.get('id') == 'sample' for row in frames)
        assert await send({'type': 'ping', 'group_id': 'main'}, kind='pong')
        if end == 'release':
            assert (await answer(output['event'], 'output'))['success']
            async with asyncio.timeout(5):
                while runtime.mcp._clients['owned'].reverse._jobs:
                    await asyncio.sleep(.005)
            await runtime.mcp._bridge(runtime.mcp._clients['owned'].request('ping', {}))
            frames = [json.loads(row) for row in log.read_text().splitlines()]
            reply = next(row for row in frames if row.get('id') == 'sample')
            assert reply['result']['content']['text'] == 'MODEL OUTPUT'
        else:
            if end == 'detach':
                assert (await send({**envelope, 'request_id': 'detach', 'action': 'detach'}))['success']
            elif end == 'withdraw':
                pong = await send({'type': 'ping', 'group_id': 'main', 'mcp_sampling': 0}, kind='pong')
                assert pong['mcp_sampling'] == 0
            else:
                writer.close()
                await writer.wait_closed()
            async with asyncio.timeout(5):
                while runtime.mcp._sampling:
                    await asyncio.sleep(.005)
            assert not runtime.mcp._clients['owned'].reverse._jobs
        assert not session.agent.messages and len(adapter.calls) == 1
        assert not server._orchestrator.is_group_running('main')
    finally:
        writer.close()
        other_writer.close()
        await asyncio.gather(writer.wait_closed(), other_writer.wait_closed())
        await server.stop()


@pytest.mark.parametrize('release', [False, True])
async def test_actual_claw_ui_two_painted_reviews_and_parent_history_preservation(tmp_path, release):
    from xdog.claw.channels.tui.tui_app import ChatApp
    from xdog.tui.keys import KeyEvent
    server, config, runtime, session, adapter, log = await setup(tmp_path)
    ui = ChatApp(config.socket_path)
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    task = asyncio.create_task(ui._run_connection(reader, writer))
    async def wait(predicate):
        async with asyncio.timeout(5):
            while True:
                ui._poll()
                if predicate():
                    return
                await asyncio.sleep(.003)
    def paint(panel):
        for _ in range(300):
            panel.render(80)
            if panel._visible:
                return
            panel.handle_input(KeyEvent('pagedown'))
        raise AssertionError('Sampling review never fully painted')
    try:
        await wait(lambda: ui._connection_ready and ui._mcp_sampling_supported)
        ui._editor.set_text('PARENT UNSENT SAMPLING')
        ui._open_mcp()
        await wait(lambda: not ui._mcp_panel._busy)
        panel = ui._mcp_panel
        panel.handle_input(KeyEvent('b'))
        await wait(lambda: not panel._busy)
        assert runtime.mcp._sampling and not log.exists() and not adapter.calls
        panel.handle_input(KeyEvent('c'))
        await wait(lambda: panel.sampling_review is not None)
        first = panel.sampling_review
        first.editor.set_text(json.dumps({'model': 'explicit-model', 'max_tokens': 10,
                                         'context': 'none', 'tools': []}))
        first.handle_input(KeyEvent('enter'))
        first.handle_input(KeyEvent('enter'))
        assert not adapter.calls
        paint(first)
        first.handle_input(KeyEvent('enter'))
        await wait(lambda: panel.sampling_review is not None and panel.sampling_review.event['phase'] == 'output')
        assert len(adapter.calls) == 1 and not session.agent.messages
        frames = [json.loads(row) for row in log.read_text().splitlines()]
        assert not any(row.get('id') == 'sample' for row in frames)
        output = panel.sampling_review
        if release:
            paint(output)
            output.handle_input(KeyEvent('enter'))
            await wait(lambda: not runtime.mcp._clients['owned'].reverse._jobs)
            await runtime.mcp._bridge(runtime.mcp._clients['owned'].request('ping', {}))
            frames = [json.loads(row) for row in log.read_text().splitlines()]
            returned = next(row for row in frames if row.get('id') == 'sample')
            assert returned['result']['content']['text'] == 'MODEL OUTPUT'
        else:
            output.handle_input(KeyEvent('escape'))
            await wait(lambda: not runtime.mcp._sampling)
        ui._close_mcp()
        assert ui._editor.get_text() == 'PARENT UNSENT SAMPLING'
        assert len(adapter.calls) == 1 and not session.agent.messages
        assert not server._orchestrator.is_group_running('main')
    finally:
        ui._request_exit()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        writer.close()
        await writer.wait_closed()
        await server.stop()


async def test_withdrawal_during_provider_cleanup_keeps_socket_reader_responsive(tmp_path):
    import threading

    from xdog.ai.types import AssistantMessage, DoneEvent
    from xdog.ai.utils.event_stream import EventStream
    server, config, runtime, session, adapter, log = await setup(tmp_path)
    entered, cleanup, release = threading.Event(), threading.Event(), threading.Event()
    original = session.agent.stream_fn
    def stalled(model, context, options):
        async def events():
            try:
                entered.set()
                await asyncio.sleep(30)
            finally:
                cleanup.set()
                while not release.is_set():
                    await asyncio.sleep(.003)
            yield DoneEvent(message=AssistantMessage())
        return EventStream.from_async_generator(events())
    session.agent.set_stream_fn(stalled)
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    envelope = {'type': 'mcp_sampling_control', 'group_id': 'main', 'session_id': session.meta.session_id,
                'server': 'owned', 'view': 'd' * 32}
    async def send(value, kind):
        writer.write((json.dumps(value) + '\n').encode())
        await writer.drain()
        return await receive(reader, kind)
    try:
        await send({'type': 'ping', 'group_id': 'main', 'mcp_sampling': 1}, 'pong')
        assert (await send({**envelope, 'action': 'enable', 'request_id': 'enable'},
                           'mcp_sampling_result'))['success']
        await runtime.mcp.connect('owned')
        event = (await receive(reader, 'mcp_sampling_event'))['event']
        response = {key: event[key] for key in ('id', 'phase', 'receipt', 'sha256')}
        response.update(approve=True, selection={'model': 'explicit-model', 'max_tokens': 10,
                                                'context': 'none', 'tools': []})
        assert (await send({**envelope, 'action': 'answer', 'request_id': 'answer', 'response': response},
                           'mcp_sampling_result'))['success']
        assert await asyncio.to_thread(entered.wait, 2)
        pong = await send({'type': 'ping', 'group_id': 'main', 'mcp_sampling': 0}, 'pong')
        assert pong['mcp_sampling'] == 0
        assert await asyncio.to_thread(cleanup.wait, 2)
        assert runtime.mcp._sampling and server._orchestrator.is_group_running('main')
        # Withdrawal must not block the reader while provider finally owns work.
        assert (await send({'type': 'ping', 'group_id': 'main'}, 'pong'))['type'] == 'pong'
        release.set()
        async with asyncio.timeout(5):
            while runtime.mcp._sampling or server._orchestrator.is_group_running('main'):
                await asyncio.sleep(.003)
        frames = [json.loads(row) for row in log.read_text().splitlines()]
        assert not any(row.get('id') == 'sample' and 'result' in row for row in frames)
        assert not session.agent.messages
    finally:
        release.set()
        writer.close()
        await writer.wait_closed()
        session.agent.set_stream_fn(original)
        await server.stop()
