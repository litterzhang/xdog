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
from xdog.claw.channels.tui.tui_app import ChatApp
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


async def test_claw_auth_consent_owns_connection_and_keeps_reader_responsive(tmp_path, monkeypatch):
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
    ui = ChatApp(config.socket_path)
    ui._io_generation = 1
    ui._handle_response({"type": "_connection_opened", "generation": 1})
    reader, writer = await asyncio.open_unix_connection(config.socket_path, limit=4 * 1024 * 1024)
    other_reader, other_writer = await asyncio.open_unix_connection(config.socket_path)
    task = asyncio.create_task(ui._run_connection(reader, writer))
    async def wait(predicate):
        async with asyncio.timeout(8):
            while True:
                ui._poll()
                if predicate():
                    return
                await asyncio.sleep(0.004)
    try:
        await wait(lambda: ui._connection_ready and ui._mcp_auth_supported)
        ui._editor.set_text("parent draft")
        ui._handle_submit("/mcp authorize peer")
        await wait(lambda: ui._mcp_panel is not None and ui._mcp_panel.auth is not None)
        auth = ui._mcp_panel.auth
        ui._queue_request({'type': 'ping', 'group_id': 'main', 'mcp_private_key_jwt': 0})
        await wait(lambda: not ui._mcp_private_jwt_supported)
        assert not auth._disabled and auth.phase == 'review'  # Unrelated public consent remains usable.
        proof = auth.review["review_id"]
        lease = ui._mcp_auth_lease
        assert not peer.token_posts and not calls
        forged = {"type": "mcp_auth_control", "request_id": "foreign", "group_id": "main", "session_id": "",
                  "lease_id": lease, "server": "peer", "action": "start", "review_id": proof}
        other_writer.write((json.dumps(forged) + "\n").encode())
        await other_writer.drain()
        rejected = json.loads(await asyncio.wait_for(other_reader.readline(), 3))
        assert not rejected["success"]
        other_writer.write(b'{"type":"ping","group_id":"main"}\n')
        await other_writer.drain()
        assert json.loads(await asyncio.wait_for(other_reader.readline(), 3))["type"] == "pong"
        review_all(auth)
        auth.handle_input(KeyEvent("enter"))
        await wait(lambda: auth.phase in {"waiting", "failed"})
        assert auth.phase == "waiting", auth.message
        peer.launch(auth.url)
        await complete(auth.url, module.ISSUER)
        await wait(lambda: auth.phase == "authorized")
        runtime = server._orchestrator._runtimes["main"]
        assert not runtime.mcp.tools() and not calls
        auth.handle_input(KeyEvent("escape"))
        await wait(lambda: not ui._mcp_panel._busy)
        ui._mcp_panel.handle_input(KeyEvent("c"))
        await wait(lambda: not ui._mcp_panel._busy)
        assert len(runtime.mcp.tools()) == 1 and not runtime.mcp.allowed(runtime.mcp.tools()[0].name)
        ui._mcp_panel.handle_input(KeyEvent("s"))
        await wait(lambda: ui._mcp_panel.auth is not None and ui._mcp_panel.auth.phase == "authorized")
        ui._mcp_panel.auth.handle_input(KeyEvent("f"))
        review_all(ui._mcp_panel.auth)
        ui._mcp_panel.auth.handle_input(KeyEvent("enter"))
        await wait(lambda: ui._mcp_panel.auth.phase == "forgotten")
        assert not runtime.mcp.tools()
        assert "FIXTURE-OAUTH-SECRET" not in "\n".join(ui._chat_log.render(80))
        ui._close_mcp()
        assert ui._editor.get_text() == "parent draft" and ui._tui._focused is ui._editor
    finally:
        ui._request_exit()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        writer.close()
        other_writer.close()
        await writer.wait_closed()
        await other_writer.wait_closed()
        await server.stop()


async def test_claw_auth_disconnect_cancels_listener_and_reconnect_cannot_adopt_receipt(tmp_path, monkeypatch):
    module, peer = setup_peer(monkeypatch)
    config = ClawConfig(model="test/model", data_dir=str(tmp_path / "data"),
        socket_path=str(tmp_path / "oauth.sock"), pid_file=str(tmp_path / "oauth.pid"),
        groups=(GroupDef(id="main", is_main=True),),
        mcp_servers={"peer": {"transport": "http", "url": module.RESOURCE, "oauth": module.PROFILE}})
    server = GatewayServer(config)
    with patch("xdog.claw.core.runtime.gateway._build_model_and_options",
               return_value=("test/model", lambda *args: EventStream.empty(AssistantMessage()))):
        await server.start()
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    view = "a" * 32
    def request(operation, receipt=""):
        return {"type": "mcp_auth_control", "request_id": operation, "group_id": "main", "session_id": "",
                "lease_id": view, "server": "peer", "action": operation, "review_id": receipt}
    async def send(operation, receipt=""):
        writer.write((json.dumps(request(operation, receipt)) + "\n").encode())
        await writer.drain()
        return json.loads(await asyncio.wait_for(reader.readline(), 5))
    try:
        review = await send("prepare")
        assert review["success"]
        proof = review["data"]["review_id"]
        started = await send("start", proof)
        assert started["success"]
        redirect = urlsplit(parse_qs(urlsplit(started["data"]["authorization_url"]).query)["redirect_uri"][0])
        runtime = server._orchestrator._runtimes["main"]
        owner = runtime.mcp._oauth["peer"]
        writer.close()
        await writer.wait_closed()
        async with asyncio.timeout(5):
            while owner.status()["state"] == "waiting":
                await asyncio.sleep(0.003)
        assert owner.credential() is None and not peer.token_posts
        assert not owner.flow.listener.is_serving() and runtime.get_new_session_id() is None
        reader, writer = await asyncio.open_unix_connection(config.socket_path)
        rejected = await send("start", proof)
        assert not rejected["success"]
        try:
            _reader, unexpected = await asyncio.open_connection(redirect.hostname, redirect.port)
        except OSError:
            pass
        else:
            unexpected.close()
            raise AssertionError("Disconnected consent listener remained alive")
    finally:
        writer.close()
        await writer.wait_closed()
        await server.stop()


async def test_auth_detach_is_not_refused_when_metadata_job_capacity_is_full(tmp_path, monkeypatch):
    import threading
    module, peer = setup_peer(monkeypatch)
    entered, release = [], threading.Event()
    original = peer.handle
    async def delayed(request):
        if request.url.path.startswith("/.well-known/oauth-protected-resource"):
            entered.append(True)
            while not release.is_set():
                await asyncio.sleep(0.003)
        return await original(request)
    peer.transport = httpx.MockTransport(delayed)
    config = ClawConfig(model="test/model", data_dir=str(tmp_path / "data"),
        socket_path=str(tmp_path / "quota.sock"), pid_file=str(tmp_path / "quota.pid"),
        groups=tuple(GroupDef(id=f"g{index}", is_main=index == 0) for index in range(8)),
        mcp_servers={"peer": {"transport": "http", "url": module.RESOURCE, "oauth": module.PROFILE}})
    server = GatewayServer(config)
    with patch("xdog.claw.core.runtime.gateway._build_model_and_options",
               return_value=("test/model", lambda *args: EventStream.empty(AssistantMessage()))):
        await server.start()
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    try:
        for index in range(8):
            writer.write((json.dumps({"type": "mcp_auth_control", "request_id": f"prepare{index}",
                "group_id": f"g{index}", "session_id": "", "lease_id": f"{index:032x}",
                "server": "peer", "action": "prepare"}) + "\n").encode())
        await writer.drain()
        async with asyncio.timeout(8):
            while len(entered) != 8:
                await asyncio.sleep(0.003)
        writer.write((json.dumps({"type": "mcp_auth_control", "request_id": "detach", "group_id": "g0",
                                 "session_id": "", "lease_id": "0" * 32, "action": "detach"}) + "\n").encode())
        await writer.drain()
        async with asyncio.timeout(5):
            while True:
                reply = json.loads(await reader.readline())
                if reply.get("request_id") == "detach":
                    break
        assert reply["success"] and reply["data"]["state"] == "detached"
        assert not server._orchestrator._runtimes["g0"].mcp._auth_leases
        assert not peer.token_posts
    finally:
        release.set()
        writer.close()
        await writer.wait_closed()
        await server.stop()


async def test_claw_dynamic_registration_is_explicit_and_issued_client_is_not_a_tool_grant(tmp_path, monkeypatch):
    module, peer = setup_peer(monkeypatch)
    profile = {"issuer": module.ISSUER, "scopes": ["tools:read"], "registration": "dynamic"}
    config = ClawConfig(model="test/model", data_dir=str(tmp_path / "data"),
        socket_path=str(tmp_path / "registration.sock"), pid_file=str(tmp_path / "registration.pid"),
        groups=(GroupDef(id="main", is_main=True),),
        mcp_servers={"peer": {"transport": "http", "url": module.RESOURCE, "oauth": profile}})
    server = GatewayServer(config)
    with patch("xdog.claw.core.runtime.gateway._build_model_and_options",
               return_value=("test/model", lambda *args: EventStream.empty(AssistantMessage()))):
        await server.start()
    ui = ChatApp(config.socket_path)
    ui._io_generation = 1
    ui._handle_response({"type": "_connection_opened", "generation": 1})
    reader, writer = await asyncio.open_unix_connection(config.socket_path, limit=4 * 1024 * 1024)
    task = asyncio.create_task(ui._run_connection(reader, writer))
    async def wait(predicate):
        async with asyncio.timeout(8):
            while True:
                ui._poll()
                if predicate():
                    return
                await asyncio.sleep(0.004)
    try:
        await wait(lambda: ui._connection_ready and ui._mcp_registration_supported)
        ui._handle_submit("/mcp authorize peer")
        await wait(lambda: ui._mcp_panel is not None and ui._mcp_panel.auth is not None)
        auth = ui._mcp_panel.auth
        assert auth.review["client_id"] == "" and not peer.registration_posts
        review_all(auth)
        auth.handle_input(KeyEvent("enter"))
        await wait(lambda: auth.phase in {"waiting", "failed"})
        assert auth.phase == "waiting", auth.message
        assert len(peer.registration_posts) == 1
        peer.launch(auth.url)
        await complete(auth.url, module.ISSUER)
        await wait(lambda: auth.phase == "authorized")
        manager = server._orchestrator._runtimes["main"].mcp
        assert not manager.tools() and manager.configs["peer"].allow_tools is False
        assert "IGNORED-REGISTRATION-SECRET" not in "\n".join(ui._chat_log.render(80))
    finally:
        ui._request_exit()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        writer.close()
        await writer.wait_closed()
        await server.stop()


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


async def test_claw_refresh_keeps_connection_lease_review_and_token_rotation_separate_from_tools(tmp_path, monkeypatch):
    module, peer = setup_peer(monkeypatch)
    peer.issue_refresh = True
    config = ClawConfig(model="test/model", data_dir=str(tmp_path / "data"),
        socket_path=str(tmp_path / "refresh.sock"), pid_file=str(tmp_path / "refresh.pid"),
        groups=(GroupDef(id="main", is_main=True),),
        mcp_servers={"peer": {"transport": "http", "url": module.RESOURCE, "oauth": module.PROFILE}})
    server = GatewayServer(config)
    with patch("xdog.claw.core.runtime.gateway._build_model_and_options",
               return_value=("test/model", lambda *args: EventStream.empty(AssistantMessage()))):
        await server.start()
    ui = ChatApp(config.socket_path)
    ui._io_generation = 1
    ui._handle_response({"type": "_connection_opened", "generation": 1})
    reader, writer = await asyncio.open_unix_connection(config.socket_path, limit=4 * 1024 * 1024)
    task = asyncio.create_task(ui._run_connection(reader, writer))
    async def wait(predicate):
        async with asyncio.timeout(8):
            while True:
                ui._poll()
                if predicate():
                    return
                await asyncio.sleep(0.004)
    try:
        await wait(lambda: ui._connection_ready and ui._mcp_refresh_supported)
        ui._editor.set_text("parent draft")
        ui._handle_submit("/mcp authorize peer")
        await wait(lambda: ui._mcp_panel is not None and ui._mcp_panel.auth is not None)
        auth = ui._mcp_panel.auth
        review_all(auth)
        auth.handle_input(KeyEvent("enter"))
        await wait(lambda: auth.phase == "waiting")
        peer.launch(auth.url)
        await complete(auth.url, module.ISSUER)
        await wait(lambda: auth.phase == "authorized")
        auth.handle_input(KeyEvent("r"))
        await wait(lambda: ui._mcp_panel.auth.phase == "refresh")
        refresh = ui._mcp_panel.auth
        assert not peer.refresh_posts
        review_all(refresh)
        refresh.handle_input(KeyEvent("enter"))
        await wait(lambda: refresh.phase in {"authorized", "failed"})
        assert refresh.phase == "authorized", refresh.message
        manager = server._orchestrator._runtimes["main"].mcp
        assert len(peer.refresh_posts) == 1 and manager._oauth["peer"]._refresh == "FIXTURE-REFRESH-TWO-SECRET"
        assert not manager.tools() and manager.configs["peer"].allow_tools is False
        assert ui._editor.get_text() == "parent draft" and "FIXTURE-REFRESH" not in "\n".join(ui._chat_log.render(80))
    finally:
        ui._request_exit()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        writer.close()
        await writer.wait_closed()
        await server.stop()


async def test_claw_saved_credentials_lease_review_restart_memory_restore_without_model_or_token_post(
        tmp_path, monkeypatch):
    import time

    from xdog.agent.mcp.oauth import OAuthReview
    module, peer = setup_peer(monkeypatch)
    calls = []
    def stream(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())
    config = ClawConfig(model='test/model', data_dir=str(tmp_path / 'data'),
        socket_path=str(tmp_path / 'save.sock'), pid_file=str(tmp_path / 'save.pid'),
        groups=(GroupDef(id='main', is_main=True),), mcp_servers={'peer': {
            'transport': 'http', 'url': module.RESOURCE, 'oauth': module.PROFILE}})
    async def start_server():
        result = GatewayServer(config)
        with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=('test/model', stream)):
            await result.start()
        return result
    server = await start_server()
    ui = ChatApp(config.socket_path)
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    task = asyncio.create_task(ui._run_connection(reader, writer))
    async def wait(predicate):
        async with asyncio.timeout(8):
            while not predicate():
                ui._poll()
                await asyncio.sleep(0.003)
    try:
        await wait(lambda: ui._connection_ready)
        assert ui._mcp_credentials_supported
        manager = server._orchestrator._runtimes['main'].mcp
        value = OAuthOwner(module.RESOURCE, manager.configs['peer'].oauth)
        value._token, value._refresh = 'SAVED-PRIVATE-ACCESS', 'SAVED-PRIVATE-REFRESH'
        value._expires = time.monotonic() + 900
        value._grant = OAuthReview('a' * 32, module.PROFILE['issuer'], module.RESOURCE,
            module.PROFILE['client_id'], tuple(module.PROFILE['scopes']),
            'https://issuer.test/authorize', 'https://issuer.test/token')
        manager._oauth['peer'] = value
        ui._open_mcp()
        await wait(lambda: not ui._mcp_panel._busy)
        ui._mcp_panel.handle_input(KeyEvent('s'))
        await wait(lambda: ui._mcp_panel.auth is not None)
        ui._mcp_panel.auth.handle_input(KeyEvent('p'))
        await wait(lambda: ui._mcp_panel.auth.phase == 'credentials_save')
        auth = ui._mcp_panel.auth
        assert auth.phase == 'credentials_save'
        review_all(auth)
        auth.handle_input(KeyEvent('enter'))
        await wait(lambda: auth.phase in {'authorized', 'failed'})
        assert auth.phase == 'authorized', auth.message
        directory = tmp_path / 'data/groups/main/mcp-credentials'
        assert len(list(directory.glob('*.json'))) == 1
        ui._request_exit()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        writer.close()
        await writer.wait_closed()
        await server.stop()
        server = await start_server()
        ui = ChatApp(config.socket_path)
        reader, writer = await asyncio.open_unix_connection(config.socket_path)
        task = asyncio.create_task(ui._run_connection(reader, writer))
        await wait(lambda: ui._connection_ready)
        manager = server._orchestrator._runtimes['main'].mcp
        assert not manager._oauth
        ui._editor.set_text('fresh parent draft')
        ui._open_mcp()
        await wait(lambda: not ui._mcp_panel._busy)
        ui._mcp_panel.handle_input(KeyEvent('l'))
        await wait(lambda: ui._mcp_panel.auth is not None)
        auth = ui._mcp_panel.auth
        assert auth.phase == 'credentials_restore'
        review_all(auth)
        auth.handle_input(KeyEvent('enter'))
        await wait(lambda: auth.phase == 'authorized')
        assert manager._oauth['peer'].credential() == 'SAVED-PRIVATE-ACCESS' and not manager._clients
        assert not calls and not peer.token_posts
        ui._close_mcp()
        assert ui._editor.get_text() == 'fresh parent draft'
    finally:
        ui._request_exit()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        writer.close()
        await writer.wait_closed()
        await server.stop()


@pytest.mark.skipif(__import__('os').name != 'posix' or not __import__('shutil').which('openssl'),
                    reason='owned POSIX/OpenSSL signer')
async def test_claw_private_key_jwt_live_consent_exchange_refresh_and_withdrawal(tmp_path, monkeypatch):
    import re
    import subprocess
    module, peer = setup_peer(monkeypatch)
    pem = subprocess.run(['openssl', 'genpkey', '-algorithm', 'RSA', '-pkeyopt', 'rsa_keygen_bits:2048'],
                         capture_output=True, check=True).stdout
    public = subprocess.run(['openssl', 'pkey', '-text_pub', '-noout'], input=pem,
                            capture_output=True, check=True).stdout.decode()
    modulus = int(re.search(r'Modulus:\s*([0-9a-f:\s]+)Exponent:', public)[1].replace(':', '')
                  .replace('\n', '').replace(' ', ''), 16)
    exponent = int(re.search(r'Exponent: ([0-9]+)', public)[1])
    variable = 'XDOG_UI_RSA_KEY'
    monkeypatch.setenv(variable, pem.decode())
    profile = {**module.PROFILE, 'token_auth_method': 'private_key_jwt', 'client_private_key_env': variable}
    peer.method, peer.public_rsa, peer.issue_refresh = 'private_key_jwt', (modulus, exponent), True
    calls = []
    def stream(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())
    config = ClawConfig(model='test/model', data_dir=str(tmp_path / 'data'),
        socket_path=str(tmp_path / 'rsa.sock'), pid_file=str(tmp_path / 'rsa.pid'),
        groups=(GroupDef(id='main', is_main=True),), mcp_servers={'peer': {
            'transport': 'http', 'url': module.RESOURCE, 'oauth': profile}})
    server = GatewayServer(config)
    with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=('test/model', stream)):
        await server.start()
    ui = ChatApp(config.socket_path)
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    task = asyncio.create_task(ui._run_connection(reader, writer))
    async def wait(predicate):
        async with asyncio.timeout(8):
            while not predicate():
                ui._poll()
                await asyncio.sleep(0.003)
    try:
        await wait(lambda: ui._connection_ready)
        assert ui._mcp_private_jwt_supported
        ui._editor.set_text('RSA parent draft')
        ui._open_mcp()
        await wait(lambda: ui._mcp_panel is not None and not ui._mcp_panel._busy)
        ui._mcp_panel.authorize_server('peer')
        await wait(lambda: ui._mcp_panel.auth is not None)
        auth = ui._mcp_panel.auth
        assert auth.review['token_auth_method'] == 'private_key_jwt' and auth.review['client_secret_env'] == variable
        review_all(auth)
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
        review_all(auth)
        auth.handle_input(KeyEvent('enter'))
        await wait(lambda: auth.phase == 'waiting')
        peer.launch(auth.url)
        await complete(auth.url, module.ISSUER)
        auth.handle_input(KeyEvent('s'))
        await wait(lambda: auth.phase == 'authorized')
        assert peer.token_posts == 1 and len(peer.assertions) == 1
        auth.handle_input(KeyEvent('r'))
        await wait(lambda: ui._mcp_panel.auth.phase == 'refresh')
        refresh = ui._mcp_panel.auth
        review_all(refresh)
        refresh.handle_input(KeyEvent('enter'))
        await wait(lambda: refresh.phase == 'authorized')
        assert len(peer.refresh_posts) == 1 and len(peer.assertions) == 2 and peer.assertions[0] != peer.assertions[1]
        manager = server._orchestrator._runtimes['main'].mcp
        assert not calls and not manager.tools() and ui._editor.get_text() == 'RSA parent draft'
        assert pem.decode() not in json.dumps(manager._oauth['peer'].status())
        # Negotiation withdrawal revokes only this private authentication view's
        # operation lease, before any later start/refresh can perform a POST.
        writer.write(b'{"type":"ping","mcp_private_key_jwt":0}\n')
        await writer.drain()
        await wait(lambda: not ui._mcp_private_jwt_supported and refresh._disabled)
        assert refresh.phase == 'canceled' and not refresh.url and ui._mcp_auth_review is None
        assert 'not remotely revoked' in refresh.message
        async with asyncio.timeout(5):
            while manager._auth_leases:
                await asyncio.sleep(0.003)
        assert manager._oauth['peer'].credential() is not None  # A completed token is not a signing lease.
        before = peer.token_posts + len(peer.refresh_posts)
        for key in ('enter', 'r', 'p', 's', 'y'):
            refresh.handle_input(KeyEvent(key))
        refresh.publish('auth_status', {'state': 'authorized'})
        assert refresh.phase == 'canceled'

        ui._mcp_send('auth_refresh_prepare', 'peer', '', {})
        await wait(lambda: not ui._mcp_panel._busy)
        assert before == peer.token_posts + len(peer.refresh_posts)
    finally:
        ui._request_exit()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        writer.close()
        await writer.wait_closed()
        await server.stop()
