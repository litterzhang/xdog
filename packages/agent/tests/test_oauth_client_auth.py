from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from urllib.parse import parse_qs, unquote_plus, urlencode, urlsplit

import httpx
import pytest
from xdog.agent.mcp.oauth import OAuthConfig, OAuthError, OAuthOwner, validate_launch, validate_review
from xdog.agent.mcp.oauth_client_auth import token_request

RESOURCE = "https://resource.test/mcp"
ISSUER = "https://issuer.test"
CLIENT = "client:id+%/"
SECRET = "private secret: +%/"
VARIABLE = "XDOG_TEST_OAUTH_SECRET"


def config(method):
    return {"issuer": ISSUER, "client_id": CLIENT, "scopes": ["tools:read"],
            "token_auth_method": method, "client_secret_env": VARIABLE}


class Peer:
    def __init__(self, method):
        self.method = method
        self.calls = []
        self.token_body = None
        self.challenge = ""
        self.advertised = [method]
        self.transport = httpx.MockTransport(self.handle)

    async def handle(self, request):
        self.calls.append((request.method, request.url.path))
        assert "cookie" not in request.headers
        if request.url.path == "/.well-known/oauth-protected-resource/mcp":
            assert "authorization" not in request.headers
            return httpx.Response(200, json={"resource": RESOURCE, "authorization_servers": [ISSUER],
                                           "scopes_supported": ["tools:read"]})
        if request.url.path == "/.well-known/oauth-authorization-server":
            assert "authorization" not in request.headers
            metadata = {"issuer": ISSUER, "authorization_endpoint": ISSUER + "/authorize",
                "token_endpoint": ISSUER + "/token", "code_challenge_methods_supported": ["S256"],
                "response_types_supported": ["code"]}
            if self.advertised is not None:
                metadata["token_endpoint_auth_methods_supported"] = self.advertised
            return httpx.Response(200, json=metadata)
        assert request.url.path == "/token" and request.method == "POST"
        self.token_body = parse_qs(request.content.decode())
        if self.method == "client_secret_basic":
            header = request.headers["authorization"]
            decoded = base64.b64decode(header.removeprefix("Basic ")).decode()
            identifier, value = decoded.split(":", 1)
            assert unquote_plus(identifier) == CLIENT and unquote_plus(value) == SECRET
            assert "client_id" not in self.token_body and "client_secret" not in self.token_body
        else:
            assert "authorization" not in request.headers
            assert self.token_body["client_id"] == [CLIENT] and self.token_body["client_secret"] == [SECRET]
        assert self.token_body["resource"] == [RESOURCE]
        digest = base64.urlsafe_b64encode(
            hashlib.sha256(self.token_body["code_verifier"][0].encode()).digest()).decode()
        assert digest.rstrip("=") == self.challenge
        return httpx.Response(200, json={"access_token": "BEARER-ACCESS-SECRET", "token_type": "Bearer",
                                       "expires_in": 3600, "scope": "tools:read"})


async def callback(url):
    query = parse_qs(urlsplit(url).query)
    redirect = urlsplit(query["redirect_uri"][0])
    reader, writer = await asyncio.open_connection(redirect.hostname, redirect.port)
    target = "/callback?" + urlencode({"code": "owned-code", "state": query["state"][0]})
    writer.write(("GET " + target + " HTTP/1.1\r\nHost: localhost\r\n\r\n").encode())
    await writer.drain()
    assert b"200 OK" in await reader.read()
    writer.close()
    await writer.wait_closed()


@pytest.mark.parametrize("method", ["client_secret_basic", "client_secret_post"])
async def test_real_confidential_exchange_sends_environment_secret_only_to_pinned_token_endpoint(method, monkeypatch):
    monkeypatch.setenv(VARIABLE, SECRET)
    peer = Peer(method)
    value = OAuthOwner(RESOURCE, OAuthConfig.parse(config(method)))
    value.client = lambda: httpx.AsyncClient(transport=peer.transport)
    try:
        review = validate_review(await value.prepare(), prepared=True)
        assert review["token_auth_method"] == method and review["client_secret_env"] == VARIABLE
        launch = validate_launch(await value.start(review["review_id"]), review)
        peer.challenge = parse_qs(urlsplit(launch["authorization_url"]).query)["code_challenge"][0]
        public = json.dumps(review) + json.dumps(launch)
        assert SECRET not in public and "client_secret" not in parse_qs(urlsplit(launch["authorization_url"]).query)
        await callback(launch["authorization_url"])
        async with asyncio.timeout(5):
            while value.status()["state"] == "waiting":
                await asyncio.sleep(0.002)
        assert value.credential() == "BEARER-ACCESS-SECRET" and peer.token_body is not None
        assert SECRET not in json.dumps(value.status()) and "BEARER-ACCESS-SECRET" not in json.dumps(value.status())
    finally:
        await value.close()


def test_inline_secret_implicit_public_credentials_and_unimplemented_methods_are_rejected() -> None:

    def _check_case(bad):
        with pytest.raises(OAuthError):
            OAuthConfig.parse({**config("client_secret_basic"), **bad})

    for bad in [
        {"token_auth_method": "private_key_jwt"},
        {"client_secret": "inline"},
        {"client_secret_env": "BAD NAME"},
        {"registration": "metadata", "client_id": "https://client.test/data.json", "callback_port": 3000},
        {"registration": "dynamic", "client_id": ""},
        {"token_auth_method": "none"},
    ]:
        _check_case(bad)


@pytest.mark.parametrize("secret", [None, "", "bad\r\nheader", "non-ascii-😀"])
async def test_missing_or_unsafe_environment_value_fails_before_metadata_or_code(secret, monkeypatch):
    if secret is None:
        monkeypatch.delenv(VARIABLE, raising=False)
    else:
        monkeypatch.setenv(VARIABLE, secret)
    peer = Peer("client_secret_basic")
    value = OAuthOwner(RESOURCE, OAuthConfig.parse(config("client_secret_basic")))
    value.client = lambda: httpx.AsyncClient(transport=peer.transport)
    try:
        with pytest.raises(OAuthError, match="missing/unsafe"):
            await value.prepare()
        assert not peer.calls and value.flow is None
    finally:
        await value.close()


async def test_issuer_method_mismatch_is_not_silently_downgraded_to_post_or_public(monkeypatch):
    monkeypatch.setenv(VARIABLE, SECRET)
    peer = Peer("client_secret_basic")
    peer.advertised = ["none", "client_secret_post"]
    value = OAuthOwner(RESOURCE, OAuthConfig.parse(config("client_secret_basic")))
    value.client = lambda: httpx.AsyncClient(transport=peer.transport)
    try:
        with pytest.raises(OAuthError, match="no method fallback"):
            await value.prepare()
        assert value.flow is None and peer.token_body is None
    finally:
        await value.close()


async def test_omitted_auth_methods_uses_basic_metadata_default_not_secret_post(monkeypatch):
    monkeypatch.setenv(VARIABLE, SECRET)
    peer = Peer("client_secret_basic")
    peer.advertised = None
    value = OAuthOwner(RESOURCE, OAuthConfig.parse(config("client_secret_basic")))
    value.client = lambda: httpx.AsyncClient(transport=peer.transport)
    try:
        assert (await value.prepare())["token_auth_method"] == "client_secret_basic"
    finally:
        await value.close()


def test_request_builder_does_not_mutate_payload_or_send_multiple_authentication_methods(monkeypatch):
    monkeypatch.setenv(VARIABLE, SECRET)
    payload = {"client_id": CLIENT, "grant_type": "authorization_code"}
    body, headers = token_request("client_secret_basic", VARIABLE, CLIENT, payload)
    assert payload == {"client_id": CLIENT, "grant_type": "authorization_code"}
    assert "client_id" not in body and "client_secret" not in body and set(headers) == {"Authorization"}


@pytest.mark.parametrize("placement", ["header", "body"])
async def test_client_credentials_cannot_be_forwarded_to_discovery_resource_or_registration(placement, monkeypatch):
    monkeypatch.setenv(VARIABLE, SECRET)
    peer = Peer("client_secret_basic")
    value = OAuthOwner(RESOURCE, OAuthConfig.parse(config("client_secret_basic")))
    client = httpx.AsyncClient(transport=peer.transport)
    try:
        with pytest.raises(OAuthError, match="exact pinned token"):
            await value._json(client, "POST", ISSUER + "/register",
                              {"client_secret": SECRET} if placement == "body" else {},
                              token_headers={"Authorization": "Basic secret"} if placement == "header" else None)
        assert not peer.calls
    finally:
        await client.aclose()
        await value.close()


async def test_secret_removed_after_browser_start_causes_no_token_request_or_secret_error(monkeypatch):
    monkeypatch.setenv(VARIABLE, SECRET)
    peer = Peer("client_secret_post")
    value = OAuthOwner(RESOURCE, OAuthConfig.parse(config("client_secret_post")))
    value.client = lambda: httpx.AsyncClient(transport=peer.transport)
    try:
        review = await value.prepare()
        launch = await value.start(review["review_id"])
        monkeypatch.delenv(VARIABLE)
        await callback(launch["authorization_url"])
        async with asyncio.timeout(5):
            while value.status()["state"] == "waiting":
                await asyncio.sleep(0.002)
        assert value.status()["state"] == "failed" and peer.token_body is None and value.credential() is None
        assert SECRET not in json.dumps(value.status())
    finally:
        await value.close()
