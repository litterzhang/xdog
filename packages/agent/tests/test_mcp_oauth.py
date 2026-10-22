from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest
from xdog.agent.mcp.config import McpServerConfig
from xdog.agent.mcp.oauth import OAuthConfig, OAuthError, OAuthOwner
from xdog.agent.mcp.registry import McpRegistry

RESOURCE = "https://resource.test/mcp"
ISSUER = "https://issuer.test/tenant"
CONFIG = {"issuer": ISSUER, "client_id": "registered-client", "scopes": ["tools:read"]}


class Peer:
    def __init__(self):
        self.requests = []
        self.token_posts = []
        self.metadata = {"issuer": ISSUER, "authorization_endpoint": "https://issuer.test/authorize",
            "token_endpoint": "https://issuer.test/token", "code_challenge_methods_supported": ["S256"],
            "response_types_supported": ["code"], "token_endpoint_auth_methods_supported": ["none"],
            "authorization_response_iss_parameter_supported": True}
        self.resource_metadata = {"resource": RESOURCE, "authorization_servers": [ISSUER],
                                  "scopes_supported": ["tools:read"]}
        self.token = {"access_token": "RESOURCE-BOUND-SECRET", "token_type": "Bearer", "expires_in": 3600,
                      "scope": "tools:read"}
        self.challenge = ""
        self.transport = httpx.MockTransport(self.handle)

    async def handle(self, request):
        self.requests.append(request)
        if request.url.path == "/.well-known/oauth-protected-resource/mcp":
            assert "authorization" not in request.headers
            return httpx.Response(200, json=self.resource_metadata)
        if request.url.path == "/.well-known/oauth-authorization-server/tenant":
            assert "authorization" not in request.headers
            return httpx.Response(200, json=self.metadata)
        if request.url.path == "/token":
            assert "authorization" not in request.headers
            body = parse_qs(request.content.decode())
            self.token_posts.append(body)
            assert body["resource"] == [RESOURCE] and body["client_id"] == ["registered-client"]
            verifier = body["code_verifier"][0]
            expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
            assert self.challenge == expected
            return httpx.Response(200, json=self.token)
        if request.url.path == "/mcp":
            assert request.headers.get("Authorization") == "Bearer RESOURCE-BOUND-SECRET"
            message = json.loads(request.content)
            if message["method"] == "server/discover":
                result = {"supportedVersions": ["2026-07-28"], "capabilities": {"tools": {}},
                          "serverInfo": {"name": "fixture", "version": "1"}}
            else:
                result = {"tools": [{"name": "read", "description": "read", "inputSchema": {"type": "object"}}]}
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": message["id"], "result": result})
        raise AssertionError("Unexpected cross-origin endpoint: " + str(request.url))


def owner(peer):
    value = OAuthOwner(RESOURCE, OAuthConfig.parse(CONFIG))
    value.client = lambda: httpx.AsyncClient(transport=peer.transport, trust_env=False, follow_redirects=False)
    return value


async def callback(url, code="code", state=None, issuer=ISSUER):
    query = parse_qs(urlsplit(url).query)
    redirect = urlsplit(query["redirect_uri"][0])
    reader, writer = await asyncio.open_connection(redirect.hostname, redirect.port)
    target = redirect.path + "?" + urlencode({"state": state if state is not None else query["state"][0],
                                            "code": code, "iss": issuer})
    writer.write(("GET " + target + " HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n").encode())
    await writer.drain()
    response = await reader.read()
    writer.close()
    await writer.wait_closed()
    return response


async def finished(value):
    async with asyncio.timeout(5):
        while value.status()["state"] == "waiting":
            await asyncio.sleep(0.002)


async def test_actual_pkce_loopback_exchange_is_single_use_resource_bound_and_has_no_public_secrets():
    peer = Peer()
    value = owner(peer)
    try:
        review = await value.prepare()
        assert not peer.token_posts and value.credential() is None
        with pytest.raises(OAuthError, match="review changed"):
            await value.start("foreign")
        launch = await value.start(review["review_id"])
        url = launch["authorization_url"]
        params = parse_qs(urlsplit(url).query)
        peer.challenge = params["code_challenge"][0]
        assert params["resource"] == [RESOURCE] and params["scope"] == ["tools:read"]
        assert "code_verifier" not in params and params["code_challenge_method"] == ["S256"]
        assert b"400 Bad Request" in await callback(url, state="foreign")
        assert b"400 Bad Request" in await callback(url, issuer="https://foreign.test")
        assert not peer.token_posts and value.status()["state"] == "waiting"
        assert b"200 OK" in await callback(url)
        await finished(value)
        assert value.credential() == "RESOURCE-BOUND-SECRET" and len(peer.token_posts) == 1
        assert value.status()["state"] == "authorized"
        assert "RESOURCE-BOUND-SECRET" not in json.dumps(value.status()) + json.dumps(review) + json.dumps(launch)
        assert value.flow.verifier == "" and value.flow.url == "" and value.flow.code.cancelled()
        with pytest.raises(OAuthError):
            await value.start(review["review_id"])
        assert len(peer.token_posts) == 1
    finally:
        await value.close()
    assert value.credential() is None


@pytest.mark.parametrize("metadata", [
    {"issuer": "https://foreign.test"}, {"token_endpoint": "https://foreign.test/token"},
    {"authorization_endpoint": "http://issuer.test/authorize"}, {"code_challenge_methods_supported": ["plain"]},
    {"token_endpoint_auth_methods_supported": ["client_secret_basic"]}, {"response_types_supported": ["token"]},
])
async def test_untrusted_or_unsupported_metadata_fails_before_code_browser_or_token_effect(metadata):
    peer = Peer()
    peer.metadata.update(metadata)
    value = owner(peer)
    try:
        with pytest.raises(OAuthError):
            await value.prepare()
        assert value.flow is None and not peer.token_posts and value.credential() is None
    finally:
        await value.close()


@pytest.mark.parametrize("metadata", [{"resource": "https://foreign.test/mcp"}, {"authorization_servers": []},
                                     {"scopes_supported": ["tools:write"]}, {"scopes_supported": [{}]}])
async def test_wrong_resource_or_scope_discovery_never_visits_arbitrary_issuer(metadata):
    peer = Peer()
    peer.resource_metadata.update(metadata)
    value = owner(peer)
    try:
        with pytest.raises(OAuthError):
            await value.prepare()
        assert len(peer.requests) == 1 and not peer.token_posts
    finally:
        await value.close()


@pytest.mark.parametrize("token", [{"scope": "tools:read tools:write"}, {"token_type": "Basic"},
                                  {"access_token": "bad\r\nheader"}, {"expires_in": True}])
async def test_invalid_or_overprivileged_token_response_is_never_committed(token):
    peer = Peer()
    peer.token.update(token)
    value = owner(peer)
    try:
        review = await value.prepare()
        launch = await value.start(review["review_id"])
        peer.challenge = parse_qs(urlsplit(launch["authorization_url"]).query)["code_challenge"][0]
        await callback(launch["authorization_url"])
        await finished(value)
        assert value.status()["state"] == "failed" and value.credential() is None
        assert "SECRET" not in json.dumps(value.status()) and len(peer.token_posts) == 1
    finally:
        await value.close()


async def test_cancel_closes_owned_listener_without_token_post_and_start_receipt_cannot_be_replayed():
    peer = Peer()
    value = owner(peer)
    try:
        review = await value.prepare()
        launch = await value.start(review["review_id"])
        redirect = urlsplit(parse_qs(urlsplit(launch["authorization_url"]).query)["redirect_uri"][0])
        await value.cancel()
        assert value.status()["state"] == "canceled" and not peer.token_posts
        with pytest.raises(OSError):
            await asyncio.open_connection(redirect.hostname, redirect.port)
        assert value.flow.verifier == "" and value.credential() is None
    finally:
        await value.close()


@pytest.mark.parametrize("extra", [
    {"headers": {"Authorization": "Bearer static"}}, {"headers_env": {"authorization": "TOKEN"}},
    {"url": "http://remote.test/mcp", "allow_insecure_http": True}, {"url": RESOURCE + "?api_key=secret"},
])
def test_oauth_config_cannot_mix_credentials_or_expose_them_to_remote_plaintext(extra):
    with pytest.raises(ValueError):
        McpServerConfig.parse("peer", {"transport": "http", "url": RESOURCE, "oauth": CONFIG, **extra})


async def test_discovery_cookie_is_not_forwarded_to_issuer_and_redirects_are_not_followed():
    peer = Peer()
    async def handler(request):
        response = await peer.handle(request)
        assert "cookie" not in request.headers
        if request.url.path.startswith("/.well-known/oauth-protected-resource"):
            response.headers["set-cookie"] = "session=private; Domain=.test; Path=/"
        return response
    peer.transport = httpx.MockTransport(handler)
    value = owner(peer)
    try:
        await value.prepare()
        assert len(peer.requests) == 2
    finally:
        await value.close()
    requests = []
    async def redirected(request):
        requests.append(request)
        return httpx.Response(302, headers={"Location": "https://foreign.test/metadata"})
    value = OAuthOwner(RESOURCE, OAuthConfig.parse(CONFIG))
    value.client = lambda: httpx.AsyncClient(transport=httpx.MockTransport(redirected), follow_redirects=False)
    try:
        with pytest.raises(OAuthError, match="no redirects"):
            await value.prepare()
        assert len(requests) == 1
    finally:
        await value.close()


async def test_duplicate_start_and_stale_review_cannot_allocate_extra_callback_listeners():
    from dataclasses import replace
    peer = Peer()
    value = owner(peer)
    try:
        review = await value.prepare()
        value.review = replace(value.review, created=value.review.created - 301)
        with pytest.raises(OAuthError, match="fresh review"):
            await value.start(review["review_id"])
        review = await value.prepare()
        outcomes = await asyncio.gather(*(value.start(review["review_id"]) for _ in range(2)), return_exceptions=True)
        assert sum(isinstance(result, dict) for result in outcomes) == 1
        assert sum(isinstance(result, OAuthError) for result in outcomes) == 1
        await value.cancel()
        assert value.status()["state"] == "canceled" and not peer.token_posts
    finally:
        await value.close()


async def test_expired_bearer_is_not_sent_and_status_remains_expired():
    peer = Peer()
    value = owner(peer)
    try:
        review = await value.prepare()
        launch = await value.start(review["review_id"])
        peer.challenge = parse_qs(urlsplit(launch["authorization_url"]).query)["code_challenge"][0]
        await callback(launch["authorization_url"])
        await finished(value)
        value._expires -= 4000
        assert value.credential() is None
        assert value.status()["state"] == "expired" and value.status()["state"] == "expired"
        assert "SECRET" not in json.dumps(value.status())
    finally:
        await value.close()


async def test_changed_resource_definition_cannot_receive_previous_resources_bearer(tmp_path):
    from dataclasses import replace
    peer = Peer()
    value = owner(peer)
    value._token = "SECRET-OLD-RESOURCE"
    value._expires = float("inf")
    manager = McpRegistry({"peer": {"transport": "http", "url": RESOURCE, "oauth": CONFIG}}, tmp_path)
    manager._oauth["peer"] = value
    manager.configs["peer"] = replace(manager.configs["peer"], url="https://foreign.test/mcp")
    try:
        with pytest.raises(Exception, match="resource/profile changed"):
            await manager.connect("peer")
        with pytest.raises(Exception, match="resource/profile changed"):
            await manager.manage_auth("peer", "prepare")
        assert not peer.requests and not manager.tools()
        assert (await manager.manage_auth("peer", "forget"))["state"] == "forgotten"
        assert value.credential() is None
    finally:
        await manager.aclose()


async def test_callback_arriving_before_listener_admission_cannot_acquire_flow_or_crash_owner(monkeypatch):
    peer = Peer()
    value = owner(peer)
    original = asyncio.start_server
    early = []
    async def early_accept(handler, *args, **kwargs):
        listener = await original(handler, *args, **kwargs)
        port = listener.sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b"GET /callback?state=foreign&code=evil HTTP/1.1\r\nHost: localhost\r\n\r\n")
        await writer.drain()
        try:
            early.append(await reader.read())
        except ConnectionResetError:
            early.append(b"")  # EOF or TCP reset both refuse an unowned early callback.
        writer.close()
        try:
            await writer.wait_closed()
        except ConnectionResetError:
            pass
        return listener
    monkeypatch.setattr(asyncio, "start_server", early_accept)
    try:
        review = await value.prepare()
        launch = await value.start(review["review_id"])
        assert early == [b""] and not peer.token_posts and value.status()["state"] == "waiting"
        peer.challenge = parse_qs(urlsplit(launch["authorization_url"]).query)["code_challenge"][0]
        await callback(launch["authorization_url"])
        await finished(value)
        assert value.credential() == "RESOURCE-BOUND-SECRET" and len(peer.token_posts) == 1
    finally:
        await value.close()


async def test_non_ascii_foreign_state_or_receipt_fails_closed_without_callback_crash():
    peer = Peer()
    value = owner(peer)
    try:
        review = await value.prepare()
        with pytest.raises(OAuthError, match="fresh review"):
            await value.start("é" * 32)
        launch = await value.start(review["review_id"])
        assert b"400 Bad Request" in await callback(launch["authorization_url"], state="é" * 43)
        assert value.status()["state"] == "waiting" and not peer.token_posts
        peer.challenge = parse_qs(urlsplit(launch["authorization_url"]).query)["code_challenge"][0]
        await callback(launch["authorization_url"])
        await finished(value)
        assert value.credential() == "RESOURCE-BOUND-SECRET"
    finally:
        await value.close()


async def test_callback_connections_are_bounded_and_foreign_burst_cannot_commit_credentials():
    peer = Peer()
    value = owner(peer)
    writers = []
    try:
        review = await value.prepare()
        launch = await value.start(review["review_id"])
        redirect = urlsplit(parse_qs(urlsplit(launch["authorization_url"]).query)["redirect_uri"][0])
        for _ in range(8):
            _reader, writer = await asyncio.open_connection(redirect.hostname, redirect.port)
            writers.append(writer)
            writer.write(b"GET /callback HTTP/1.1\r\n")
            await writer.drain()
        async with asyncio.timeout(3):
            while len(value.flow.handlers) != 8:
                await asyncio.sleep(0.002)
        extra_reader, extra_writer = await asyncio.open_connection(redirect.hostname, redirect.port)
        assert await extra_reader.read() == b""
        extra_writer.close()
        await extra_writer.wait_closed()
        assert not peer.token_posts and value.credential() is None
        for writer in writers:
            writer.close()
            await writer.wait_closed()
        writers.clear()
        async with asyncio.timeout(3):
            while value.flow.handlers:
                await asyncio.sleep(0.002)
        peer.challenge = parse_qs(urlsplit(launch["authorization_url"]).query)["code_challenge"][0]
        await callback(launch["authorization_url"])
        await finished(value)
        assert value.credential() == "RESOURCE-BOUND-SECRET" and len(peer.token_posts) == 1
    finally:
        for writer in writers:
            writer.close()
        await value.close()


async def test_consent_lease_rejects_foreign_start_and_live_guard_blocks_late_credential_commit(monkeypatch, tmp_path):
    peer = Peer()
    monkeypatch.setattr(OAuthOwner, "client", lambda self: httpx.AsyncClient(transport=peer.transport))
    manager = McpRegistry({"peer": {"transport": "http", "url": RESOURCE, "oauth": CONFIG}}, tmp_path)
    alive = [True]
    try:
        review = await manager.manage_auth("peer", "prepare", lease="first", alive=lambda: alive[0])
        with pytest.raises(Exception, match="another view"):
            await manager.manage_auth("peer", "start", review["review_id"], lease="other")
        launch = await manager.manage_auth("peer", "start", review["review_id"], lease="first", alive=lambda: alive[0])
        peer.challenge = parse_qs(urlsplit(launch["authorization_url"]).query)["code_challenge"][0]
        alive[0] = False
        await callback(launch["authorization_url"])
        async with asyncio.timeout(5):
            while (await manager.manage_auth("peer", "status", lease="first"))["state"] == "waiting":
                await asyncio.sleep(0.003)
        assert manager._oauth["peer"].credential() is None
        await manager.detach_auth("first")
        assert manager._oauth["peer"].flow.listener.is_serving() is False
    finally:
        await manager.aclose()
