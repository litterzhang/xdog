"""Controlled socket/worker scheduling for literal argument acceptance phases."""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import shutil
import threading
from pathlib import Path
from unittest.mock import patch

import pytest
from xdog.agent.mcp.stdio import StdioClient
from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer
from xdog.tui.stdin_buffer import KeyBytes


@pytest.mark.parametrize('token,replacement', [
    (r'release\ canTAIL', r'release\ candidate'),
    ('"release canTAIL"', '"release candidate"'),
    ("'release\\ canTAIL'", "'release\\ candidate'"),
])
@pytest.mark.parametrize('settle_before_clear', [False, True], ids=['cancel-admitted', 'settle-before-clear'])
async def test_literal_undo_end_clear_requires_frontend_settlement(
    tmp_path, monkeypatch, token, replacement, settle_before_clear,
):
    """Canceling an undo's admitted callback disables the worker by design.

    Hold a real worker reply inside its invocation, not an arbitrary sleep. The
    safe path releases and consumes that receipt BEFORE Ctrl-C; the unsafe path
    reproduces the missing literal menu on the next draft. Both paths exercise
    the real gateway and batched terminal key parser without touching services.
    """
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/argcomplete', root / 'argcomplete')
    config = ClawConfig(model='offline/model', data_dir=str(tmp_path / 'data'),
        socket_path=str(tmp_path / 'literal.sock'), pid_file=str(tmp_path / 'literal.pid'),
        groups=(GroupDef(id='main', is_main=True, workspace=str(tmp_path / 'workspace')),),
        extensions=('argcomplete',), extension_dir=str(root))
    server = GatewayServer(config)
    ui = ChatApp(config.socket_path)
    writer = worker = None
    gate = concurrent.futures.Future()
    entered = asyncio.Event()
    armed = threading.Event()
    loop = asyncio.get_running_loop()
    original_request = StdioClient.request
    async def gated_request(client, method, params, **kwargs):
        result = await original_request(client, method, params, **kwargs)
        if method == 'xdog/command_complete' and armed.is_set():
            armed.clear()  # Exactly one admitted callback, across the worker loop.
            loop.call_soon_threadsafe(entered.set)
            await asyncio.wrap_future(gate)
        return result
    monkeypatch.setattr(StdioClient, 'request', gated_request)

    def no_model(*args):
        pytest.fail('Completion insertion must never run a model')

    async def send(packet):
        writer.write((json.dumps(packet) + '\n').encode())
        await writer.drain()

    async def read(kind):
        async with asyncio.timeout(5):
            while True:
                line = await reader.readline()
                assert line, 'Private gateway closed before its settlement receipt'
                packet = json.loads(line)
                if packet['type'] == kind:
                    return packet

    async def flush_requests():
        packets = []
        while not ui._send_queue.empty():
            packet = ui._send_queue.get_nowait()
            packets.append(packet)
            await send(packet)
        return packets

    async def settle():
        receipts = []
        async with asyncio.timeout(5):
            while ui._argument_requests:
                await flush_requests()
                receipt = await read('argument_completion_result')
                ui._handle_response(receipt)
                receipts.append(receipt)
        assert ui._argument_feed._inflight is None
        assert ui._argument_feed._canceling is None
        return receipts

    def keys(data):
        assert ui._tui._handle_frame(KeyBytes(data))

    try:
        with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=(config.model, no_model)):
            await server.start()
        runtime = server._orchestrator._runtimes['main']
        session = runtime.get_or_create_session(allow_reset=False)
        await session.initialize_extensions()
        worker = session.extensions._manager._clients['argcomplete'].process
        reader, writer = await asyncio.open_unix_connection(config.socket_path)
        await send({'type': 'ping', 'group_id': 'main', 'extension_argument_completion': 1,
                    'extension_argument_cancel': 1, 'extension_argument_context': 1})
        ui._handle_response(await read('pong'))
        original = '/ext-argcomplete-deploy literal ' + token + ' later'
        ui._editor.set_text(original)
        cursor = original.index('canTAIL') + 3
        keys(b'\x1b[D' * (len(original) - cursor))
        assert ui._editor._cursor == cursor
        assert len(ui._argument_requests) == 1  # Cursor bursts queue only the latest draft.
        assert ui._argument_feed._request.cursor == cursor
        receipts = await settle()
        assert len(receipts) == 2  # Superseded end-of-line receipt, then cursor-local receipt.
        assert 'Literal complete token' in '\n'.join(ui._editor.render(120))
        # A duplicate superseded socket receipt cannot overwrite the cursor-local rows.
        ui._handle_response(receipts[0])
        assert 'Literal complete token' in '\n'.join(ui._editor.render(120))
        keys(b'\t\x1b[F')  # Insert only, then move to the end in the same frame.
        assert ui._editor.get_text() == '/ext-argcomplete-deploy literal ' + replacement + ' later'
        await settle()

        armed.set()
        keys(b'\x1bz\x1b[F')  # Alt-Z undo + End starts a new callback for this draft.
        assert ui._editor.get_text() == original and ui._editor._cursor == len(original)
        packets = await flush_requests()
        pending = next(packet for packet in packets if packet['type'] == 'argument_completion')
        async with asyncio.timeout(5):
            await entered.wait()
        assert ui._argument_feed._inflight is not None and worker.returncode is None
        if settle_before_clear:
            gate.set_result(None)
            await settle()  # Frontend receipt, not editor paint or host idle state.
        keys(b'\x03')
        assert ui._editor.get_text() == ''
        canceled = await flush_requests()
        if settle_before_clear:
            assert not canceled
        else:
            assert [packet['type'] for packet in canceled] == ['argument_completion_cancel']
            assert canceled[0]['request_id'] == pending['request_id']
            receipt = await read('argument_completion_result')
            assert receipt['request_id'] == pending['request_id'] and receipt['status'] == 'canceled'
            ui._handle_response(receipt)
            assert worker.returncode is not None
            assert session.extensions.snapshot()['extensions'][0]['state'] == 'failed'
            assert not ui._argument_requests and ui._argument_feed._inflight is None

        ui._editor.set_text(original)
        keys(b'\x1b[D' * (len(original) - cursor))
        await settle()
        menu = '\n'.join(ui._editor.render(120))
        assert ('Literal complete token' in menu) is settle_before_clear
        if settle_before_clear:
            keys(b'\r\x1b[F')  # Enter also inserts only; preserves quotes/backslashes/suffix.
            assert ui._editor.get_text() == '/ext-argcomplete-deploy literal ' + replacement + ' later'
            await settle()
            counts = json.loads(await session.extensions.command('ext-argcomplete-counts', ''))
            assert counts['executed'] == 0 and counts['completed'] > 0
            assert worker.returncode is None
        assert not session.agent.state.messages
    finally:
        if not gate.done():
            gate.set_result(None)
        try:
            ui._request_exit()
            if writer is not None:
                writer.close()
                await writer.wait_closed()
        finally:
            await server.stop()
        assert worker is None or worker.returncode is not None
        assert not Path(config.socket_path).exists() and not Path(config.pid_file).exists()
        assert not server._client_tasks
