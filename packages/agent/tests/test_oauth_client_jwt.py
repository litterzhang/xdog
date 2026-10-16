"""Retired JWT profiles refuse offline; generic refresh/storage safety stays public PKCE."""
from __future__ import annotations

import json
from dataclasses import asdict, replace
from urllib.parse import parse_qs

import httpx
import pytest
from xdog.agent.mcp.oauth import OAuthError, OAuthOwner, validate_launch, validate_review
from xdog.agent.mcp.oauth_store import CredentialStore, capture, decode_credential, restore

from packages.agent.tests.test_mcp_oauth import Peer as DiscoveryPeer
from packages.agent.tests.test_mcp_oauth import owner
from packages.agent.tests.test_oauth_refresh import ISSUER, Peer, authenticated
from packages.agent.tests.test_oauth_store import seeded

RETIRED = [
    {"registration": "metadata"},
    {"registration": "dynamic", "client_id": "", "client_assigned": False},
    {"registration_endpoint": "https://issuer.test/register"},
    {"token_auth_method": "client_secret_basic", "client_secret_env": "XDOG_SOURCE"},
    {"token_auth_method": "client_secret_post", "client_secret_env": "XDOG_SOURCE"},
    {"token_auth_method": "client_secret_jwt", "client_secret_env": "XDOG_SOURCE"},
    {"token_auth_method": "private_key_jwt", "client_secret_env": "XDOG_KEY_SOURCE"},
    {"client_private_key_env": ""},
    {"client_assigned": False},
]


@pytest.mark.parametrize("failure", ["redirect", "uncertain"])
async def test_failed_public_refresh_consumes_consent_and_never_replays_post(failure):
    value = await authenticated(Peer())
    posts = []

    async def handle(request):
        assert str(request.url) == ISSUER + "/token" and request.method == "POST"
        assert "authorization" not in request.headers
        body = parse_qs(request.content.decode())
        assert body["grant_type"] == ["refresh_token"]
        assert not {"client_secret", "client_assertion", "client_assertion_type"} & body.keys()
        posts.append(body)
        if failure == "uncertain":
            raise httpx.ReadError("PRIVATE uncertain transport response", request=request)
        return httpx.Response(307, headers={"Location": "https://other.test/token"})

    value.client = lambda: httpx.AsyncClient(transport=httpx.MockTransport(handle), follow_redirects=False)
    try:
        review = await value.prepare_refresh()
        with pytest.raises(OAuthError, match="no token replay") as error:
            await value.refresh(review["review_id"])
        assert not value._refresh and value.status()["refresh_state"] == "failed"
        with pytest.raises(OAuthError, match="stale/unavailable"):
            await value.refresh(review["review_id"])
        with pytest.raises(OAuthError, match="authorize explicitly again"):
            await value.prepare_refresh()
        assert len(posts) == 1
        public = str(error.value) + json.dumps(value.status())
        assert "PRIVATE" not in public and "REFRESH-ONE-SECRET" not in public
    finally:
        await value.close()


@pytest.mark.parametrize("changes", RETIRED)
def test_retired_saved_profiles_refused_before_ca_file_storage_or_network(changes, tmp_path, monkeypatch):
    value = seeded()
    saved = capture(value)
    payload = {"version": 1, **asdict(saved)}
    payload["review"].update(changes)
    with pytest.raises(OAuthError):
        decode_credential(json.dumps(payload).encode(), value.resource)
    with pytest.raises(OAuthError):
        replace(saved, review={**saved.review, **changes})

    # Even an existing record whose mutable public review is later corrupted
    # must be refused before binding reads the CA file or a save creates storage.
    saved.review.update(changes)
    fresh = OAuthOwner(value.resource, replace(value.config, ca_file="/does-not-exist.pem"))
    store = CredentialStore(tmp_path / "private")

    def forbidden(*args, **kwargs):
        pytest.fail("Unsupported saved profile reached I/O")

    monkeypatch.setattr("pathlib.Path.open", forbidden)
    monkeypatch.setattr("httpx.AsyncClient", forbidden)
    monkeypatch.setattr(CredentialStore, "_open", forbidden)
    with pytest.raises(OAuthError):
        restore(fresh, saved)
    with pytest.raises(OAuthError):
        store.save(saved, lambda: True)
    assert fresh.credential() is None and fresh._grant is None
    assert not store.directory.exists()


@pytest.mark.parametrize("changes", RETIRED)
async def test_retired_review_refused_before_loopback_and_launch_validation(changes, monkeypatch):
    peer = DiscoveryPeer()
    value = owner(peer)
    try:
        review = await value.prepare()
        bad = {**review, **changes}
        with pytest.raises(OAuthError):
            validate_review(bad, prepared=True)
        with pytest.raises(OAuthError):
            validate_launch({"review_id": bad["review_id"], "authorization_url": "unused"}, bad)
        internal = {key: item for key, item in changes.items() if hasattr(value.review, key)}
        if not internal:
            return  # Codec-only fields cannot be injected into the slots-based internal review.
        value.review = replace(value.review, **internal)

        def forbidden(*args, **kwargs):
            pytest.fail("Unsupported review opened a callback listener")

        monkeypatch.setattr("asyncio.start_server", forbidden)
        with pytest.raises(OAuthError):
            await value.start(review["review_id"])
        assert value.flow is None and not peer.token_posts
    finally:
        await value.close()


def test_saved_public_profile_hash_and_explicit_file_roundtrip_remain_stable(tmp_path):
    import hashlib

    value = seeded()
    saved = capture(value)
    expected = hashlib.sha256(json.dumps({"resource": value.resource, "config": asdict(value.config)},
                                        sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert saved.profile == expected  # No silent migration or valid-public-profile invalidation.
    store = CredentialStore(tmp_path / "private")
    store.save(saved, lambda: True)
    fresh = OAuthOwner(value.resource, value.config)
    assert fresh.credential() is None
    restore(fresh, store.read(value.resource))
    assert fresh.credential() == value.credential()
    assert fresh._grant.token_auth_method == "none" and fresh._grant.client_secret_env == ""
