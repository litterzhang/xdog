from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
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
