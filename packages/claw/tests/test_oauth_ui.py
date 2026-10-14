from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest
from xdog.agent.mcp.oauth import OAuthOwner
from xdog.ai.types import AssistantMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer
from xdog.tui.keys import KeyEvent

FIXTURE = Path(__file__).parents[2] / "agent/tests/fixtures/oauth_ui_peer.py"


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


async def complete(url, issuer):
    params = parse_qs(urlsplit(url).query)
    redirect = urlsplit(params["redirect_uri"][0])
    reader, writer = await asyncio.open_connection(redirect.hostname, redirect.port)
    target = "/callback?" + urlencode({"code": "fixture-code", "state": params["state"][0], "iss": issuer})
    writer.write(("GET " + target + " HTTP/1.1\r\nHost: localhost\r\n\r\n").encode())
    await writer.drain()
    assert b"200 OK" in await reader.read()
    writer.close()
    await writer.wait_closed()


@pytest.mark.parametrize("method", ["client_secret_basic", "client_secret_jwt"])
async def test_claw_confidential_method_review_and_pinned_basic_exchange(tmp_path, monkeypatch, method):
    module, peer = setup_peer(monkeypatch)
    variable, secret = "XDOG_UI_SECRET", "UI-PRIVATE-CLIENT-SECRET-" + "x" * 48
    monkeypatch.setenv(variable, secret)
    peer.method, peer.secret = method, secret
    profile = {**module.PROFILE, "token_auth_method": peer.method, "client_secret_env": variable}
    config = ClawConfig(model="test/model", data_dir=str(tmp_path / "data"),
        socket_path=str(tmp_path / "secret.sock"), pid_file=str(tmp_path / "secret.pid"),
        groups=(GroupDef(id="main", is_main=True),),
        mcp_servers={"peer": {"transport": "http", "url": module.RESOURCE, "oauth": profile}})
    server = GatewayServer(config)
    with patch("xdog.claw.core.runtime.gateway._build_model_and_options",
               return_value=("test/model", lambda *args: EventStream.empty(AssistantMessage()))):
        await server.start()
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    view = "b" * 32
    async def request(action, receipt="", reviewed=False):
        body = {"type": "mcp_auth_control", "request_id": action + str(reviewed), "group_id": "main", "session_id": "",
                "lease_id": view, "server": "peer", "action": action, "review_id": receipt}
        if reviewed:
            body.update(token_auth_method=peer.method, client_secret_env=variable)
        writer.write((json.dumps(body) + "\n").encode())
        await writer.drain()
        return json.loads(await asyncio.wait_for(reader.readline(), 5))
    try:
        review = await request("prepare")
        assert review["success"] and secret not in json.dumps(review)
        assert review["data"]["token_auth_method"] == method
        proof = review["data"]["review_id"]
        rejected = await request("start", proof)
        assert not rejected["success"] and not peer.token_posts
        started = await request("start", proof, reviewed=True)
        assert started["success"] and secret not in json.dumps(started)
        peer.launch(started["data"]["authorization_url"])
        await complete(started["data"]["authorization_url"], module.ISSUER)
        async with asyncio.timeout(5):
            while (await request("status"))["data"]["state"] == "waiting":
                await asyncio.sleep(0.003)
        assert peer.token_posts == 1 and not server._orchestrator._runtimes["main"].mcp.tools()
    finally:
        writer.close()
        await writer.wait_closed()
        await server.stop()
