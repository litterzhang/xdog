from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest
from xdog.agent.mcp.oauth import OAuthConfig, OAuthError, OAuthOwner

RESOURCE = "https://resource.test/mcp"
ISSUER = "https://issuer.test"


class Peer:
    def __init__(self):
        self.refresh_calls = []
        self.bad = {}
        self.release = asyncio.Event()
        self.block = False
        self.entered = asyncio.Event()
        self.no_rotation = False
        self.fail_network = False
        self.transport = httpx.MockTransport(self.handle)

    async def handle(self, request):
        assert request.method == "POST" and str(request.url) == ISSUER + "/token"
        assert not request.headers.get("authorization") and not request.headers.get("cookie")
        body = parse_qs(request.content.decode())
        if body["grant_type"] == ["authorization_code"]:
            return httpx.Response(200, json={"access_token": "OLD-ACCESS-SECRET", "refresh_token": "REFRESH-ONE-SECRET",
                "token_type": "Bearer", "expires_in": 3600, "scope": "read"})
        self.refresh_calls.append(body)
        self.entered.set()
        assert body["resource"] == [RESOURCE] and body["client_id"] == ["client"] and body["scope"] == ["read"]
        if self.block:
            await self.release.wait()
        if self.fail_network:
            raise httpx.ReadError("Outcome uncertain", request=request)
        reply = {"access_token": "NEW-ACCESS-SECRET", "token_type": "Bearer", "expires_in": 3600, "scope": "read"}
        if not self.no_rotation:
            reply["refresh_token"] = "REFRESH-TWO-SECRET"
        return httpx.Response(200, json={**reply, **self.bad})


async def authenticated(peer):
    from xdog.agent.mcp.oauth import OAuthReview
    value = OAuthOwner(RESOURCE, OAuthConfig.parse({
        "issuer": ISSUER, "client_id": "client", "scopes": ["read", "write"]}))
    value.client = lambda: httpx.AsyncClient(transport=peer.transport)
    value.review = OAuthReview("a" * 32, ISSUER, RESOURCE, "client", ("read", "write"),
                               ISSUER + "/authorize", ISSUER + "/token")
    launch = await value.start("a" * 32)
    query = parse_qs(urlsplit(launch["authorization_url"]).query)
    redirect = urlsplit(query["redirect_uri"][0])
    reader, writer = await asyncio.open_connection(redirect.hostname, redirect.port)
    target = "/callback?" + urlencode({"code": "owned", "state": query["state"][0]})
    writer.write(("GET " + target + " HTTP/1.1\r\nHost: localhost\r\n\r\n").encode())
    await writer.drain()
    assert b"200 OK" in await reader.read()
    writer.close()
    await writer.wait_closed()
    async with asyncio.timeout(5):
        while value.status()["state"] == "waiting":
            await asyncio.sleep(0.002)
    assert value.status()["refresh_available"]
    return value


async def test_explicit_refresh_rotates_once_uses_actual_grant_scopes_and_never_exposes_tokens():
    peer = Peer()
    value = await authenticated(peer)
    try:
        value._expires -= 4000
        assert value.credential() is None and not peer.refresh_calls
        review = await value.prepare_refresh()
        assert review["operation"] == "refresh" and review["scopes"] == ["read"]
        assert not peer.refresh_calls  # Review is not redemption.
        public = await value.refresh(review["review_id"])
        assert len(peer.refresh_calls) == 1 and peer.refresh_calls[0]["refresh_token"] == ["REFRESH-ONE-SECRET"]
        assert value.credential() == "NEW-ACCESS-SECRET" and value._refresh == "REFRESH-TWO-SECRET"
        assert all(secret not in json.dumps(public) + json.dumps(review) for secret in (
            "OLD-ACCESS-SECRET", "NEW-ACCESS-SECRET", "REFRESH-ONE-SECRET", "REFRESH-TWO-SECRET"))
        with pytest.raises(OAuthError):
            await value.refresh(review["review_id"])
        assert len(peer.refresh_calls) == 1
    finally:
        await value.close()
    assert not value._refresh and value.credential() is None


@pytest.mark.parametrize("bad", [{"scope": "read write"}, {"refresh_token": "bad\r\nvalue"},
                                 {"token_type": "Basic"}, {"expires_in": True}])
async def test_invalid_refresh_discards_rotating_token_and_never_retries_or_broadens(bad):
    peer = Peer()
    value = await authenticated(peer)
    peer.bad = bad
    try:
        review = await value.prepare_refresh()
        with pytest.raises(OAuthError, match="rotation uncertain"):
            await value.refresh(review["review_id"])
        assert value.credential() == "OLD-ACCESS-SECRET" and not value._refresh
        assert len(peer.refresh_calls) == 1
        with pytest.raises(OAuthError, match="authorize explicitly again"):
            await value.prepare_refresh()
        assert len(peer.refresh_calls) == 1
    finally:
        await value.close()


async def test_lost_rotation_response_cannot_redeem_old_refresh_token_again():
    peer = Peer()
    value = await authenticated(peer)
    peer.fail_network = True
    try:
        review = await value.prepare_refresh()
        with pytest.raises(OAuthError, match="no token replay"):
            await value.refresh(review["review_id"])
        assert not value._refresh and len(peer.refresh_calls) == 1
        assert value.status()["refresh_state"] == "failed"
    finally:
        await value.close()


async def test_revoked_refresh_owner_cannot_commit_late_rotated_credentials():
    peer = Peer()
    value = await authenticated(peer)
    peer.block = True
    alive = [True]
    value.guard = lambda: alive[0]
    try:
        review = await value.prepare_refresh()
        task = asyncio.create_task(value.refresh(review["review_id"]))
        await asyncio.wait_for(peer.entered.wait(), 3)
        alive[0] = False
        peer.release.set()
        with pytest.raises(OAuthError):
            await task
        assert value.credential() == "OLD-ACCESS-SECRET" and not value._refresh
    finally:
        peer.release.set()
        await value.close()


async def test_cancel_settles_in_flight_refresh_without_deadlock_or_old_token_replay():
    peer = Peer()
    value = await authenticated(peer)
    peer.block = True
    try:
        review = await value.prepare_refresh()
        task = asyncio.create_task(value.refresh(review["review_id"]))
        await asyncio.wait_for(peer.entered.wait(), 3)
        async with asyncio.timeout(3):
            await value.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not value._refresh and value.status()["refresh_state"] == "canceled"
        assert len(peer.refresh_calls) == 1
    finally:
        peer.release.set()
        await value.close()


async def test_success_without_rotation_retains_old_token_only_after_valid_response():
    peer = Peer()
    value = await authenticated(peer)
    peer.no_rotation = True
    try:
        review = await value.prepare_refresh()
        await value.refresh(review["review_id"])
        assert value._refresh == "REFRESH-ONE-SECRET" and value.credential() == "NEW-ACCESS-SECRET"
        stale = await value.prepare_refresh()
        value._refresh_review = replace(value._refresh_review, created=value._refresh_review.created - 301)
        with pytest.raises(OAuthError):
            await value.refresh(stale["review_id"])
        assert len(peer.refresh_calls) == 1
    finally:
        await value.close()


async def test_unavailable_refresh_does_not_claim_lease_and_block_another_view(tmp_path):
    from xdog.agent.mcp.registry import McpRegistry
    manager = McpRegistry({"peer": {"transport": "http", "url": RESOURCE,
        "oauth": {"issuer": ISSUER, "client_id": "client", "scopes": ["read"]}}}, tmp_path)
    try:
        for lease in ("first", "second"):
            with pytest.raises(OAuthError, match="No live resource-bound refresh grant"):
                await manager.manage_auth("peer", "refresh_prepare", lease=lease)
            assert "peer" not in manager._auth_leases
    finally:
        await manager.aclose()
