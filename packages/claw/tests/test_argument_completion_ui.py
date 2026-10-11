from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import pytest
from xdog.ai.types import AssistantMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer


@pytest.mark.parametrize('capability', [None, False, 0])
async def test_argument_query_requires_its_own_explicit_viewer_negotiation(tmp_path, capability):
    config = ClawConfig(model='offline/model', data_dir=str(tmp_path / 'data'),
        socket_path=str(tmp_path / 'old.sock'), pid_file=str(tmp_path / 'old.pid'),
        groups=(GroupDef(id='main', is_main=True),))
    server = GatewayServer(config)
    calls = []
    def stream(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())
    with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=(config.model, stream)):
        await server.start()
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    try:
        frames = [{'type': 'ping', 'group_id': 'main', 'extension_controls': 1,
            **({'extension_argument_completion': capability} if capability is not None else {})},
            {'type': 'argument_completion', 'request_id': 'old', 'group_id': 'main', 'session_id': 'foreign',
             'command': 'ext-owned-command', 'identity': 'a' * 32, 'prefix': 'a'}]
        writer.write(''.join(json.dumps(frame) + '\n' for frame in frames).encode())
        await writer.drain()
        async with asyncio.timeout(5):
            while True:
                reply = json.loads(await reader.readline())
                if reply['type'] == 'argument_completion_result':
                    break
        assert reply['status'] == 'unavailable' and not reply['items'] and not calls
        assert not server._orchestrator.is_group_running('main')
    finally:
        writer.close()
        await writer.wait_closed()
        await server.stop()


def test_withdrawn_argument_support_and_reconnection_cannot_insert_old_results_or_send_again():
    ui = ChatApp('/tmp/unused-arguments.sock')
    try:
        snapshot = {'extensions': [{'name': 'owned', 'state': 'active'}],
            'commands': {'ext-owned-run': 'Registered'}, 'command_completions': {'ext-owned-run': 'a' * 32},
            'ui': {}, 'shortcuts': [], 'terminal_input': False, 'terminal_generation': 0}
        ui._handle_response({'type': 'pong', 'session_controls': 1, 'session_id': 'owner',
                             'extension_argument_completion': 1, 'extension_snapshot': snapshot})
        ui._editor.set_text('/ext-owned-run a')
        request = ui._send_queue.get_nowait()
        assert request['type'] == 'argument_completion'
        ui._handle_response({'type': 'internal_error', 'message': 'lost'})
        assert not ui._argument_requests and ui._editor.get_text() == '/ext-owned-run a'
        ui._handle_response({'type': 'argument_completion_result', 'request_id': request['request_id'],
                             'group_id': 'main', 'session_id': 'owner', 'status': 'ok', 'items': [{'value': 'LATE'}]})
        assert not ui._argument_feed._rows and ui._editor.get_text() == '/ext-owned-run a'
        ui._handle_response({'type': 'pong', 'session_controls': 1, 'session_id': 'owner',
                             'extension_argument_completion': False, 'extension_snapshot': snapshot})
        ui._editor.poll_autocomplete()
        assert not ui._argument_supported and ui._send_queue.empty()
    finally:
        ui._request_exit()


async def test_cancel_before_argument_admission_is_negotiated_and_releases_only_owned_reservation(tmp_path):
    config = ClawConfig(model='offline/model', data_dir=str(tmp_path / 'data'),
        socket_path=str(tmp_path / 'cancel.sock'), pid_file=str(tmp_path / 'cancel.pid'),
        groups=(GroupDef(id='main', is_main=True),))
    server = GatewayServer(config)
    def unexpected(*args):
        pytest.fail('Argument cancellation cannot call a model')
    with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=(config.model, unexpected)):
        await server.start()
    runtime = server._orchestrator._runtimes['main']
    meta = runtime.transcript_store.create_session('main')
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    try:
        frames = [{'type': 'ping', 'group_id': 'main', 'extension_argument_completion': 1,
                   'extension_argument_cancel': 1},
                  {'type': 'argument_completion', 'request_id': 'before', 'group_id': 'main',
                   'session_id': meta.session_id, 'command': 'ext-absent-command',
                   'identity': 'a' * 32, 'prefix': ''},
                  {'type': 'argument_completion_cancel', 'request_id': 'before', 'group_id': 'main',
                   'session_id': meta.session_id}]
        writer.write(''.join(json.dumps(frame) + '\n' for frame in frames).encode())
        await writer.drain()
        async with asyncio.timeout(5):
            while True:
                result = json.loads(await reader.readline())
                if result['type'] == 'argument_completion_result':
                    break
        assert result['status'] == 'canceled' and not result['items']
        async with asyncio.timeout(3):
            while server._orchestrator.is_group_running('main'):
                await asyncio.sleep(0.003)
        assert runtime._active_session is None  # Cancellation cannot activate an unrequested Agent.
    finally:
        writer.close()
        await writer.wait_closed()
        await server.stop()


def test_cancel_identity_uses_original_group_session_and_requires_current_exact_capability():
    ui = ChatApp('/tmp/unused-argument-cancel-scope.sock')
    try:
        snapshot = {'extensions': [{'name': 'owned', 'state': 'active'}],
            'commands': {'ext-owned-run': 'Registered'}, 'command_completions': {'ext-owned-run': 'a' * 32},
            'ui': {}, 'shortcuts': [], 'terminal_input': False, 'terminal_generation': 0}
        ui._handle_response({'type': 'pong', 'session_id': 'original', 'extension_argument_completion': 1,
                             'extension_argument_cancel': True, 'extension_snapshot': snapshot})
        ui._editor.set_text('/ext-owned-run a')
        ui._send_queue.get_nowait()
        pending = ui._argument_feed._inflight
        ui._argument_feed.dismiss(ui._editor.get_text(), ui._editor._cursor)
        assert ui._send_queue.empty()  # Boolean/older support never sends a guessed cancel.
        ui._argument_feed.reset()
        ui._argument_requests.clear()
        ui._argument_scopes.clear()
        ui._argument_cancel_supported = True
        ui._argument_feed.reopen()
        ui._editor.poll_autocomplete()
        wire = ui._send_queue.get_nowait()
        current = ui._argument_feed._inflight
        ui._gateway_session_id = 'changed-viewer-head'
        ui._cancel_arguments(current)
        cancel = ui._send_queue.get_nowait()
        assert cancel['request_id'] == wire['request_id'] and cancel['session_id'] == 'original'
        assert cancel['group_id'] == 'main' and current != pending
    finally:
        ui._request_exit()


@pytest.mark.parametrize('capability', [None, 0, True])
async def test_full_argument_context_cannot_borrow_prefix_only_capability_and_old_queries_still_work(
    tmp_path, capability,
):
    import shutil
    from pathlib import Path

    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/argcomplete', root / 'argcomplete')
    config = ClawConfig(model='offline/model', data_dir=str(tmp_path / 'data'),
        socket_path=str(tmp_path / 'context.sock'), pid_file=str(tmp_path / 'context.pid'),
        groups=(GroupDef(id='main', is_main=True),), extensions=('argcomplete',), extension_dir=str(root))
    server = GatewayServer(config)
    def unexpected(*args):
        pytest.fail('Argument context inspection cannot submit model work')
    with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=(config.model, unexpected)):
        await server.start()
    runtime = server._orchestrator._runtimes['main']
    session = runtime.get_or_create_session(allow_reset=False)
    await session.initialize_extensions()
    identity = session.extensions.command_completions()['ext-argcomplete-deploy']
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    async def send(packet):
        writer.write((json.dumps(packet) + '\n').encode())
        await writer.drain()
    async def result(kind):
        async with asyncio.timeout(5):
            while True:
                message = json.loads(await reader.readline())
                if message['type'] == kind:
                    return message
    try:
        await send({'type': 'ping', 'group_id': 'main', 'extension_argument_completion': 1,
                    **({'extension_argument_context': capability} if capability is not None else {})})
        await result('pong')
        base = {'type': 'argument_completion', 'group_id': 'main', 'session_id': session.meta.session_id,
                'command': 'ext-argcomplete-deploy', 'identity': identity}
        await send({**base, 'request_id': 'unsupported-context', 'prefix': 'en',
                    'argument_context': {'arguments': 'production en', 'cursor': 13, 'start': 11, 'end': 13}})
        denied = await result('argument_completion_result')
        assert denied['status'] == 'unavailable' and not denied['items']
        counts = json.loads(await session.extensions.command('ext-argcomplete-counts', ''))
        assert counts['executed'] == counts['completed'] == 0
        await send({**base, 'request_id': 'prefix-only', 'prefix': 'a'})
        allowed = await result('argument_completion_result')
        assert allowed['status'] == 'ok' and allowed['items'][0]['value'] == 'alpha'
        assert session.extensions.snapshot()['extensions'][0]['state'] == 'active'
    finally:
        writer.close()
        await writer.wait_closed()
        await server.stop()


def test_withdrawn_full_context_cannot_adopt_late_contextual_items_as_prefix_only_data():
    ui = ChatApp('/tmp/unused-context-withdrawal.sock')
    try:
        snapshot = {'extensions': [{'name': 'owned', 'state': 'active'}],
            'commands': {'ext-owned-run': 'Registered'}, 'command_completions': {'ext-owned-run': 'a' * 32},
            'ui': {}, 'shortcuts': [], 'terminal_input': False, 'terminal_generation': 0}
        ui._handle_response({'type': 'pong', 'session_id': 'owner', 'extension_argument_completion': 1,
            'extension_argument_cancel': 1, 'extension_argument_context': 1, 'extension_snapshot': snapshot})
        ui._editor.set_text('/ext-owned-run production en')
        request = ui._send_queue.get_nowait()
        assert request['argument_context'] == {'arguments': 'production en', 'cursor': 13, 'start': 11, 'end': 13}
        ui._handle_response({'type': 'pong', 'session_id': 'owner', 'extension_argument_completion': 1,
            'extension_argument_cancel': 1, 'extension_argument_context': 0, 'extension_snapshot': snapshot})
        cancel = ui._send_queue.get_nowait()
        assert cancel['type'] == 'argument_completion_cancel' and cancel['request_id'] == request['request_id']
        ui._handle_response({'type': 'argument_completion_result', 'request_id': request['request_id'],
            'group_id': 'main', 'session_id': 'owner', 'status': 'ok',
            'items': [{'value': 'OLD-CONTEXT-RESULT'}]})
        assert ui._editor.get_text() == '/ext-owned-run production en' and not ui._argument_feed._rows
        assert ui._send_queue.empty()  # No automatic context-to-prefix downgrade/replay.
        from xdog.tui.keys import KeyEvent
        ui._editor.handle_input(KeyEvent('tab'))
        following = ui._send_queue.get_nowait()
        assert following['type'] == 'argument_completion' and 'argument_context' not in following
    finally:
        ui._request_exit()


async def test_context_capability_withdrawal_before_job_start_settles_receipt_without_activation(tmp_path):
    config = ClawConfig(model='offline/model', data_dir=str(tmp_path / 'data'),
        socket_path=str(tmp_path / 'withdraw.sock'), pid_file=str(tmp_path / 'withdraw.pid'),
        groups=(GroupDef(id='main', is_main=True),))
    server = GatewayServer(config)
    def unexpected(*args):
        pytest.fail('Withdrawing completion metadata cannot submit model work')
    with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=(config.model, unexpected)):
        await server.start()
    runtime = server._orchestrator._runtimes['main']
    meta = runtime.transcript_store.create_session('main')
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    try:
        packets = [{'type': 'ping', 'group_id': 'main', 'extension_argument_completion': 1,
                    'extension_argument_context': 1},
            {'type': 'argument_completion', 'request_id': 'withdraw', 'group_id': 'main',
             'session_id': meta.session_id, 'command': 'ext-never-activate', 'identity': 'a' * 32, 'prefix': 'en',
             'argument_context': {'arguments': 'production en', 'cursor': 13, 'start': 11, 'end': 13}},
            {'type': 'ping', 'group_id': 'main', 'extension_argument_context': 0}]
        writer.write(''.join(json.dumps(packet) + '\n' for packet in packets).encode())
        await writer.drain()
        async with asyncio.timeout(5):
            while True:
                reply = json.loads(await reader.readline())
                if reply['type'] == 'argument_completion_result':
                    break
        assert reply['status'] == 'canceled' and not reply['items']
        async with asyncio.timeout(3):
            while server._orchestrator.is_group_running('main'):
                await asyncio.sleep(0.003)
        assert runtime._active_session is None
    finally:
        writer.close()
        await writer.wait_closed()
        await server.stop()
