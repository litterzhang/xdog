from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import socket
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest
from xdog.agent.mcp.oauth import OAuthConfig, OAuthError, OAuthOwner, validate_launch, validate_review

RESOURCE = "https://resource.test/mcp"
ISSUER = "https://issuer.test"
DOCUMENT = "https://client.test/xdog.json"


class Peer:
    def __init__(self, mode, port=0):
        self.mode, self.port = mode, port
        self.requests = []
        self.registrations = []
        self.token_calls = []
        self.challenge = ""
        self.metadata = {"issuer": ISSUER, "authorization_endpoint": ISSUER + "/authorize",
            "token_endpoint": ISSUER + "/token", "code_challenge_methods_supported": ["S256"],
            "response_types_supported": ["code"], "token_endpoint_auth_methods_supported": ["none"],
            "client_id_metadata_document_supported": True, "registration_endpoint": ISSUER + "/register"}
        self.document = {"client_id": DOCUMENT, "client_name": "Self-hosted XDOG",
            "redirect_uris": [f"http://127.0.0.1:{port}/callback"], "token_endpoint_auth_method": "none"}
        self.registration_reply = {"client_id": "issued-cli", "token_endpoint_auth_method": "none",
            "client_secret": "NEVER-SEND-PRIVATE-SECRET", "registration_access_token": "NEVER-STORE-MANAGEMENT-TOKEN"}
        self.transport = httpx.MockTransport(self.handle)

    async def handle(self, request):
        self.requests.append((request.method, str(request.url)))
        assert not request.headers.get("authorization") and not request.headers.get("cookie")
        if request.url.path == "/.well-known/oauth-protected-resource/mcp":
            return httpx.Response(200, json={"resource": RESOURCE, "authorization_servers": [ISSUER],
                                           "scopes_supported": ["tools:read"]})
        if request.url.path == "/.well-known/oauth-authorization-server":
            return httpx.Response(200, json=self.metadata)
        if str(request.url) == DOCUMENT:
            return httpx.Response(200, json=self.document)
        if request.url.path == "/register":
            assert request.method == "POST" and request.headers["content-type"] == "application/json"
            body = json.loads(request.content)
            assert body["token_endpoint_auth_method"] == "none" and body["grant_types"] == ["authorization_code"]
            self.registrations.append(body)
            return httpx.Response(201, json=self.registration_reply)
        if request.url.path == "/token":
            body = parse_qs(request.content.decode())
            self.token_calls.append(body)
            assert "client_secret" not in body
            assert body["client_id"] == [DOCUMENT if self.mode == "metadata" else "issued-cli"]
            assert body["resource"] == [RESOURCE]
            digest = base64.urlsafe_b64encode(hashlib.sha256(body["code_verifier"][0].encode()).digest()).decode()
            assert digest.rstrip("=") == self.challenge
            return httpx.Response(200, json={"access_token": "REGISTRATION-TOKEN-SECRET", "token_type": "Bearer",
                                           "expires_in": 3600, "scope": "tools:read"})
        raise AssertionError("Unexpected endpoint")


def unused_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def owner(peer):
    config = {"issuer": ISSUER, "scopes": ["tools:read"], "registration": peer.mode}
    if peer.mode == "metadata":
        config.update(client_id=DOCUMENT, callback_port=peer.port)
    result = OAuthOwner(RESOURCE, OAuthConfig.parse(config))
    result.client = lambda: httpx.AsyncClient(transport=peer.transport, follow_redirects=False)
    return result


async def authorize(value, peer, launch):
    params = parse_qs(urlsplit(launch["authorization_url"]).query)
    peer.challenge = params["code_challenge"][0]
    redirect = urlsplit(params["redirect_uri"][0])
    reader, writer = await asyncio.open_connection(redirect.hostname, redirect.port)
    target = redirect.path + "?" + urlencode({"state": params["state"][0], "code": "owned-code"})
    writer.write(("GET " + target + " HTTP/1.1\r\nHost: localhost\r\n\r\n").encode())
    await writer.drain()
    assert b"200 OK" in await reader.read()
    writer.close()
    await writer.wait_closed()
    async with asyncio.timeout(5):
        while value.status()["state"] == "waiting":
            await asyncio.sleep(0.002)


@pytest.mark.parametrize("mode", ["dynamic", "metadata"])
async def test_real_public_registration_alternative_keeps_pkce_resource_scope_and_secrets_private(mode):
    peer = Peer(mode, unused_port() if mode == "metadata" else 0)
    value = owner(peer)
    try:
        review = validate_review(await value.prepare(), prepared=True)
        assert not peer.registrations and not peer.token_calls
        assert review["registration"] == mode
        launch = validate_launch(await value.start(review["review_id"]), review)
        assert launch["client_id"] == (DOCUMENT if mode == "metadata" else "issued-cli")
        if mode == "dynamic":
            assert len(peer.registrations) == 1
            assert peer.registrations[0]["redirect_uris"] == [
                parse_qs(urlsplit(launch["authorization_url"]).query)["redirect_uri"][0]]
        else:
            assert not peer.registrations and any(url == DOCUMENT for _method, url in peer.requests)
        await authorize(value, peer, launch)
        assert value.status()["state"] == "authorized" and value.credential() == "REGISTRATION-TOKEN-SECRET"
        assert len(peer.token_calls) == 1
        completed = validate_review(value.flow.review.to_dict())
        assert completed["client_id"] == launch["client_id"]
        public = json.dumps(review) + json.dumps(launch) + json.dumps(value.status()) + json.dumps(completed)
        assert all(secret not in public for secret in ("NEVER-SEND-PRIVATE-SECRET", "NEVER-STORE-MANAGEMENT-TOKEN",
                                                        "REGISTRATION-TOKEN-SECRET"))
        with pytest.raises(OAuthError):
            await value.start(review["review_id"])
        assert len(peer.registrations) == (1 if mode == "dynamic" else 0)
    finally:
        await value.close()


@pytest.mark.parametrize("bad", [{"client_id": "https://foreign.test/client.json"}, {"redirect_uris": []},
                                 {"token_endpoint_auth_method": "client_secret_basic"}, {"client_name": "bad\nname"}])
async def test_client_metadata_identity_callback_and_public_method_must_match(bad):
    peer = Peer("metadata", unused_port())
    peer.document.update(bad)
    value = owner(peer)
    try:
        with pytest.raises(OAuthError):
            await value.prepare()
        assert value.flow is None and not peer.registrations and not peer.token_calls
    finally:
        await value.close()


async def test_metadata_mode_does_not_silently_fallback_to_dynamic_registration():
    peer = Peer("metadata", unused_port())
    peer.metadata["client_id_metadata_document_supported"] = False
    value = owner(peer)
    try:
        with pytest.raises(OAuthError, match="no hidden dynamic fallback"):
            await value.prepare()
        assert not peer.registrations and not any(url == DOCUMENT for _method, url in peer.requests)
    finally:
        await value.close()


@pytest.mark.parametrize("bad", [{"client_id": "bad\r\nheader"}, {"token_endpoint_auth_method": "client_secret_post"},
                                 {"redirect_uris": ["http://foreign.test/callback"]}])
async def test_incompatible_dcr_response_does_not_adopt_id_or_launch_code_flow(bad):
    peer = Peer("dynamic")
    peer.registration_reply.update(bad)
    value = owner(peer)
    try:
        review = await value.prepare()
        with pytest.raises(OAuthError):
            await value.start(review["review_id"])
        assert len(peer.registrations) == 1 and value.flow is None and value.credential() is None
        with pytest.raises(OAuthError, match="fresh review"):
            await value.start(review["review_id"])
        assert len(peer.registrations) == 1  # No registration POST replay after an uncertain remote effect.
    finally:
        await value.close()


async def test_revoked_consent_after_registration_response_cannot_adopt_remote_client():
    peer = Peer("dynamic")
    value = owner(peer)
    active = [True]
    original = peer.handle
    async def handler(request):
        response = await original(request)
        if request.url.path == "/register":
            active[0] = False
        return response
    peer.transport = httpx.MockTransport(handler)
    value.guard = lambda: active[0]
    try:
        review = await value.prepare()
        with pytest.raises(OAuthError, match="remote registration effects uncertain"):
            await value.start(review["review_id"])
        assert len(peer.registrations) == 1 and not peer.token_calls and value.flow is None
        assert value.credential() is None
    finally:
        await value.close()


@pytest.mark.parametrize("value", [False, None, 123, "pre-existing-client"])
def test_dynamic_config_does_not_hide_or_coerce_supplied_credentials(value):
    with pytest.raises(OAuthError):
        OAuthConfig.parse({"issuer": ISSUER, "registration": "dynamic", "scopes": ["tools:read"], "client_id": value})
