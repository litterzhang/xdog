"""Native readonly controls change host scope, never model input or tool approval."""
from __future__ import annotations

import asyncio
import importlib
import threading
from unittest.mock import AsyncMock, Mock, patch

import pytest
from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer
from xdog.coding.core.permissions import PermissionRequest
from xdog.tui.attachments import Attachment

from packages.coding.tests.test_session_name_ui import app, wait


async def test_coding_sync_access_controls_use_owned_worker_and_preserve_draft_permissions(tmp_path, monkeypatch):
    ui, session, _manager = app(tmp_path)
    original = session.access_control
    threads = []
    def control(value):
        assert type(value) is bool
        threads.append(threading.get_ident())
        return original(value)
    monkeypatch.setattr(session, 'access_control', control)
    before = tuple(session.agent.messages)
    ui._editor.set_text('PRIMARY 草稿 /quit')
    ui._editor._cursor = 3
    image = Attachment('retained', 'original', 'image/png', b'opaque', 1, 1)
    ui._composer.items[image.id] = image
    try:
        for command, expected in [('status', False), ('on', True), ('status', True), ('off', False)]:
            ui._handle_slash_command('readonly', command)
            await wait(ui, lambda: ui._command is None)
            assert session.access.state.read_only is expected
            assert ui._editor.get_text() == 'PRIMARY 草稿 /quit' and ui._editor._cursor == 3
            assert ui._composer.items == {image.id: image} and session.permissions.mode == 'deny'
            assert tuple(session.agent.messages) == before
        assert len(threads) == 2 and all(value != threading.get_ident() for value in threads)
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module('xdog.tui.components.plan_panel')
        assert not any(name.startswith('_plan_') for name in vars(ui))
        assert 'plan' not in {name.removeprefix('/') for name in ui._editor._command_provider()}
        ui._handle_slash_command('plan', 'execute')
        ui._handle_slash_command('readonly', 'on extra')
        assert ui._command is None and not session.access.state.read_only
    finally:
        ui._request_exit()
        session.dispose()


async def test_coding_access_cannot_take_protected_approval_or_mutate_while_busy(tmp_path):
    ui, session, _manager = app(tmp_path)
    try:
        ui._show_permission_request(PermissionRequest('owned', 'bash', {}, 'protected review'))
        approval = ui._permission_prompt
        ui._handle_slash_command('readonly', 'on')
        assert ui._command is None and ui._tui._focused is approval
        assert not session.access.state.read_only and session.permissions.mode == 'deny'
        approval._on_decision('deny')
        ui._worker_active = True
        ui._handle_slash_command('readonly', 'on')
        assert ui._command is None and not session.access.state.read_only
        ui._worker_active = False
    finally:
        ui._request_exit()
        session.dispose()


def connected_access(monkeypatch):
    ui = ChatApp('/unused-readonly-ui.sock')
    sent = []
    monkeypatch.setattr(ui, '_queue_request', sent.append)
    ui._handle_response({'type': 'pong', 'session_id': 'owner', 'history_count': 0, 'access_controls': 1})
    return ui, sent


def access_reply(ui, request, **changes):
    return {'type': 'access_control_result', 'request_id': request['request_id'], 'group_id': ui.group_id,
            'session_id': 'owner', 'success': True, 'access': {'read_only': True, 'revision': 2}, **changes}


@pytest.mark.parametrize('argument,action', [('on', 'restrict'), ('off', 'release'),
                                            ('status', 'status'), ('', 'status')])
def test_claw_readonly_exact_packets_and_receipts_never_model_input(monkeypatch, argument, action):
    ui, sent = connected_access(monkeypatch)
    ui._editor.set_text('PRIMARY 草稿')
    try:
        ui._handle_submit('/readonly ' + argument)
        request = sent[-1]
        assert request == {'type': 'access_control', 'request_id': request['request_id'],
                           'group_id': 'main', 'session_id': 'owner', 'action': action}
        ui._handle_response(access_reply(ui, request))
        assert ui._access_read_only is True and ui._access_revision == 2 and ui._pending_command is None
        assert ui._editor.get_text() == 'PRIMARY 草稿' and ui._active_run_id is None
        assert not any(name.startswith('_plan_') for name in vars(ui))
        before = list(sent)
        ui._handle_submit('/plan execute')
        ui._handle_submit('/readonly off extra')
        assert sent == before
    finally:
        ui._request_exit()


@pytest.mark.parametrize('access', [{'read_only': 1, 'revision': 2}, {'read_only': True, 'revision': True},
                                   {'read_only': False, 'revision': -1}, {'read_only': False, 'revision': 1},
                                   {'read_only': False, 'revision': 3, 'grant': True}])
def test_claw_malformed_or_older_receipt_cannot_release_scope_or_clear_successor(monkeypatch, access):
    ui, sent = connected_access(monkeypatch)
    try:
        ui._readonly_command('on')
        ui._handle_response(access_reply(ui, sent[-1]))
        ui._readonly_command('off')
        request = sent[-1]
        ui._pending_command = 'newer-unrelated-command'
        ui._handle_response(access_reply(ui, request, access=access))
        assert ui._access_read_only is True and ui._access_revision == 2
        assert ui._pending_command == 'newer-unrelated-command'
        before = list(sent)
        ui._handle_response(access_reply(ui, request, access={'read_only': False, 'revision': 3}))
        assert ui._access_read_only is True and sent == before
    finally:
        ui._request_exit()


def test_claw_access_context_timeout_capability_and_protected_modal_fences(monkeypatch):
    ui, sent = connected_access(monkeypatch)
    try:
        with patch.object(ui._tui, 'has_overlay', return_value=True):
            ui._readonly_command('off')
        assert not sent
        ui._active_run_id = 'run'
        ui._readonly_command('on')
        assert not sent
        ui._active_run_id = None
        ui._readonly_command('on')
        request = sent[-1]
        ui._handle_response(access_reply(ui, request, request_id='foreign'))
        assert ui._access_pending is not None and ui._access_read_only is None
        ui._handle_response(access_reply(ui, request, group_id='foreign'))
        assert ui._access_pending is not None
        ui._handle_response(access_reply(ui, request, session_id='foreign'))
        assert ui._access_pending is None and ui._access_read_only is None
        ui._readonly_command('status')
        pending = ui._access_pending
        ui._access_pending = (*pending[:3], 0)
        ui._pending_command = 'successor'
        ui._poll()
        assert ui._access_pending is None and ui._pending_command == 'successor'
        ui._pending_command = None
        ui._readonly_command('on')
        before = list(sent)
        ui._handle_response({'type': '_connection_opened', 'generation': 1})
        assert ui._access_pending is None and not ui._access_supported and ui._access_read_only is None
        ui._handle_response(access_reply(ui, request))
        assert sent == before and ui._access_read_only is None
        ui._handle_response({'type': 'pong', 'session_id': 'owner', 'access_controls': True})
        ui._readonly_command('off')
        assert not ui._access_supported and sent == before  # bool is not capability version 1.
    finally:
        ui._request_exit()


async def test_claw_hello_has_access_not_planning_or_watch_negotiation():
    ui = ChatApp('/unused-readonly-ui.sock')
    reader, writer = asyncio.StreamReader(), AsyncMock()
    writer.write = Mock()
    owner = asyncio.create_task(ui._run_connection(reader, writer))
    try:
        async with asyncio.timeout(2):
            while not writer.write.call_args_list:
                await asyncio.sleep(0.002)
        import json
        hello = json.loads(writer.write.call_args_list[0].args[0])
        assert hello['access_controls'] == 1
        assert not any('watch' in key or 'plan' in key or 'recovery' in key for key in hello)
    finally:
        owner.cancel()
        await asyncio.gather(owner, return_exceptions=True)
        ui._request_exit()


async def test_claw_actual_gateway_readonly_controls_preserve_native_owner_and_draft(tmp_path):
    calls = []
    def stream(*args):
        calls.append(args)
        pytest.fail('Readonly command must never call a model')
    config = ClawConfig(model='offline/model', data_dir=str(tmp_path / 'data'),
                       socket_path=str(tmp_path / 'access.sock'), pid_file=str(tmp_path / 'access.pid'),
                       groups=(GroupDef(id='main', is_main=True, workspace=str(tmp_path)),))
    server = GatewayServer(config)
    with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=(config.model, stream)):
        await server.start()
    session = server._orchestrator._runtimes['main'].get_or_create_session(allow_reset=False)
    ui = ChatApp(config.socket_path)
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    owner = asyncio.create_task(ui._run_connection(reader, writer))
    try:
        await wait(ui, lambda: ui._connection_ready)
        assert ui._access_supported
        ui._editor.set_text('KEEP OWNER DRAFT')
        for command, expected in [('on', True), ('status', True), ('off', False)]:
            ui._handle_submit('/readonly ' + command)
            assert ui._access_pending is not None
            await wait(ui, lambda: ui._access_pending is None)
            assert ui._access_read_only is expected and session.access.state.read_only is expected
            assert ui._gateway_session_id == session._meta.session_id and ui._editor.get_text() == 'KEEP OWNER DRAFT'
            assert not calls and not session.agent.messages
    finally:
        ui._request_exit()
        owner.cancel()
        await asyncio.gather(owner, return_exceptions=True)
        writer.close()
        await writer.wait_closed()
        await server.stop()
