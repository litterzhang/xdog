"""Native Claw provider authentication: cold explicit ownership, private code, no model permission."""
from __future__ import annotations

import asyncio
import json
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
from xdog.ai.types import AssistantMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer
from xdog.tui.components.extension_provider_auth import ExtensionProviderAuth
from xdog.tui.keys import KeyEvent

from packages.agent.tests.test_extension_provider_oauth import ACCESS, CODE, PROVIDER, REFRESH
from packages.claw.tests.test_extension_message_gateway import handshake, peer
from packages.claw.tests.test_extension_message_ui import native_ui
from packages.tui.tests.test_extension_provider_oauth_panel import paint


@asynccontextmanager
async def auth_server(tmp_path, *, settings=None):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/provideroauth', root / 'provideroauth')
    calls = []
    def stream(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())
    config = ClawConfig(model='offline/model', data_dir=str(tmp_path / 'data'),
        socket_path=str(tmp_path / 'socket'), pid_file=str(tmp_path / 'pid'),
        groups=(GroupDef(id='main', is_main=True, workspace=str(tmp_path)),),
        extensions=('provideroauth',), extension_dir=str(root),
        extension_settings={'provideroauth': settings or {}})
    server = GatewayServer(config)
    with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=('offline/model', stream)):
        await server.start()
    runtime = server._orchestrator._runtimes['main']
    assert runtime._active_session is None and not runtime.get_new_session_id()
    try:
        yield server, runtime, config, calls
    finally:
        await asyncio.wait_for(server.stop(), 10)


@pytest.mark.parametrize('ending', ['accept', 'deny', 'cancel', 'withdraw', 'foreign', 'menu', 'old-client'])
async def test_cold_original_native_login_masked_review_and_owned_capability(tmp_path, monkeypatch, ending):
    async with auth_server(tmp_path) as (server, runtime, config, calls):
        async with native_ui(config) as (ui, wait):
            assert not ui._gateway_session_id and ui._extension_provider_auth_supported
            ui._editor.set_text('PRIVATE ORIGINAL AUTH DRAFT')
            ui._editor._cursor = 4
            opened = []
            monkeypatch.setattr(ui._reviewed_url, 'start', lambda *args: opened.append(args))
            if ending == 'old-client':
                ui._queue_request({'type': 'ping', 'extension_provider_auth': 0})
                await wait(lambda: not ui._extension_provider_auth_supported)
                ui._extension_command('extensions', 'auth login ' + PROVIDER)
                assert ui._extension_pending is None and runtime._active_session is None
                assert ui._editor.get_text() == 'PRIVATE ORIGINAL AUTH DRAFT' and not calls
                return
            panels = []
            if ending in {'menu', 'accept'}:
                ui._handle_submit('/login' if ending == 'menu' else '/login ' + PROVIDER)
            else:
                ui._extension_command('extensions', 'auth login ' + PROVIDER)
            if ending == 'menu':
                await wait(lambda: ui._extension_dialog_panel is not None)
                first = ui._extension_dialog_panel
                assert first.dialog['choices'][0] == 'Cancel'
                assert 'provider_auth_menu' in first.dialog
                first.handle_input(KeyEvent('down'))
                first.render(80)
                first.handle_input(KeyEvent('enter'))
                await wait(lambda: ui._extension_dialog_panel is not None and ui._extension_dialog_panel is not first)
                second = ui._extension_dialog_panel
                assert second.dialog['choices'] == ['Cancel', 'login', 'refresh', 'logout']
                second.handle_input(KeyEvent('down'))
                second.render(80)
                second.handle_input(KeyEvent('enter'))
            for phase in ('consent', 'on_auth', 'on_prompt'):
                await wait(lambda: isinstance(ui._extension_dialog_panel, ExtensionProviderAuth)
                    and ui._extension_dialog_panel.dialog['provider_auth']['phase'] == phase)
                panel = ui._extension_dialog_panel
                panels.append(panel)
                assert ui._gateway_session_id == runtime.get_new_session_id()
                assert ui._extension_auth_owner[1] == ui._mcp_context_key()
                assert server._orchestrator.is_group_running('main') and not calls
                if ending == 'foreign' and phase == 'consent':
                    async with peer(config) as (send, receive, _writer):
                        await send({'type': 'extension_dialog_answer', 'id': panel.dialog['id'], 'value': True})
                        assert (await receive('extension_dialog_ack'))['accepted'] is False
                if ending == 'deny' and phase == 'consent' or ending == 'cancel' and phase == 'on_prompt':
                    panel.handle_input(KeyEvent('escape'))
                    break
                if ending == 'withdraw' and phase == 'on_prompt':
                    ui._queue_request({'type': 'ping', 'extension_provider_auth': 0})
                    await wait(lambda: not ui._extension_provider_auth_supported)
                    break
                paint(panel)
                panel.handle_input(KeyEvent('enter'))
                if phase == 'on_prompt':
                    panel.handle_paste(CODE)
                    assert CODE not in str(panel.render(80)) + repr(panel._input.__dict__)
                    panel.handle_input(KeyEvent('enter'))
            await wait(lambda: ui._extension_pending is None)
            await wait(lambda: not server._orchestrator.is_group_running('main'))
            assert ui._extension_auth_owner is None and not calls and not opened
            assert ui._editor.get_text() == 'PRIVATE ORIGINAL AUTH DRAFT' and ui._editor._cursor == 4
            session = runtime._active_session
            assert not session.agent.messages and session.agent.state.model == 'offline/model'
            public = str(ui._chat_log.render(180)) + repr(session.extensions.snapshot())
            public += json.dumps([panel.dialog for panel in panels])
            assert all(secret not in public for secret in (ACCESS, REFRESH, CODE))
            assert all(not panel._secret for panel in panels)
            if ending in {'accept', 'foreign', 'menu'}:
                assert session.extensions.provider_auth_catalog()[0]['authenticated']
                assert await session.extensions.command('ext-provideroauth-count', '') == (
                    'login=1;refresh=0;key=0;models=1;stream=0')
            elif ending == 'deny':
                assert not session.extensions.provider_auth_catalog()[0]['authenticated']


@pytest.mark.parametrize('version', [None, True, '1', 0])
async def test_provider_auth_default_off_does_not_borrow_other_extension_grants(tmp_path, version):
    async with auth_server(tmp_path) as (_, runtime, config, calls):
        async with peer(config) as (send, receive, _writer):
            caps = await handshake(send, receive, extension_message=1, extension_editor_write=1,
                **({'extension_provider_auth': version} if version is not None else {}))
            assert caps['extension_provider_auth'] == 0
            await send({'type': 'extension_control', 'request_id': 'off', 'group_id': 'main', 'session_id': '',
                        'action': 'auth', 'args': 'login ' + PROVIDER})
            result = await receive('extension_result')
            assert not result['success'] and not calls and runtime._active_session is None


async def test_private_code_is_cleared_on_capability_withdrawal_before_submission(tmp_path):
    async with auth_server(tmp_path) as (_, runtime, config, calls):
        async with native_ui(config) as (ui, wait):
            ui._handle_submit('/login ' + PROVIDER)
            for phase in ('consent', 'on_auth', 'on_prompt'):
                await wait(lambda: isinstance(ui._extension_dialog_panel, ExtensionProviderAuth)
                    and ui._extension_dialog_panel.dialog['provider_auth']['phase'] == phase)
                panel = ui._extension_dialog_panel
                paint(panel)
                panel.handle_input(KeyEvent('enter'))
            panel.handle_paste(CODE)
            panel.render(80)
            assert panel._secret == CODE
            ui._queue_request({'type': 'ping', 'extension_provider_auth': 0})
            await wait(lambda: not ui._extension_provider_auth_supported)
            assert not panel._secret
            await wait(lambda: ui._extension_pending is None)
            assert not calls and not runtime._active_session.agent.messages
            assert CODE not in str(ui._chat_log.render(150))


async def test_auth_view_transport_failure_resolves_original_broker_without_login_or_stuck_owner(tmp_path, monkeypatch):
    async with auth_server(tmp_path) as (server, runtime, config, calls):
        async with native_ui(config) as (ui, wait):
            real_write = asyncio.StreamWriter.write
            blocked = []
            def write(writer, data):
                if b'"type": "extension_dialog"' in data and b'"provider_auth_host"' in data:
                    blocked.append(data)
                    raise BrokenPipeError('Injected auth consent transport failure')
                return real_write(writer, data)
            monkeypatch.setattr(asyncio.StreamWriter, 'write', write)
            ui._extension_command('extensions', 'auth login ' + PROVIDER)
            await wait(lambda: ui._extension_pending is None)
            await wait(lambda: not server._orchestrator.is_group_running('main'))
            assert len(blocked) == 1 and not calls
            session = runtime._active_session
            assert await session.extensions.command('ext-provideroauth-count', '') == (
                'login=0;refresh=0;key=0;models=0;stream=0')
            assert ui._extension_dialog_panel is None and not session.extension_dialogs.has_pending


async def test_auth_storage_requires_separate_full_consent_and_no_default_namespace(tmp_path):
    async with auth_server(tmp_path) as (_, runtime, config, calls):
        async with native_ui(config) as (ui, wait):
            ui._extension_command('extensions', f'auth save {PROVIDER} secret-service {tmp_path / "auth-store"}')
            await wait(lambda: isinstance(ui._extension_dialog_panel, ExtensionProviderAuth))
            panel = ui._extension_dialog_panel
            body = panel.dialog['provider_auth']
            assert body['action'] == 'save' and body['phase'] == 'consent'
            panel.handle_input(KeyEvent('escape'))
            await wait(lambda: ui._extension_pending is None)
            assert not calls and not (tmp_path / 'auth-store').exists()
            assert not runtime._active_session.agent.messages


@pytest.mark.parametrize('reenable', [False, True])
async def test_auth_withdrawal_before_coroutine_admission_has_one_canceled_receipt(tmp_path, monkeypatch, reenable):
    async with auth_server(tmp_path) as (server, runtime, config, calls):
        results = []
        original = server._write_response
        async def record(writer, data):
            if data.get('type') == 'extension_result':
                results.append(data)
            await original(writer, data)
        monkeypatch.setattr(server, '_write_response', record)
        async with peer(config) as (send, receive, writer):
            await handshake(send, receive, extension_provider_auth=1)
            batch = [{'type': 'extension_control', 'request_id': 'pre-admission', 'group_id': 'main', 'session_id': '',
                      'action': 'auth', 'args': 'login ' + PROVIDER},
                     {'type': 'ping', 'extension_provider_auth': 0}]
            if reenable:
                batch.append({'type': 'ping', 'extension_provider_auth': 1})
            writer.write(('\n'.join(json.dumps(row) for row in batch) + '\n').encode())
            await writer.drain()
            async with asyncio.timeout(2):
                result = await receive('extension_result')
            assert result['canceled'] and not result['success'] and result['request_id'] == 'pre-admission'
            async with asyncio.timeout(3):
                while server._orchestrator.is_group_running('main'):
                    await asyncio.sleep(.003)
            assert len(results) == 1 and not calls and runtime._active_session is None
