"""Public token exchange plus explicit refusal of retired confidential authentication."""
from __future__ import annotations

import json
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from xdog.agent.mcp.oauth import OAuthConfig, OAuthError, validate_launch, validate_review
from xdog.agent.mcp.oauth_client_auth import METHODS, token_request

from packages.agent.tests.test_mcp_oauth import CONFIG, ISSUER, RESOURCE, Peer, callback, finished, owner


async def test_real_public_exchange_keeps_pkce_resource_and_credentials_out_of_public_output(monkeypatch):
    monkeypatch.setenv("XDOG_TEST_OAUTH_SECRET", "PRIVATE unused environment credential")
    peer = Peer()
    value = owner(peer)
    try:
        review = validate_review(await value.prepare(), prepared=True)
        assert review["token_auth_method"] == "none" and review["client_secret_env"] == ""
        launch = validate_launch(await value.start(review["review_id"]), review)
        peer.challenge = parse_qs(urlsplit(launch["authorization_url"]).query)["code_challenge"][0]
        await callback(launch["authorization_url"])
        await finished(value)
        assert value.credential() == "RESOURCE-BOUND-SECRET" and len(peer.token_posts) == 1
        assert not {"client_secret", "client_assertion", "client_assertion_type"} & peer.token_posts[0].keys()
        public = json.dumps(review) + json.dumps(launch) + json.dumps(value.status())
        assert "PRIVATE" not in public and "RESOURCE-BOUND-SECRET" not in public
    finally:
        await value.close()


@pytest.mark.parametrize("changes", [
    {"token_auth_method": method, "client_secret_env": "XDOG_TEST_OAUTH_SECRET"}
    for method in ("client_secret_basic", "client_secret_post", "client_secret_jwt", "private_key_jwt", "unknown")
] + [{"client_secret_env": "XDOG_TEST_OAUTH_SECRET"}, {"client_secret_env": None},
     {"client_secret": "inline"}, {"client_assertion": "inline"},
     {"client_private_key_env": ""}, {"client_private_key_env": "XDOG_PRIVATE_KEY"}])
def test_confidential_or_inline_sources_refused_before_network_or_helpers(changes, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Unsupported profile reached I/O")

    monkeypatch.setattr("httpx.AsyncClient", forbidden)
    monkeypatch.setattr("ssl.create_default_context", forbidden)
    monkeypatch.setattr("subprocess.Popen", forbidden)
    with pytest.raises(OAuthError) as error:
        OAuthConfig.parse({**CONFIG, **changes})
    assert "inline" not in str(error.value) and "XDOG_PRIVATE_KEY" not in str(error.value)
    assert METHODS == {"none"}


@pytest.mark.parametrize("advertised", [None, ["client_secret_basic"], ["client_secret_post", "private_key_jwt"],
                                        [], "none", ["none", 1]])
async def test_public_method_must_be_explicitly_advertised_without_fallback(advertised):
    peer = Peer()
    if advertised is None:
        peer.metadata.pop("token_endpoint_auth_methods_supported")
    else:
        peer.metadata["token_endpoint_auth_methods_supported"] = advertised
    value = owner(peer)
    try:
        with pytest.raises(OAuthError, match="no method fallback"):
            await value.prepare()
        assert value.flow is None and not peer.token_posts
    finally:
        await value.close()


def test_public_request_builder_copies_payload_and_rejects_multiple_credentials():
    payload = {"client_id": CONFIG["client_id"], "grant_type": "authorization_code"}
    body, headers = token_request("none", "", CONFIG["client_id"], payload)
    assert body == payload and body is not payload and not headers
    assert payload == {"client_id": CONFIG["client_id"], "grant_type": "authorization_code"}
    for field in ("client_secret", "client_assertion", "client_assertion_type"):
        with pytest.raises(OAuthError):
            token_request("none", "", CONFIG["client_id"], {**payload, field: "forged"})
    with pytest.raises(OAuthError):
        token_request("none", "", CONFIG["client_id"], {"client_id": "other"})


@pytest.mark.parametrize("target", [RESOURCE, ISSUER + "/register", ISSUER + "/token", "https://other.test/token"])
@pytest.mark.parametrize("placement", ["header", "client_secret", "client_assertion", "client_assertion_type"])
async def test_confidential_credentials_never_leave_even_the_exact_token_operation(target, placement):
    peer = Peer()
    value = owner(peer)
    value._token_target = ISSUER + "/token"
    try:
        async with httpx.AsyncClient(transport=peer.transport) as client:
            with pytest.raises(OAuthError, match="confidential credentials refused"):
                await value._json(client, "POST", target,
                                  {placement: "PRIVATE"} if placement != "header" else {},
                                  token_headers={"Authorization": "Basic PRIVATE"} if placement == "header" else None)
        assert not peer.requests
    finally:
        await value.close()


@pytest.mark.parametrize("target", [RESOURCE, ISSUER + "/register", "https://other.test/token"])
async def test_public_token_post_cannot_leave_exact_pinned_endpoint(target):
    peer = Peer()
    value = owner(peer)
    value._token_target = ISSUER + "/token"
    try:
        async with httpx.AsyncClient(transport=peer.transport) as client:
            with pytest.raises(OAuthError, match="exact pinned token"):
                await value._json(client, "POST", target, {"client_id": CONFIG["client_id"]})
        assert not peer.requests
    finally:
        await value.close()


@pytest.mark.parametrize("failure", ["redirect", "uncertain"])
async def test_failed_public_code_exchange_never_replays_post_or_reuses_consent(failure):
    peer = Peer()
    original = peer.handle
    posts = []

    async def handle(request):
        if request.url.path != "/token":
            return await original(request)
        posts.append(request)
        assert "authorization" not in request.headers
        if failure == "uncertain":
            raise httpx.ReadError("PRIVATE uncertain code response", request=request)
        return httpx.Response(307, headers={"Location": "https://other.test/token"})

    peer.transport = httpx.MockTransport(handle)
    value = owner(peer)
    try:
        review = await value.prepare()
        launch = await value.start(review["review_id"])
        await callback(launch["authorization_url"])
        await finished(value)
        assert value.status()["state"] == "failed" and value.credential() is None
        assert value.flow.verifier == "" and value.flow.code.cancelled()
        with pytest.raises(OAuthError):
            await value.start(review["review_id"])
        assert len(posts) == 1 and "PRIVATE" not in json.dumps(value.status())
    finally:
        await value.close()


async def test_revoked_public_consent_cannot_allocate_callback_or_adopt_changed_client(monkeypatch):
    peer = Peer()
    value = owner(peer)
    try:
        review = await value.prepare()
        value.guard = lambda: False

        def forbidden(*args, **kwargs):
            pytest.fail("Revoked consent allocated a callback listener")

        monkeypatch.setattr("asyncio.start_server", forbidden)
        with pytest.raises(OAuthError, match="consent owner changed"):
            await value.start(review["review_id"])
        assert value.flow is None and not peer.token_posts
    finally:
        await value.close()
