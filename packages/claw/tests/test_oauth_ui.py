"""Native MCP OAuth is public PKCE only, with owned review/callback/credential consent."""
from __future__ import annotations

import asyncio
import importlib.util
import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest
from xdog.agent.mcp.oauth import OAuthError, OAuthOwner
from xdog.ai.types import AssistantMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer
from xdog.tui.keys import KeyEvent

FIXTURE = Path(__file__).parents[2] / "agent/tests/fixtures/oauth_ui_peer.py"
RETIRED = [
    {"registration": "metadata"}, {"registration": "dynamic", "client_id": ""},
    {"token_auth_method": "client_secret_basic", "client_secret_env": "XDOG_UI_SECRET"},
    {"token_auth_method": "client_secret_post", "client_secret_env": "XDOG_UI_SECRET"},
    {"token_auth_method": "client_secret_jwt", "client_secret_env": "XDOG_UI_SECRET"},
    {"token_auth_method": "private_key_jwt", "client_private_key_env": "XDOG_UI_PRIVATE_KEY"},
]


def setup_peer(monkeypatch):
    spec = importlib.util.spec_from_file_location("oauth_ui_peer", FIXTURE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    peer = module.Peer()
    monkeypatch.setattr(OAuthOwner, "client", lambda self: httpx.AsyncClient(transport=peer.transport))
    import xdog.agent.mcp.http as transport
    original = httpx.AsyncClient
    monkeypatch.setattr(transport, "httpx", SimpleNamespace(
        AsyncClient=lambda **kwargs: original(transport=peer.transport, **kwargs),
        Limits=httpx.Limits, HTTPError=httpx.HTTPError, Headers=httpx.Headers))
    return module, peer


def review_all(panel):
    for _ in range(100):
        panel.render(80)
        if panel._visible:
            return
        panel.handle_input(KeyEvent("pagedown"))
    raise AssertionError("OAuth consent did not become visible")


async def complete(url, issuer, *, state=None, expected=b"200 OK"):
    params = parse_qs(urlsplit(url).query)
    redirect = urlsplit(params["redirect_uri"][0])
    reader, writer = await asyncio.open_connection(redirect.hostname, redirect.port)
    target = "/callback?" + urlencode({"code": "fixture-code", "state": state or params["state"][0], "iss": issuer})
    writer.write(("GET " + target + " HTTP/1.1\r\nHost: localhost\r\n\r\n").encode())
    await writer.drain()
    assert expected in await reader.read()
    writer.close()
    await writer.wait_closed()


@asynccontextmanager
async def oauth_server(tmp_path, monkeypatch):
    module, peer = setup_peer(monkeypatch)
    calls = []

    def stream(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())

    config = ClawConfig(model="test/model", data_dir=str(tmp_path / "data"),
        socket_path=str(tmp_path / "oauth.sock"), pid_file=str(tmp_path / "oauth.pid"),
        groups=(GroupDef(id="main", is_main=True),),
        mcp_servers={"peer": {"transport": "http", "url": module.RESOURCE, "oauth": module.PROFILE}})
    server = GatewayServer(config)
    with patch("xdog.claw.core.runtime.gateway._build_model_and_options", return_value=("test/model", stream)):
        await server.start()
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    sequence = 0

    async def send(body):
        nonlocal sequence
        sequence += 1
        body = {"type": "mcp_auth_control", "request_id": "auth-" + str(sequence), "group_id": "main",
                "session_id": "", "lease_id": "b" * 32, "server": "peer", **body}
        writer.write((json.dumps(body) + "\n").encode())
        await writer.drain()
        async with asyncio.timeout(5):
            return json.loads(await reader.readline())

    try:
        yield module, peer, server, send, calls
    finally:
        writer.close()
        await writer.wait_closed()
        await asyncio.wait_for(server.stop(), 10)


@pytest.mark.parametrize("changes", RETIRED)
def test_claw_retired_oauth_configuration_refused_before_client_or_helper_io(tmp_path, monkeypatch, changes):
    module, peer = setup_peer(monkeypatch)
    def forbidden(*args, **kwargs):
        pytest.fail("Unsupported OAuth configuration reached I/O")

    monkeypatch.setattr(OAuthOwner, "client", forbidden)
    monkeypatch.setattr("subprocess.Popen", forbidden)
    with pytest.raises(OAuthError):
        ClawConfig(mcp_servers={"peer": {"transport": "http", "url": module.RESOURCE,
                                       "oauth": {**module.PROFILE, **changes}}})
    assert not peer.requests


@pytest.mark.parametrize("fields", RETIRED)
async def test_claw_retired_oauth_packets_cannot_prepare_discover_or_start(tmp_path, monkeypatch, fields):
    async with oauth_server(tmp_path, monkeypatch) as (_, peer, server, send, calls):
        for action in ("prepare", "start", "refresh_prepare"):
            response = await send({"action": action, "review_id": "a" * 32, **fields})
            assert response["success"] is False and "public PKCE" in response["error"]
        manager = server._orchestrator._runtimes["main"].mcp
        assert not peer.requests and not manager._oauth and not manager.tools() and not calls


@pytest.mark.parametrize("cancel", [False, True])
async def test_claw_actual_public_ui_review_login_or_cancel_preserves_issuer_resource_and_no_tool_grant(
        tmp_path, monkeypatch, cancel):
    async with oauth_server(tmp_path, monkeypatch) as (module, peer, server, send, calls):
        ui = ChatApp("/unused-offline-ui-socket")
        pending = []
        monkeypatch.setattr(ui, "_queue_request", pending.append)
        ui._connection_ready = ui._mcp_supported = ui._mcp_auth_supported = True
        ui._open_mcp()
        pending.clear()  # Browser list is not authorization or a tool permission.
        ui._mcp_publish("list", ())

        async def deliver():
            assert len(pending) == 1, pending
            request = pending.pop()
            assert request["type"] == "mcp_auth_control"
            response = await send(request)
            ui._handle_response(response)
            return response

        try:
            ui._mcp_panel.authorize_server("peer")
            prepared = await deliver()
            assert prepared["success"]
            auth = ui._mcp_panel.auth
            assert auth.review["token_auth_method"] == "none" and auth.review["client_secret_env"] == ""
            auth.handle_input(KeyEvent("enter"))
            assert not pending and not peer.token_posts  # No confirmation before fields were painted.
            review_all(auth)
            auth.handle_input(KeyEvent("enter"))
            launched = await deliver()
            assert launched["success"] and auth.phase == "waiting"
            url = auth.url
            peer.launch(url)
            params = parse_qs(urlsplit(url).query)
            assert params["resource"] == [module.RESOURCE] and params["client_id"] == [module.PROFILE["client_id"]]
            assert params["code_challenge_method"] == ["S256"] and "code_verifier" not in params
            manager = server._orchestrator._runtimes["main"].mcp
            owned = manager._oauth["peer"]
            if cancel:
                ui._close_mcp()
                detached = await deliver()
                assert detached["success"]
                assert not owned.flow.listener.is_serving() and owned.credential() is None
                with pytest.raises(OSError):
                    await complete(url, module.ISSUER)
                ui._handle_response(launched)
                assert ui._mcp_panel is None and peer.token_posts == 0
            else:
                await complete(url, "https://foreign.test", expected=b"400 Bad Request")
                await complete(url, module.ISSUER, state="foreign", expected=b"400 Bad Request")
                assert peer.token_posts == 0
                await complete(url, module.ISSUER)
                async with asyncio.timeout(5):
                    while owned.status()["state"] == "waiting":
                        await asyncio.sleep(.003)
                ui._mcp_send("auth_status", "peer", "", {})
                status = await deliver()
                assert status["success"] and auth.phase == "authorized" and peer.token_posts == 1
                assert owned.credential() == "FIXTURE-OAUTH-SECRET"
                duplicate = await send({"action": "start", "review_id": prepared["data"]["review_id"],
                                        "lease_id": ui._mcp_auth_lease})
                assert not duplicate["success"] and peer.token_posts == 1
                assert "FIXTURE-OAUTH-SECRET" not in json.dumps(status) + json.dumps(prepared) + json.dumps(launched)
            assert not peer.registration_posts and not manager.tools() and not calls
        finally:
            ui._request_exit()
