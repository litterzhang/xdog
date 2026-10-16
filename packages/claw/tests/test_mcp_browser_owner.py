"""Cold MCP browser owner selection is explicit, idle, correlated and fail-closed."""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest
from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer

from packages.agent.tests.mcp_helpers import Adapter


@asynccontextmanager
async def cold_gateway(tmp_path):
    adapter = Adapter()
    config = ClawConfig(model='explicit-model', data_dir=str(tmp_path / 'data'),
        socket_path=str(tmp_path / 'gateway.sock'), pid_file=str(tmp_path / 'gateway.pid'),
        groups=(GroupDef(id='main', is_main=True, workspace=str(tmp_path / 'workspace')),))
    gateway = GatewayServer(config)
    with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=('explicit-model', adapter)):
        await gateway.start()
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    async def send(value):
        writer.write((json.dumps(value) + '\n').encode())
        await writer.drain()
        async with asyncio.timeout(5):
            return json.loads(await reader.readline())
    try:
        yield gateway, send, adapter
    finally:
        writer.close()
        await writer.wait_closed()
        await gateway.stop()


@pytest.mark.parametrize('kind', ['elicitation'])
@pytest.mark.parametrize('invalid', ['empty-owner', 'wrong-view', 'wrong-action'])
async def test_rejected_review_control_echoes_valid_correlation_without_granting_empty_owner(tmp_path, kind, invalid):
    async with cold_gateway(tmp_path) as (gateway, send, adapter):
        value = {'type': f'mcp_{kind}_control', 'request_id': 'owned-request', 'group_id': 'main',
                 'session_id': '' if invalid == 'empty-owner' else 'missing-session',
                 'view': 'wrong' if invalid == 'wrong-view' else 'a' * 32,
                 'server': 'modern', 'action': 'wrong' if invalid == 'wrong-action' else 'enable'}
        response = await send(value)
        assert response['type'] == 'error'
        assert 'Unknown message type' in response['message']
        runtime = gateway._orchestrator._runtimes['main']
        assert (runtime._active_session is None
                and not hasattr(runtime.mcp, '_sampling') and not hasattr(runtime.mcp, '_elicitation'))
        assert not adapter.calls


@pytest.mark.parametrize('condition', ['idle', 'busy', 'existing', 'wrong-expectation', 'invalid-view'])
async def test_explicit_browser_owner_requires_idle_exact_cold_source_and_never_borrows(tmp_path, condition):
    async with cold_gateway(tmp_path) as (gateway, send, adapter):
        orchestrator = gateway._orchestrator
        runtime = orchestrator._runtimes['main']
        original = None
        if condition == 'busy':
            assert orchestrator.try_reserve_group('main')
        elif condition == 'existing':
            original = runtime.get_or_create_session(allow_reset=False)
        try:
            value = {'type': 'mcp_control', 'request_id': 'browser-open', 'group_id': 'main', 'action': 'owner',
                     'view': 'invalid' if condition == 'invalid-view' else 'b' * 32,
                     'expected_session': 'foreign' if condition == 'wrong-expectation' else ''}
            response = await send(value)
            assert response['request_id'] == value['request_id'] and response['view'] == value['view']
            assert response['expected_session'] == value['expected_session']
            if condition == 'idle':
                assert response['success'] is True and response['session_id']
                assert runtime._active_session.meta.session_id == response['session_id']
                assert not runtime._active_session.agent.messages
                # A second cold snapshot cannot adopt even that just-created session.
                second = await send({**value, 'request_id': 'another-browser'})
                assert second['success'] is False and 'changed' in second['error']
                assert 'session_id' not in second
            else:
                assert response['success'] is False and 'session_id' not in response
                assert runtime._active_session is original
            assert not adapter.calls
            assert not hasattr(runtime.mcp, '_sampling') and not hasattr(runtime.mcp, '_elicitation')
        finally:
            if condition == 'busy':
                orchestrator.release_group_reservation('main')


def native_browser(monkeypatch, *, supported=True):
    ui = ChatApp('/unused-owned-test-socket')
    sent = []
    monkeypatch.setattr(ui, '_queue_request', sent.append)
    ui._connection_ready = ui._mcp_supported = True
    ui._mcp_browser_owner_supported = supported
    ui._open_mcp()
    return ui, sent


def test_native_owner_reply_requires_same_view_context_and_does_not_auto_arm(monkeypatch):
    ui, sent = native_browser(monkeypatch)
    try:
        value = next(row for row in sent if row.get('action') == 'owner')
        pending = ui._mcp_owner_request
        result = {'type': 'mcp_result', **{key: value[key] for key in
                  ('request_id', 'group_id', 'view', 'expected_session')},
                  'success': True, 'session_id': 'authoritative-session', 'servers': []}
        ui._handle_response({**result, 'view': 'f' * 32})
        assert ui._mcp_owner_request == pending and ui._gateway_session_id == ''
        ui._handle_response(result)
        assert ui._gateway_session_id == 'authoritative-session'
        assert ui._mcp_owner_context == ui._mcp_context_key()
        assert ui._mcp_owner_request is None and not ui._mcp_panel._busy
        assert not any(row['type'] in {'mcp_sampling_control', 'mcp_elicitation_control'} for row in sent)
    finally:
        ui._request_exit()


@pytest.mark.parametrize('action', ['elicitation_on'])
def test_old_gateway_never_receives_empty_owner_arm_and_local_request_settles(monkeypatch, action):
    ui, sent = native_browser(monkeypatch, supported=False)
    try:
        assert not any(row.get('action') == 'owner' for row in sent)
        ui._mcp_panel._busy = True
        ui._mcp_send(action, 'modern', '', {})
        assert ui._mcp_panel._busy  # A retired operation cannot unlock this pending owner request.
        assert not any(row['type'] in {'mcp_sampling_control', 'mcp_elicitation_control'} for row in sent)
    finally:
        ui._request_exit()


def test_closed_browser_cannot_adopt_late_selected_owner(monkeypatch):
    ui, sent = native_browser(monkeypatch)
    try:
        value = next(row for row in sent if row.get('action') == 'owner')
        ui._close_mcp()
        ui._handle_response({'type': 'mcp_result', **{key: value[key] for key in
                            ('request_id', 'group_id', 'view', 'expected_session')},
                            'success': True, 'session_id': 'late-session', 'servers': []})
        assert ui._gateway_session_id == '' and ui._mcp_panel is None
    finally:
        ui._request_exit()


@pytest.mark.parametrize('failure', ['refused', 'withdrawn', 'empty-result'])
def test_native_owner_failure_settles_without_adoption_or_review_arm(monkeypatch, failure):
    ui, sent = native_browser(monkeypatch)
    try:
        value = next(row for row in sent if row.get('action') == 'owner')
        if failure == 'withdrawn':
            ui._mcp_browser_owner_supported = False
        ui._handle_response({'type': 'mcp_result', **{key: value[key] for key in
                            ('request_id', 'group_id', 'view', 'expected_session')},
                            'success': failure != 'refused', 'error': 'Original owner unavailable',
                            'session_id': '' if failure == 'empty-result' else 'must-not-adopt', 'servers': []})
        assert ui._gateway_session_id == '' and ui._mcp_owner_request is None
        assert not ui._mcp_panel._busy
        assert 'unavailable' in ui._mcp_panel._message
        assert not any(row['type'] in {'mcp_sampling_control', 'mcp_elicitation_control'} for row in sent)
    finally:
        ui._request_exit()


def test_retired_sampling_cannot_unlock_an_unrelated_native_request(monkeypatch):
    ui, sent = native_browser(monkeypatch, supported=False)
    try:
        panel = ui._mcp_panel
        panel._busy = True
        panel._message = 'A newer resource read owns this panel'
        original_sent = list(sent)
        owner_request = ui._mcp_owner_request
        ui._mcp_send('sampling_on', 'modern', '', {})
        assert panel._busy is True
        assert panel._message == 'A newer resource read owns this panel'
        assert sent == original_sent and ui._mcp_owner_request == owner_request
    finally:
        ui._request_exit()
