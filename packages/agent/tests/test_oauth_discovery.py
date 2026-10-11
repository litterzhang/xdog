from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest
from xdog.agent.mcp.manager import McpManager
from xdog.agent.mcp.oauth import OAuthConfig, OAuthError, OAuthOwner
from xdog.agent.mcp.oauth_discovery import challenge, issuer_candidates, resource_candidates

RESOURCE = "https://resource.test/api/mcp"
ISSUER = "https://issuer.test/tenant"
CONFIG = {"issuer": ISSUER, "client_id": "client", "scopes": ["files:read", "files:write"]}
META = {"issuer": ISSUER, "authorization_endpoint": "https://issuer.test/authorize",
        "token_endpoint": "https://issuer.test/token", "code_challenge_methods_supported": ["S256"],
        "response_types_supported": ["code"], "token_endpoint_auth_methods_supported": ["none"]}


def test_quoted_header_boundaries_and_non_bearer_tickets_do_not_corrupt_operation_scope():
    parsed = challenge('Negotiate dGVzdA==, Basic realm="other,account", bEaReR realm="api", '
        'resource_metadata="https://resource.test/custom?tenant=one", scope="files:read files:write"')
    assert parsed.metadata == "https://resource.test/custom?tenant=one"
    assert parsed.scopes == ("files:read", "files:write")
    assert challenge('Bearer realm="a,b", scope="files:read,metadata"').scopes == ("files:read,metadata",)
    assert challenge('Basic realm="private"') is None


@pytest.mark.parametrize("value", [
    'Bearer scope="read", Scope="write"', 'Bearer scope="unterminated', 'Bearer scope="read" trailing',
    'Bearer resource_metadata="https://user:secret@resource.test/meta"', 'Bearer realm="x", Bearer realm="y"',
    'Bearer scope="read\r\nprivate"', 'Bearer resource_metadata="/relative"', 'Bearer scope="read"' + 'x' * 16384,
])
def test_malformed_duplicate_or_ambiguous_challenges_are_not_silently_ignored(value):
    with pytest.raises(OAuthError):
        challenge(value)


def test_well_known_candidate_order_and_pointer_origin_pinning():
    assert resource_candidates(RESOURCE) == (
        "https://resource.test/.well-known/oauth-protected-resource/api/mcp",
        "https://resource.test/.well-known/oauth-protected-resource")
    assert len(resource_candidates("https://resource.test/")) == 1
    assert issuer_candidates(ISSUER) == (
        "https://issuer.test/.well-known/oauth-authorization-server/tenant",
        "https://issuer.test/.well-known/openid-configuration/tenant",
        "https://issuer.test/tenant/.well-known/openid-configuration")
    assert len(issuer_candidates("https://issuer.test")) == 2
    with pytest.raises(OAuthError, match="origin differs"):
        resource_candidates(RESOURCE, challenge('Bearer resource_metadata="https://foreign.test/meta"'))


async def test_resource_root_and_oidc_fallbacks_are_ordered_and_cookie_credential_free():
    requests = []
    async def handler(request):
        requests.append(str(request.url))
        assert not request.headers.get("authorization") and not request.headers.get("cookie")
        if request.url.path == "/.well-known/oauth-protected-resource":
            return httpx.Response(200, json={"resource": RESOURCE, "authorization_servers": [ISSUER],
                                           "scopes_supported": ["files:read"]})
        if request.url.path == "/tenant/.well-known/openid-configuration":
            return httpx.Response(200, json=META)
        return httpx.Response(404, headers={"Set-Cookie": "private=secret; Domain=.test; Path=/"})
    owner = OAuthOwner(RESOURCE, OAuthConfig.parse(CONFIG))
    owner.client = lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
    try:
        review = await owner.prepare()
        assert review["scopes"] == ["files:read"] and review["scope_source"] == "metadata"
        assert review["resource_metadata"].endswith("/oauth-protected-resource")
        assert review["issuer_metadata"].endswith("/tenant/.well-known/openid-configuration")
        assert requests == [*resource_candidates(RESOURCE), *issuer_candidates(ISSUER)]
        assert owner.flow is None and owner.credential() is None
    finally:
        await owner.close()


async def test_successful_but_wrong_issuer_metadata_is_not_replaced_by_a_less_specific_fallback():
    requests = []
    async def handler(request):
        requests.append(request.url.path)
        if request.url.host == "resource.test":
            return httpx.Response(200, json={"resource": RESOURCE, "authorization_servers": [ISSUER]})
        return httpx.Response(200, json={**META, "issuer": "https://other.test"})
    owner = OAuthOwner(RESOURCE, OAuthConfig.parse(CONFIG))
    owner.client = lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(OAuthError, match="identity mismatch"):
            await owner.prepare()
        assert len(requests) == 2  # Invalid 200 metadata isn't absence.
    finally:
        await owner.close()


async def test_actual_denial_pointer_and_operation_scope_are_retained_without_replaying_post(monkeypatch, tmp_path):
    requests = []
    async def handler(request):
        requests.append((request.method, str(request.url)))
        assert not request.headers.get("authorization") and not request.headers.get("cookie")
        if request.method == "POST":
            assert json.loads(request.content)["method"] == "server/discover"
            return httpx.Response(401, headers={"WWW-Authenticate": 'Bearer '
                'resource_metadata="https://resource.test/custom?tenant=one", scope="files:write"'})
        if request.url.path == "/custom":
            # Operation scope need not be listed in broad metadata scopes_supported.
            return httpx.Response(200, json={"resource": RESOURCE, "authorization_servers": [ISSUER],
                                           "scopes_supported": ["files:read"]})
        return httpx.Response(200, json=META)
    original = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(OAuthOwner, "client", lambda self: original(transport=transport))
    import xdog.agent.mcp.http as module
    monkeypatch.setattr(module, "httpx", SimpleNamespace(
        AsyncClient=lambda **kwargs: original(transport=transport, **kwargs), Limits=httpx.Limits,
        HTTPError=httpx.HTTPError, Headers=httpx.Headers))
    manager = McpManager({"peer": {"transport": "http", "url": RESOURCE, "oauth": CONFIG}}, tmp_path)
    try:
        with pytest.raises(Exception, match="authorization required"):
            await manager.connect("peer")
        review = await manager.manage_auth("peer", "prepare")
        assert review["scopes"] == ["files:write"] and review["scope_source"] == "challenge"
        assert review["resource_metadata"] == "https://resource.test/custom?tenant=one"
        assert sum(method == "POST" for method, _url in requests) == 1
        assert len(requests) == 3 and not manager.tools()
    finally:
        await manager.aclose()


async def test_failed_resource_scope_above_operator_ceiling_cannot_launch_or_exchange():
    owner = OAuthOwner(RESOURCE, OAuthConfig.parse({**CONFIG, "scopes": ["files:read"]}))
    async def handler(request):
        return httpx.Response(200, json={"resource": RESOURCE, "authorization_servers": [ISSUER],
                                       "scopes_supported": ["files:read"]})
    owner.client = lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(OAuthError, match="exceed operator"):
            await owner.prepare(challenge('Bearer scope="files:write"'))
        assert owner.review is None and owner.flow is None and owner.credential() is None
    finally:
        await owner.close()


async def test_malformed_received_challenge_does_not_turn_into_a_fallback_authorization(monkeypatch, tmp_path):
    requests = []
    async def handler(request):
        requests.append(request.method)
        return httpx.Response(401, headers={"WWW-Authenticate": 'Bearer scope="read", SCOPE="write"'})
    original = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    import xdog.agent.mcp.http as module
    monkeypatch.setattr(module, "httpx", SimpleNamespace(
        AsyncClient=lambda **kwargs: original(transport=transport, **kwargs), Limits=httpx.Limits,
        HTTPError=httpx.HTTPError, Headers=httpx.Headers))
    manager = McpManager({"peer": {"transport": "http", "url": RESOURCE, "oauth": CONFIG}}, tmp_path)
    try:
        with pytest.raises(Exception, match="Invalid MCP authorization challenge"):
            await manager.connect("peer")
        with pytest.raises(Exception, match="was not ignored"):
            await manager.manage_auth("peer", "prepare")
        assert requests == ["POST"] and not manager.tools()
    finally:
        await manager.aclose()


async def test_scope_less_metadata_can_be_reviewed_without_implicit_broader_grants():
    from xdog.agent.mcp.oauth import validate_review
    owner = OAuthOwner(RESOURCE, OAuthConfig.parse(CONFIG))
    async def handler(request):
        if request.url.host == "resource.test":
            return httpx.Response(200, json={"resource": RESOURCE, "authorization_servers": [ISSUER],
                                           "scopes_supported": []})
        return httpx.Response(200, json=META)
    owner.client = lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        review = await owner.prepare()
        assert validate_review(review)["scopes"] == [] and review["scope_source"] == "metadata"
        assert owner.flow is None and owner.credential() is None
    finally:
        await owner.close()


def test_optional_header_whitespace_does_not_accept_invalid_scope_separators():
    assert challenge('Bearer\t scope="files:read",\t realm="other"').scopes == ("files:read",)
    with pytest.raises(OAuthError, match="non-space delimiters"):
        challenge('Bearer scope="files:read\tfiles:write"')


async def test_explicit_metadata_origin_can_serve_prm_but_does_not_change_token_endpoint_trust():
    requests = []
    async def handler(request):
        requests.append(str(request.url))
        assert not request.headers.get("authorization") and not request.headers.get("cookie")
        if request.url.host == "metadata.test":
            return httpx.Response(200, json={"resource": RESOURCE, "authorization_servers": [ISSUER],
                                           "scopes_supported": ["files:read"]})
        return httpx.Response(200, json=META)
    owner = OAuthOwner(RESOURCE, OAuthConfig.parse({**CONFIG, "metadata_origins": ["https://metadata.test"]}))
    owner.client = lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        review = await owner.prepare(challenge('Bearer resource_metadata="https://metadata.test/shared?tenant=one"'))
        assert review["resource_metadata"] == "https://metadata.test/shared?tenant=one"
        assert review["token_endpoint"] == "https://issuer.test/token" and len(requests) == 2
        assert owner.flow is None
    finally:
        await owner.close()


@pytest.mark.parametrize("origins", [["http://metadata.test"], ["https://metadata.test/path"],
                                      ["https://metadata.test", "https://metadata.test/"]])
def test_metadata_origin_configuration_cannot_downgrade_https_or_pin_arbitrary_paths(origins):
    with pytest.raises(OAuthError):
        OAuthConfig.parse({**CONFIG, "metadata_origins": origins})
