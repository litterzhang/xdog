from __future__ import annotations

import pytest
from xdog.claw.channels.tui.tui_app import ChatApp


@pytest.mark.parametrize('changed', [{'session_id': 'foreign'}, {'version': True}, {'version': 2},
                                   {'history': []}, {'snapshot': {'secret': 'unvalidated'}}])
def test_failure_descriptions_cannot_adopt_foreign_or_invalid_worker_state(changed):
    ui = ChatApp('/tmp/unused-failure-scope.sock')
    original = {'extensions': [], 'commands': {'ext-retained-safe': 'Retained'}, 'ui': {},
                'shortcuts': [], 'terminal_input': False, 'terminal_generation': 0}
    try:
        ui._handle_response({'type': 'pong', 'session_controls': 1, 'session_id': 'owner', 'extension_controls': 1})
        ui._extension_snapshot = original
        ui._extension_command('ext-retained-safe', '')
        request = ui._send_queue.get_nowait()
        ui._handle_response({'type': 'extension_result', 'request_id': request['request_id'], 'group_id': 'main',
            'success': False, 'error': 'owned failure', 'failure_snapshot': {
                'version': 1, 'session_id': 'owner', 'snapshot': {
                    'extensions': [], 'commands': {}, 'ui': {}, 'shortcuts': [],
                    'terminal_input': False, 'terminal_generation': 1}, **changed}})
        assert ui._extension_snapshot is original and ui._gateway_session_id == 'owner'
        assert ui._extension_pending is None and ui._send_queue.empty()
    finally:
        ui._request_exit()


def test_cancel_control_preserves_original_bootstrap_lease_and_requires_current_capability():
    ui = ChatApp('/tmp/unused-extension-cancel.sock')
    try:
        ui._handle_response({'type': 'pong', 'extension_controls': 1, 'session_controls': 1})
        ui._extension_command('ext-owned-command', '')
        request = ui._send_queue.get_nowait()
        ui._handle_escape()
        assert ui._send_queue.empty()  # Older gateway never receives guessed cancellation.
        ui._extension_cancel_supported = True
        ui._gateway_session_id = 'created-by-owned-command'
        ui._extension_pending = request['request_id'], ui._gateway_session_id
        ui._handle_escape()
        cancel = ui._send_queue.get_nowait()
        assert cancel['request_id'] == request['request_id'] and cancel['expected_session'] == ''
        assert ui._extension_pending is not None  # A cancellation request is not a settlement receipt.
    finally:
        ui._request_exit()


async def test_wire_cancel_before_command_admission_settles_receipt_and_releases_reserved_group(tmp_path):
    import asyncio
    import json
    from unittest.mock import patch

    from xdog.ai.types import AssistantMessage
    from xdog.ai.utils.event_stream import EventStream
    from xdog.claw.config import ClawConfig, GroupDef
    from xdog.claw.core.runtime.gateway import GatewayServer

    config = ClawConfig(model='offline/model', data_dir=str(tmp_path / 'data'),
        socket_path=str(tmp_path / 'early.sock'), pid_file=str(tmp_path / 'early.pid'),
        groups=(GroupDef(id='main', is_main=True),))
    server = GatewayServer(config)
    calls = []
    def stream(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())
    with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=(config.model, stream)):
        await server.start()
    runtime = server._orchestrator._runtimes['main']
    meta = runtime.transcript_store.create_session('main')
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    try:
        # Coalesced frames are processed before the newly scheduled task gets a
        # callback slot. Even that cancellation must settle the reservation.
        frames = [{'type': 'extension_control', 'request_id': 'early', 'group_id': 'main',
                   'session_id': meta.session_id, 'action': 'list'},
                  {'type': 'extension_control_cancel', 'request_id': 'early', 'group_id': 'main',
                   'expected_session': meta.session_id}]
        writer.write(''.join(json.dumps(frame) + '\n' for frame in frames).encode())
        await writer.drain()
        async with asyncio.timeout(5):
            while True:
                reply = json.loads(await reader.readline())
                if reply['type'] == 'extension_result':
                    break
        assert reply['canceled'] and not reply['success']
        async with asyncio.timeout(3):
            while server._orchestrator.is_group_running('main'):
                await asyncio.sleep(0.003)
        assert not calls
    finally:
        writer.close()
        await writer.wait_closed()
        await server.stop()
