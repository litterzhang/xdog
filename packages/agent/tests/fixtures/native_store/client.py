"""Actual application worker + consent/profile/cancellation path on an owned native service."""
import asyncio
import json
import os
import subprocess
import time
from pathlib import Path

from xdog.agent.mcp import McpRegistry
from xdog.agent.mcp.oauth import OAuthConfig, OAuthError, OAuthOwner, OAuthReview
from xdog.agent.mcp.oauth_native_store import NativeCredentialStore
from xdog.agent.mcp.oauth_store import capture, restore

root = Path(os.environ['XDOG_NATIVE_TEST_ROOT'])
resource = 'https://resource.test/mcp'
profile = {'issuer': 'https://issuer.test', 'client_id': 'client', 'scopes': ['read']}
owner = OAuthOwner(resource, OAuthConfig.parse(profile))
owner._token, owner._refresh = 'PRIVATE-NATIVE-DUMMY-ACCESS', 'PRIVATE-NATIVE-DUMMY-REFRESH'
owner._expires = time.monotonic() + 1200
owner._grant = OAuthReview('a' * 32, profile['issuer'], resource, 'client', ('read',),
                          profile['issuer'] + '/authorize', profile['issuer'] + '/token')
store = NativeCredentialStore(root / 'scope')
record = capture(owner)
store.save(record, lambda: True)
assert store.read(resource) == record
assert not store.directory.exists()
assert 'PLAINTEXT' not in store.public(record, 'save')['storage_notice']
try:
    NativeCredentialStore(root / 'another-scope').read(resource)
except OAuthError:
    pass
else:
    raise AssertionError('Foreign namespace read a credential')
try:
    store.save(record, lambda: False)
except OAuthError:
    pass
else:
    raise AssertionError('Canceled native save was admitted')
store.forget(resource)
store.forget(resource)  # Exact namespace erase is idempotent, never login/refresh.
try:
    store.read(resource)
except OAuthError:
    pass
else:
    raise AssertionError('Forgotten native token reappeared')

async def managed():
    config = {'peer': {'transport': 'http', 'url': resource, 'oauth': profile}}
    manager = McpRegistry(config, root, credential_dir=root / 'managed', credential_backend='secret-service')
    fresh = None
    try:
        manager._oauth['peer'] = owner
        try:
            await manager.manage_auth('peer', 'credentials_save', 'unreviewed', lease='one')
        except Exception:
            pass
        else:
            raise AssertionError('Native save bypassed consent')
        review = await manager.manage_auth('peer', 'credentials_save_prepare', lease='one')
        assert review['storage_backend'] == 'secret-service' and 'PRIVATE-NATIVE' not in json.dumps(review)
        assert 'NATIVE SECRET SERVICE' in review['storage_notice']
        await manager.manage_auth('peer', 'credentials_save', review['review_id'], lease='one')
        fresh = McpRegistry(config, root, credential_dir=root / 'managed', credential_backend='secret-service')
        assert not fresh._oauth and not fresh._clients
        loaded = await fresh.manage_auth('peer', 'credentials_restore_prepare', lease='two')
        assert fresh._oauth['peer'].credential() is None
        await fresh.manage_auth('peer', 'credentials_restore', loaded['review_id'], lease='two')
        assert fresh._oauth['peer'].credential() == owner._token and not fresh._clients
        erase = await fresh.manage_auth('peer', 'credentials_forget_prepare', lease='two')
        await fresh.manage_auth('peer', 'credentials_forget', erase['review_id'], lease='two')
    finally:
        await manager.aclose()
        if fresh is not None:
            await fresh.aclose()
asyncio.run(managed())

# Drive BOTH production UI owners against the same private native service.
async def native_ui(kind):
    from unittest.mock import patch

    from xdog.agent import Agent, AgentConfig
    from xdog.ai.types import AssistantMessage
    from xdog.ai.utils.event_stream import EventStream
    from xdog.tui.keys import KeyEvent
    calls = []
    def stream(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())
    server = task = writer = None
    config = {'peer': {'transport': 'http', 'url': resource, 'oauth': profile}}
    if kind == 'coding':
        from xdog.coding.core.agent_session import AgentSession
        from xdog.coding.core.session_manager import SessionManager
        from xdog.coding.core.settings_manager import SettingsManager
        from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
        data = root / 'coding'
        data.mkdir()
        os.environ['CODING_DIR'] = str(data)
        settings = data / 'settings.json'
        settings.write_text(json.dumps({'mcp_servers': config, 'mcp_credential_backend': 'secret-service'}))
        manager = SessionManager(data / 'sessions')
        session = AgentSession(agent=Agent(stream, config=AgentConfig(model='offline/model')),
            session_data=manager.create_session(model='offline/model', working_dir=data), session_manager=manager,
            settings=SettingsManager(global_path=settings), working_dir=data, tool_registry=None, bash=None)
        ui = InteractiveMode(session)
        actual = session.mcp
    else:
        from xdog.claw.channels.tui.tui_app import ChatApp
        from xdog.claw.config import ClawConfig, GroupDef
        from xdog.claw.core.runtime.gateway import GatewayServer
        app = ClawConfig(model='offline/model', data_dir=str(root / 'claw'),
            socket_path=str(root / 'claw.sock'), pid_file=str(root / 'claw.pid'),
            groups=(GroupDef(id='main', is_main=True),), mcp_servers=config, mcp_credential_backend='secret-service')
        server = GatewayServer(app)
        with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=(app.model, stream)):
            await server.start()
        actual = server._orchestrator._runtimes['main'].mcp
        ui = ChatApp(app.socket_path)
        reader, writer = await asyncio.open_unix_connection(app.socket_path)
        task = asyncio.create_task(ui._run_connection(reader, writer))
    async def wait(predicate):
        async with asyncio.timeout(8):
            while not predicate():
                ui._poll()
                await asyncio.sleep(0.005)
    def review(panel):
        for _ in range(100):
            panel.render(80)
            if panel._visible:
                return
            panel.handle_input(KeyEvent('pagedown'))
        raise AssertionError('Native storage notice never fully reviewed')
    try:
        if kind == 'claw':
            await wait(lambda: ui._connection_ready)
        ui_owner = OAuthOwner(resource, owner.config)
        restore(ui_owner, record)
        actual._oauth['peer'] = ui_owner
        ui._editor.set_text('UNSUBMITTED NATIVE STORE DRAFT')
        ui._open_mcp()
        await wait(lambda: not ui._mcp_panel._busy)
        ui._mcp_panel.handle_input(KeyEvent('s'))
        await wait(lambda: ui._mcp_panel.auth is not None)
        panel = ui._mcp_panel.auth
        assert panel.phase == 'authorized', (panel.phase, ui._mcp_panel._message)
        panel.handle_input(KeyEvent('p'))
        try:
            await wait(lambda: ui._mcp_panel.auth.phase == 'credentials_save')
        except TimeoutError:
            raise AssertionError((panel.phase, ui._mcp_panel._message,
                                  panel._error if hasattr(panel, '_error') else '')) from None
        panel = ui._mcp_panel.auth
        assert panel.phase == 'credentials_save' and panel.review['storage_backend'] == 'secret-service'
        assert 'NATIVE SECRET SERVICE' in panel.review['storage_notice']
        panel.handle_input(KeyEvent('enter'))
        assert panel.phase == 'credentials_save'  # No unpainted approval shortcut.
        review(panel)
        panel.handle_input(KeyEvent('enter'))
        await wait(lambda: panel.phase == 'authorized')
        assert actual.credential_store.read(resource).access == record.access
        assert not actual.credential_store.directory.exists()
        assert ui._editor.get_text() == 'UNSUBMITTED NATIVE STORE DRAFT' and not calls
        actual.credential_store.forget(resource)
    finally:
        ui._request_exit()
        if kind == 'coding':
            await session.children.runtime.aclose()
            await session.extensions.aclose()
            await actual.aclose()
            session.dispose()
        else:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            writer.close()
            await writer.wait_closed()
            await server.stop()
asyncio.run(native_ui('coding'))
asyncio.run(native_ui('claw'))

# Lock a saved dummy, then all operations must refuse WITHOUT desktop activation.
store.save(record, lambda: True)
lock = subprocess.run(['gdbus', 'call', '--session', '--dest', 'org.freedesktop.secrets',
    '--object-path', '/org/freedesktop/secrets', '--method', 'org.freedesktop.Secret.Service.Lock',
    "['/org/freedesktop/secrets/collection/login']"], capture_output=True, timeout=3)
assert lock.returncode == 0
for operation in (lambda: store.read(resource), lambda: store.save(record, lambda: True),
                  lambda: store.forget(resource)):
    start = time.monotonic()
    try:
        operation()
    except OAuthError as error:
        assert 'PRIVATE-NATIVE' not in str(error) and time.monotonic() - start < 4
    else:
        raise AssertionError('Locked native storage was accessed or unlocked')
print(json.dumps({'roundtrip': True, 'scoped': True, 'review_restore_forget': True,
                  'locked_refusal': True, 'no_plaintext_fallback': True, 'both_native_ui': True}))
