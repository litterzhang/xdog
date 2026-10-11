from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest
from xdog.agent import Agent, AgentConfig
from xdog.agent.mcp.oauth import OAuthOwner
from xdog.ai.types import AssistantMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.settings_manager import SettingsManager
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
from xdog.tui.keys import KeyEvent

FIXTURE = Path(__file__).parents[2] / "agent/tests/fixtures/oauth_ui_peer.py"


def peer_module():
    spec = importlib.util.spec_from_file_location("oauth_ui_peer", FIXTURE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def patch_transport(monkeypatch, peer):
    monkeypatch.setattr(OAuthOwner, "client", lambda self: httpx.AsyncClient(transport=peer.transport))
    import xdog.agent.mcp.http as transport
    original = httpx.AsyncClient
    monkeypatch.setattr(transport, "httpx", SimpleNamespace(
        AsyncClient=lambda **kwargs: original(transport=peer.transport, **kwargs),
        Limits=httpx.Limits, HTTPError=httpx.HTTPError, Headers=httpx.Headers))


def review_all(panel):
    for _ in range(100):
        panel.render(80)
        if panel._visible:
            return
        panel.handle_input(KeyEvent("pagedown"))
    raise AssertionError("OAuth review never became fully visible")


async def complete(url, issuer):
    args = parse_qs(urlsplit(url).query)
    redirect = urlsplit(args["redirect_uri"][0])
    reader, writer = await asyncio.open_connection(redirect.hostname, redirect.port)
    target = "/callback?" + urlencode({"code": "fixture-code", "state": args["state"][0], "iss": issuer})
    writer.write(("GET " + target + " HTTP/1.1\r\nHost: localhost\r\n\r\n").encode())
    await writer.drain()
    assert b"200 OK" in await reader.read()
    writer.close()
    await writer.wait_closed()


async def wait(ui, predicate):
    async with asyncio.timeout(8):
        while True:
            ui._poll()
            if predicate():
                return
            await asyncio.sleep(0.003)


def create(tmp_path, module):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"mcp_servers": {"peer": {"transport": "http", "url": module.RESOURCE,
                                                           "oauth": module.PROFILE}}}))
    store = SessionManager(tmp_path / "sessions")
    calls = []
    def stream(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())
    session = AgentSession(agent=Agent(stream, config=AgentConfig(model="test/model")),
        session_data=store.create_session(working_dir=tmp_path), session_manager=store,
        settings=SettingsManager(global_path=settings), working_dir=tmp_path, tool_registry=None, bash=None)
    return InteractiveMode(session), session, calls


async def test_coding_auth_consent_connect_forget_and_draft(tmp_path, monkeypatch):
    module = peer_module()
    peer = module.Peer()
    patch_transport(monkeypatch, peer)
    ui, session, calls = create(tmp_path, module)
    try:
        ui._editor.set_text("parent draft")
        ui._handle_slash_command("mcp", "authorize peer")
        await wait(ui, lambda: ui._mcp_panel is not None and ui._mcp_panel.auth is not None)
        auth = ui._mcp_panel.auth
        auth.handle_input(KeyEvent("enter"))
        assert session.mcp._oauth["peer"].flow is None and not calls
        review_all(auth)
        auth.handle_input(KeyEvent("enter"))
        await wait(ui, lambda: auth.phase in {"waiting", "failed"})
        assert auth.phase == "waiting", auth.message
        peer.launch(auth.url)
        await complete(auth.url, module.ISSUER)
        await wait(ui, lambda: auth.phase == "authorized")
        assert not session.mcp.tools() and session.permissions.mode == "ask" and not calls
        assert ui._editor.get_text() == "parent draft"
        auth.handle_input(KeyEvent("escape"))
        await wait(ui, lambda: not ui._mcp_panel._busy)
        ui._mcp_panel.handle_input(KeyEvent("c"))
        await wait(ui, lambda: not ui._mcp_panel._busy and ui._command is None)
        assert len(session.mcp.tools()) == 1 and not session.mcp.allowed(session.mcp.tools()[0].name)
        assert "FIXTURE-OAUTH-SECRET" not in "\n".join(ui._chat_log.render(80))
        ui._mcp_panel.handle_input(KeyEvent("s"))
        await wait(ui, lambda: ui._mcp_panel.auth is not None and ui._mcp_panel.auth.phase == "authorized")
        forget = ui._mcp_panel.auth
        forget.handle_input(KeyEvent("f"))
        review_all(forget)
        forget.handle_input(KeyEvent("enter"))
        await wait(ui, lambda: forget.phase == "forgotten")
        assert not session.mcp.tools()
        assert not any(tool.name.startswith("mcp_peer_") for tool in session.agent.state.tools)
        ui._close_mcp()
        assert ui._tui._focused is ui._editor and ui._editor.get_text() == "parent draft"
    finally:
        ui._request_exit()
        await session.mcp.aclose()
        session.dispose()


async def test_coding_leaving_waiting_auth_revokes_pending_callback_and_no_stale_token_commit(tmp_path, monkeypatch):
    module = peer_module()
    peer = module.Peer()
    patch_transport(monkeypatch, peer)
    ui, session, calls = create(tmp_path, module)
    try:
        ui._handle_slash_command("mcp", "authorize peer")
        await wait(ui, lambda: ui._mcp_panel is not None and ui._mcp_panel.auth is not None)
        auth = ui._mcp_panel.auth
        review_all(auth)
        auth.handle_input(KeyEvent("enter"))
        await wait(ui, lambda: auth.phase in {"waiting", "failed"})
        assert auth.phase == "waiting", auth.message
        owner = session.mcp._oauth["peer"]
        ui._close_mcp()
        await wait(ui, lambda: owner.status()["state"] == "canceled")
        assert owner.credential() is None and peer.token_posts == 0 and not calls
    finally:
        ui._request_exit()
        await session.mcp.aclose()
        session.dispose()


async def test_coding_dynamic_registration_requires_visible_consent_then_uses_issued_client(tmp_path, monkeypatch):
    module = peer_module()
    module.PROFILE = {"issuer": module.ISSUER, "scopes": ["tools:read"], "registration": "dynamic"}
    peer = module.Peer("dynamic")
    patch_transport(monkeypatch, peer)
    ui, session, calls = create(tmp_path, module)
    try:
        ui._handle_slash_command("mcp", "authorize peer")
        await wait(ui, lambda: ui._mcp_panel is not None and ui._mcp_panel.auth is not None)
        auth = ui._mcp_panel.auth
        assert auth.review["registration"] == "dynamic" and not peer.registration_posts
        auth.handle_input(KeyEvent("enter"))
        assert not peer.registration_posts
        review_all(auth)
        auth.handle_input(KeyEvent("enter"))
        await wait(ui, lambda: auth.phase in {"waiting", "failed"})
        assert auth.phase == "waiting", auth.message
        assert len(peer.registration_posts) == 1 and auth.review["client_id"] == "issued-native-client"
        peer.launch(auth.url)
        await complete(auth.url, module.ISSUER)
        await wait(ui, lambda: auth.phase == "authorized")
        assert not session.mcp.tools() and not calls and session.permissions.mode == "ask"
        assert "IGNORED-REGISTRATION-SECRET" not in "\n".join(ui._chat_log.render(80))
    finally:
        ui._request_exit()
        await session.mcp.aclose()
        session.dispose()


@pytest.mark.parametrize("method", ["client_secret_post", "client_secret_jwt"])
async def test_coding_confidential_method_is_reviewed_and_secret_never_enters_draft_or_status(
        tmp_path, monkeypatch, method):
    module = peer_module()
    variable, secret = "XDOG_UI_SECRET", "UI-PRIVATE-CLIENT-SECRET-" + "x" * 48
    monkeypatch.setenv(variable, secret)
    module.PROFILE = {**module.PROFILE, "token_auth_method": method, "client_secret_env": variable}
    peer = module.Peer()
    peer.method, peer.secret = method, secret
    patch_transport(monkeypatch, peer)
    ui, session, calls = create(tmp_path, module)
    try:
        ui._editor.set_text("parent draft")
        ui._handle_slash_command("mcp", "authorize peer")
        await wait(ui, lambda: ui._mcp_panel is not None and ui._mcp_panel.auth is not None)
        auth = ui._mcp_panel.auth
        assert auth.review["token_auth_method"] == method and auth.review["client_secret_env"] == variable
        assert secret not in json.dumps(auth.review)
        review_all(auth)
        auth.handle_input(KeyEvent("enter"))
        await wait(ui, lambda: auth.phase == "waiting")
        peer.launch(auth.url)
        await complete(auth.url, module.ISSUER)
        await wait(ui, lambda: auth.phase == "authorized")
        assert not calls and not session.mcp.tools() and ui._editor.get_text() == "parent draft"
        assert secret not in "\n".join(ui._chat_log.render(80))
    finally:
        ui._request_exit()
        await session.mcp.aclose()
        session.dispose()


async def test_coding_refresh_is_reviewed_rotated_and_never_connects_or_replays_mcp(tmp_path, monkeypatch):
    module = peer_module()
    peer = module.Peer()
    peer.issue_refresh = True
    patch_transport(monkeypatch, peer)
    ui, session, calls = create(tmp_path, module)
    try:
        ui._editor.set_text("parent draft")
        ui._handle_slash_command("mcp", "authorize peer")
        await wait(ui, lambda: ui._mcp_panel is not None and ui._mcp_panel.auth is not None)
        auth = ui._mcp_panel.auth
        review_all(auth)
        auth.handle_input(KeyEvent("enter"))
        await wait(ui, lambda: auth.phase == "waiting")
        peer.launch(auth.url)
        await complete(auth.url, module.ISSUER)
        await wait(ui, lambda: auth.phase == "authorized")
        auth.handle_input(KeyEvent("r"))
        await wait(ui, lambda: ui._mcp_panel.auth.phase == "refresh")
        refresh = ui._mcp_panel.auth
        refresh.handle_input(KeyEvent("enter"))
        assert not peer.refresh_posts
        review_all(refresh)
        refresh.handle_input(KeyEvent("enter"))
        await wait(ui, lambda: refresh.phase in {"authorized", "failed"})
        assert refresh.phase == "authorized", refresh.message
        assert len(peer.refresh_posts) == 1 and session.mcp._oauth["peer"]._refresh == "FIXTURE-REFRESH-TWO-SECRET"
        assert not calls and not session.mcp.tools() and ui._editor.get_text() == "parent draft"
        assert "FIXTURE-REFRESH" not in "\n".join(ui._chat_log.render(80))
    finally:
        ui._request_exit()
        await session.mcp.aclose()
        session.dispose()


async def test_coding_explicit_saved_credentials_review_restart_restore_and_forget_no_model_or_network(
        tmp_path, monkeypatch):
    import time

    from xdog.agent.mcp.oauth import OAuthReview
    module = peer_module()
    peer = module.Peer()
    patch_transport(monkeypatch, peer)
    monkeypatch.setenv('CODING_DIR', str(tmp_path / 'private-data'))
    ui, session, calls = create(tmp_path, module)
    fresh_ui = fresh = None
    try:
        manager = session.mcp
        value = OAuthOwner(module.RESOURCE, manager.configs['peer'].oauth)
        value._token, value._refresh = 'SAVED-PRIVATE-ACCESS', 'SAVED-PRIVATE-REFRESH'
        value._expires = time.monotonic() + 900
        value._grant = OAuthReview('a' * 32, module.PROFILE['issuer'], module.RESOURCE,
            module.PROFILE['client_id'], tuple(module.PROFILE['scopes']),
            'https://issuer.test/authorize', 'https://issuer.test/token')
        manager._oauth['peer'] = value
        ui._editor.set_text('parent draft')
        ui._open_mcp()
        await wait(ui, lambda: not ui._mcp_panel._busy)
        ui._mcp_panel.handle_input(KeyEvent('s'))
        await wait(ui, lambda: ui._mcp_panel.auth is not None)
        ui._mcp_panel.auth.handle_input(KeyEvent('p'))
        await wait(ui, lambda: ui._mcp_panel.auth.phase == 'credentials_save')
        auth = ui._mcp_panel.auth
        assert auth.phase == 'credentials_save'
        auth.handle_input(KeyEvent('enter'))
        assert not (tmp_path / 'private-data/mcp-credentials').exists()
        review_all(auth)
        auth.handle_input(KeyEvent('enter'))
        await wait(ui, lambda: auth.phase in {'authorized', 'failed'})
        assert auth.phase == 'authorized', auth.message
        assert len(list((tmp_path / 'private-data/mcp-credentials').glob('*.json'))) == 1
        ui._request_exit()
        await session.mcp.aclose()
        (tmp_path / 'restart').mkdir()
        fresh_ui, fresh, more_calls = create(tmp_path / 'restart', module)
        assert not fresh.mcp._oauth
        fresh_ui._open_mcp()
        await wait(fresh_ui, lambda: not fresh_ui._mcp_panel._busy)
        fresh_ui._mcp_panel.handle_input(KeyEvent('l'))
        await wait(fresh_ui, lambda: fresh_ui._mcp_panel.auth is not None)
        restored = fresh_ui._mcp_panel.auth
        assert restored.phase == 'credentials_restore' and fresh.mcp._oauth['peer'].credential() is None
        review_all(restored)
        restored.handle_input(KeyEvent('enter'))
        await wait(fresh_ui, lambda: restored.phase == 'authorized')
        assert fresh.mcp._oauth['peer'].credential() == 'SAVED-PRIVATE-ACCESS' and not fresh.mcp._clients
        assert not calls and not more_calls and not peer.token_posts
        fresh_ui._mcp_send('auth_credentials_forget_prepare', 'peer', '', {})
        await wait(fresh_ui, lambda: fresh_ui._mcp_panel.auth.phase == 'credentials_forget')
        erase = fresh_ui._mcp_panel.auth
        review_all(erase)
        erase.handle_input(KeyEvent('enter'))
        await wait(fresh_ui, lambda: erase.phase == 'authorized')
        assert not list((tmp_path / 'private-data/mcp-credentials').glob('*.json'))
    finally:
        if fresh_ui is not None:
            fresh_ui._request_exit()
            await fresh.mcp.aclose()
        ui._request_exit()
        await session.mcp.aclose()


@pytest.mark.skipif(__import__('os').name != 'posix' or not __import__('shutil').which('openssl'),
                    reason='owned POSIX/OpenSSL signer')
async def test_coding_private_key_jwt_review_exchange_refresh_and_no_key_publication(tmp_path, monkeypatch):
    import re
    import subprocess
    module = peer_module()
    pem = subprocess.run(['openssl', 'genpkey', '-algorithm', 'RSA', '-pkeyopt', 'rsa_keygen_bits:2048'],
                         capture_output=True, check=True).stdout
    public = subprocess.run(['openssl', 'pkey', '-text_pub', '-noout'], input=pem,
                            capture_output=True, check=True).stdout.decode()
    modulus = int(re.search(r'Modulus:\s*([0-9a-f:\s]+)Exponent:', public)[1].replace(':', '')
                  .replace('\n', '').replace(' ', ''), 16)
    exponent = int(re.search(r'Exponent: ([0-9]+)', public)[1])
    variable = 'XDOG_UI_RSA_KEY'
    monkeypatch.setenv(variable, pem.decode())
    module.PROFILE = {**module.PROFILE, 'token_auth_method': 'private_key_jwt', 'client_private_key_env': variable}
    peer = module.Peer()
    peer.method, peer.public_rsa, peer.issue_refresh = 'private_key_jwt', (modulus, exponent), True
    patch_transport(monkeypatch, peer)
    ui, session, calls = create(tmp_path, module)
    try:
        ui._editor.set_text('private key parent draft')
        ui._handle_slash_command('mcp', 'authorize peer')
        await wait(ui, lambda: ui._mcp_panel is not None and ui._mcp_panel.auth is not None)
        auth = ui._mcp_panel.auth
        assert auth.review['token_auth_method'] == 'private_key_jwt'
        auth.handle_input(KeyEvent('enter'))
        assert not peer.token_posts
        painted = []
        for _ in range(100):
            painted.extend(auth.render(80))
            if auth._visible:
                break
            auth.handle_input(KeyEvent('pagedown'))
        assert auth._visible and 'RS256' in '\n'.join(painted) and variable in '\n'.join(painted)
        from xdog.tui.components.text import Text
        protected = ui._tui.show_overlay(Text('Host-owned protected review'))
        assert not auth.focused and not auth._seen
        for _ in range(30):
            auth.render(80)
            auth.handle_input(KeyEvent('pagedown'))
        auth.handle_input(KeyEvent('enter'))
        assert not peer.token_posts and not auth._visible and not auth._seen
        protected.hide()
        assert auth.focused
        review_all(auth)  # Fresh entire review, not inherited hidden/background rows.
        auth.handle_input(KeyEvent('enter'))
        await wait(ui, lambda: auth.phase == 'waiting')
        peer.launch(auth.url)
        await complete(auth.url, module.ISSUER)
        await wait(ui, lambda: auth.phase == 'authorized')
        assert peer.token_posts == 1 and len(peer.assertions) == 1
        auth.handle_input(KeyEvent('r'))
        await wait(ui, lambda: ui._mcp_panel.auth.phase == 'refresh')
        refresh = ui._mcp_panel.auth
        review_all(refresh)
        refresh.handle_input(KeyEvent('enter'))
        await wait(ui, lambda: refresh.phase == 'authorized')
        assert len(peer.refresh_posts) == 1 and len(peer.assertions) == 2 and peer.assertions[0] != peer.assertions[1]
        public = json.dumps(session.mcp._oauth['peer'].status()) + '\n'.join(ui._chat_log.render(200))
        assert pem.decode() not in public and all(assertion not in public for assertion in peer.assertions)
        assert not calls and not session.mcp.tools() and ui._editor.get_text() == 'private key parent draft'
    finally:
        ui._request_exit()
        await session.mcp.aclose()
        session.dispose()
