"""Registration alternatives are refused; pre-registered PKCE never discovers a client."""
from __future__ import annotations

from dataclasses import replace
from urllib.parse import parse_qs, urlsplit

import pytest
from xdog.agent.mcp import oauth_registration
from xdog.agent.mcp.oauth import OAuthConfig, OAuthError, validate_launch, validate_review

from packages.agent.tests.test_mcp_oauth import CONFIG, ISSUER, Peer, callback, finished, owner


@pytest.mark.parametrize("mode", ["dynamic", "metadata", "unknown", "", None, False, 123])
@pytest.mark.parametrize("client_id", ["registered-client", "", "https://client.test/xdog.json"])
def test_retired_registration_refused_before_network_ca_or_listener(mode, client_id, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Unsupported profile reached I/O")

    monkeypatch.setattr("ssl.create_default_context", forbidden)
    monkeypatch.setattr("httpx.AsyncClient", forbidden)
    monkeypatch.setattr("asyncio.start_server", forbidden)
    with pytest.raises(OAuthError):
        OAuthConfig.parse({**CONFIG, "registration": mode, "client_id": client_id,
                           "ca_file": "/does-not-exist.pem", "callback_port": 3000})
    with pytest.raises(OAuthError):
        OAuthConfig(ISSUER, client_id, ("tools:read",), registration=mode)
    with pytest.raises(OAuthError):
        replace(OAuthConfig.parse(CONFIG), registration=mode)


async def test_issuer_registration_advertisements_cannot_trigger_get_post_or_client_adoption():
    peer = Peer()
    peer.metadata.update(client_id_metadata_document_supported=True,
                         registration_endpoint=ISSUER + "/register")
    value = owner(peer)
    try:
        review = validate_review(await value.prepare(), prepared=True)
        assert review["registration"] == "pre-registered" and review["registration_endpoint"] == ""
        assert review["client_assigned"] is True
        launch = validate_launch(await value.start(review["review_id"]), review)
        params = parse_qs(urlsplit(launch["authorization_url"]).query)
        peer.challenge = params["code_challenge"][0]
        assert params["client_id"] == [CONFIG["client_id"]]
        await callback(launch["authorization_url"])
        await finished(value)
        assert value.credential() == "RESOURCE-BOUND-SECRET"
        assert len(peer.token_posts) == 1
        assert all(request.url.path not in {"/register", "/xdog.json"} for request in peer.requests)
        with pytest.raises(OAuthError):
            await value.start(review["review_id"])
        assert len(peer.token_posts) == 1
    finally:
        await value.close()


def test_registration_protocol_builders_are_absent():
    assert not any(hasattr(oauth_registration, name) for name in
                   ("metadata_id", "validate_document", "registration_request", "registered_id"))
