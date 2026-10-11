"""Actual two-host native backend consent wiring on Linux; native OS execution deferred."""
from __future__ import annotations

import asyncio
import json
import sys
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.agent.mcp.oauth import OAuthError, OAuthOwner, OAuthReview
from xdog.agent.mcp.oauth_native_store import NativeCredentialStore
from xdog.ai.types import AssistantMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.settings_manager import SettingsManager
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
from xdog.tui.keys import KeyEvent


@pytest.mark.parametrize('kind', ['coding', 'claw'])
@pytest.mark.parametrize('backend,platform', [('keychain', 'darwin'), ('windows-credential', 'win32')])
async def test_actual_host_native_backend_review_save_restore_forget_no_model_or_file(
    tmp_path, monkeypatch, kind, backend, platform,
):
    import xdog.agent.mcp.oauth_native_store as module
    monkeypatch.setattr(module, 'sys', SimpleNamespace(platform=platform, executable=sys.executable))
    rows, calls = {}, []
    def native_run(store, action, resource, alive, payload=''):
        assert alive()
        key = store.namespace, resource
        if action == 'save':
            rows[key] = payload
        elif action == 'forget':
            rows.pop(key, None)
        elif key not in rows:
            raise OAuthError('Native saved record absent')
        return {'ok': True, **({'payload': rows[key]} if action == 'read' else {})}
    monkeypatch.setattr(NativeCredentialStore, '_run', native_run)
    def stream(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())
    resource = 'https://resource.test/mcp'
    profile = {'issuer': 'https://issuer.test', 'client_id': 'client', 'scopes': ['read']}
    configs = {'peer': {'transport': 'http', 'url': resource, 'oauth': profile}}
    server = session = task = writer = None
    if kind == 'coding':
        monkeypatch.setenv('CODING_DIR', str(tmp_path / 'private-data'))
        settings = tmp_path / 'settings.json'
        settings.write_text(json.dumps({'mcp_servers': configs, 'mcp_credential_backend': backend}))
        store = SessionManager(tmp_path / 'sessions')
        session = AgentSession(agent=Agent(stream, config=AgentConfig(model='offline/model')),
            session_data=store.create_session(working_dir=tmp_path), session_manager=store,
            settings=SettingsManager(global_path=settings), working_dir=tmp_path, tool_registry=None, bash=None)
        ui = InteractiveMode(session)
        manager = session.mcp
    else:
        config = ClawConfig(model='offline/model', data_dir=str(tmp_path / 'data'),
            socket_path=str(tmp_path / 'gateway.sock'), pid_file=str(tmp_path / 'gateway.pid'),
            groups=(GroupDef(id='main', is_main=True, workspace=str(tmp_path / 'workspace')),),
            mcp_servers=configs, mcp_credential_backend=backend)
        server = GatewayServer(config)
        with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=('offline/model', stream)):
            await server.start()
        manager = server._orchestrator._runtimes['main'].mcp
        ui = ChatApp(config.socket_path)
        reader, writer = await asyncio.open_unix_connection(config.socket_path)
        task = asyncio.create_task(ui._run_connection(reader, writer))
    async def wait(predicate):
        async with asyncio.timeout(8):
            while True:
                ui._poll()
                if predicate():
                    return
                await asyncio.sleep(0.003)
    def review(panel):
        for _ in range(100):
            panel.render(80)
            if panel._visible:
                return
            panel.handle_input(KeyEvent('pagedown'))
        raise AssertionError('Native storage review not fully painted')
    try:
        if kind == 'claw':
            await wait(lambda: ui._connection_ready)
        assert manager.credential_store.backend == backend
        assert not manager.credential_store.directory.exists()
        owner = OAuthOwner(resource, manager.configs['peer'].oauth)
        owner._token, owner._refresh = 'PRIVATE-UI-ACCESS', 'PRIVATE-UI-REFRESH'
        owner._expires = time.monotonic() + 1000
        owner._grant = OAuthReview('a' * 32, profile['issuer'], resource, 'client', ('read',),
                                  'https://issuer.test/authorize', 'https://issuer.test/token')
        manager._oauth['peer'] = owner
        ui._editor.set_text('UNSUBMITTED PARENT DRAFT')
        ui._open_mcp()
        await wait(lambda: not ui._mcp_panel._busy)
        ui._mcp_panel.handle_input(KeyEvent('s'))
        await wait(lambda: ui._mcp_panel.auth is not None)
        ui._mcp_panel.auth.handle_input(KeyEvent('p'))
        await wait(lambda: ui._mcp_panel.auth.phase == 'credentials_save')
        auth = ui._mcp_panel.auth
        assert auth.review['storage_backend'] == backend and 'NATIVE' in auth.review['storage_notice']
        assert 'PRIVATE-UI' not in json.dumps(auth.review)
        auth.handle_input(KeyEvent('enter'))
        assert not rows  # No unpainted confirmation bypass.
        review(auth)
        auth.handle_input(KeyEvent('enter'))
        await wait(lambda: auth.phase == 'authorized')
        assert len(rows) == 1 and not manager.credential_store.directory.exists()
        owner._token, owner._refresh = '', ''
        ui._mcp_send('auth_credentials_restore_prepare', 'peer', '', {})
        await wait(lambda: ui._mcp_panel.auth.phase == 'credentials_restore')
        auth = ui._mcp_panel.auth
        assert owner.credential() is None
        review(auth)
        auth.handle_input(KeyEvent('enter'))
        await wait(lambda: auth.phase == 'authorized')
        assert owner.credential() == 'PRIVATE-UI-ACCESS' and not manager._clients
        ui._mcp_send('auth_credentials_forget_prepare', 'peer', '', {})
        await wait(lambda: ui._mcp_panel.auth.phase == 'credentials_forget')
        auth = ui._mcp_panel.auth
        review(auth)
        auth.handle_input(KeyEvent('enter'))
        await wait(lambda: auth.phase == 'authorized')
        assert not rows and not calls
        ui._close_mcp()
        assert ui._editor.get_text() == 'UNSUBMITTED PARENT DRAFT'
    finally:
        ui._request_exit()
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            writer.close()
            await writer.wait_closed()
        if server is not None:
            await server.stop()
        if session is not None:
            await session.children.supervisor.aclose()
            await session.extensions.aclose()
            await session.mcp.aclose()
            session.dispose()
