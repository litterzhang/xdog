from __future__ import annotations

import asyncio
import base64
import hmac
import json
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest
from xdog.agent.mcp.oauth import OAuthConfig, OAuthError, OAuthOwner, validate_launch, validate_review
from xdog.agent.mcp.oauth_client_auth import token_request

SECRET = 'explicit-jwt-secret-' + 'x' * 48
VARIABLE = 'XDOG_JWT_CLIENT_SECRET'
ISSUER = 'https://issuer.test'
RESOURCE = 'https://resource.test/mcp'
CLIENT = 'registered-jwt-client'


def config():
    return {'issuer': ISSUER, 'client_id': CLIENT, 'scopes': ['tools:read'],
            'token_auth_method': 'client_secret_jwt', 'client_secret_env': VARIABLE}


def decode(value):
    return base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))


def verify_assertion(body):
    assert body['client_assertion_type'] == ['urn:ietf:params:oauth:client-assertion-type:jwt-bearer']
    token = body['client_assertion'][0]
    header, claims, signature = token.split('.')
    assert json.loads(decode(header)) == {'alg': 'HS256', 'typ': 'JWT'}
    expected = hmac.digest(SECRET.encode(), (header + '.' + claims).encode(), 'sha256')
    assert hmac.compare_digest(decode(signature), expected)
    values = json.loads(decode(claims))
    assert values['iss'] == values['sub'] == CLIENT and values['aud'] == ISSUER + '/token'
    assert values['exp'] - values['iat'] == 60 and len(values['jti']) >= 24
    assert SECRET not in token and 'client_secret' not in body
    return values


class Peer:
    def __init__(self):
        self.calls, self.assertions, self.token_bodies = [], [], []
        self.algorithms = ['HS256']
        self.methods = ['client_secret_jwt']
        self.transport = httpx.MockTransport(self.handle)
    async def handle(self, request):
        self.calls.append((request.method, request.url.path))
        assert 'authorization' not in request.headers and 'cookie' not in request.headers
        if request.url.path == '/.well-known/oauth-protected-resource/mcp':
            return httpx.Response(200, json={'resource': RESOURCE, 'authorization_servers': [ISSUER],
                                            'scopes_supported': ['tools:read']})
        if request.url.path == '/.well-known/oauth-authorization-server':
            return httpx.Response(200, json={'issuer': ISSUER, 'authorization_endpoint': ISSUER + '/authorize',
                'token_endpoint': ISSUER + '/token', 'code_challenge_methods_supported': ['S256'],
                'response_types_supported': ['code'], 'token_endpoint_auth_methods_supported': self.methods,
                'token_endpoint_auth_signing_alg_values_supported': self.algorithms})
        assert request.url.path == '/token' and request.method == 'POST'
        body = parse_qs(request.content.decode())
        assert body['client_id'] == [CLIENT] and body['resource'] == [RESOURCE]
        self.token_bodies.append(body)
        self.assertions.append(verify_assertion(body))
        return httpx.Response(200, json={'access_token': 'PRIVATE-ACCESS', 'refresh_token': 'PRIVATE-REFRESH',
            'token_type': 'Bearer', 'expires_in': 3600, 'scope': 'tools:read'})


async def callback(url):
    params = parse_qs(urlsplit(url).query)
    target = urlsplit(params['redirect_uri'][0])
    reader, writer = await asyncio.open_connection(target.hostname, target.port)
    writer.write(('GET /callback?' + urlencode({'state': params['state'][0], 'code': 'owned-code'})
                  + ' HTTP/1.1\r\nHost: localhost\r\n\r\n').encode())
    await writer.drain()
    assert b'200 OK' in await reader.read()
    writer.close()
    await writer.wait_closed()


async def test_actual_owner_jwt_exchange_explicit_refresh_fresh_claims_and_no_public_secret(monkeypatch):
    monkeypatch.setenv(VARIABLE, SECRET)
    peer = Peer()
    owner = OAuthOwner(RESOURCE, OAuthConfig.parse(config()))
    owner.client = lambda: httpx.AsyncClient(transport=peer.transport)
    try:
        review = validate_review(await owner.prepare(), prepared=True)
        assert review['token_auth_method'] == 'client_secret_jwt' and review['client_secret_env'] == VARIABLE
        launch = validate_launch(await owner.start(review['review_id']), review)
        assert SECRET not in json.dumps(review) + json.dumps(launch) and not peer.assertions
        await callback(launch['authorization_url'])
        async with asyncio.timeout(5):
            while owner.status()['state'] == 'waiting':
                await asyncio.sleep(0.002)
        assert owner.credential() == 'PRIVATE-ACCESS' and len(peer.assertions) == 1
        refresh = validate_review(await owner.prepare_refresh())
        await owner.refresh(refresh['review_id'])
        assert len(peer.assertions) == 2 and peer.assertions[0]['jti'] != peer.assertions[1]['jti']
        assert peer.token_bodies[1]['grant_type'] == ['refresh_token']
        public = json.dumps(owner.status()) + repr(owner)
        assert all(value not in public for value in (SECRET, 'PRIVATE-ACCESS', 'PRIVATE-REFRESH',
                                                     peer.token_bodies[0]['client_assertion'][0]))
    finally:
        await owner.close()


@pytest.mark.parametrize('algorithms,methods', [([], ['client_secret_jwt']), (['RS256'], ['client_secret_jwt']),
                                               (['HS256'], ['client_secret_post'])])
async def test_jwt_requires_advertised_method_and_algorithm_without_fallback(algorithms, methods, monkeypatch):
    monkeypatch.setenv(VARIABLE, SECRET)
    peer = Peer()
    peer.algorithms, peer.methods = algorithms, methods
    owner = OAuthOwner(RESOURCE, OAuthConfig.parse(config()))
    owner.client = lambda: httpx.AsyncClient(transport=peer.transport)
    try:
        with pytest.raises(OAuthError, match='fallback'):
            await owner.prepare()
        assert not peer.assertions and owner.flow is None
    finally:
        await owner.close()


async def test_short_secret_is_refused_before_discovery_or_user_consent(monkeypatch):
    monkeypatch.setenv(VARIABLE, 'short')
    peer = Peer()
    owner = OAuthOwner(RESOURCE, OAuthConfig.parse(config()))
    owner.client = lambda: httpx.AsyncClient(transport=peer.transport)
    try:
        with pytest.raises(OAuthError, match='32 secret bytes'):
            await owner.prepare()
        assert not peer.calls
    finally:
        await owner.close()


def test_endpoint_reservations_and_payload_authentication_fields_are_not_caller_overrideable(monkeypatch):
    monkeypatch.setenv(VARIABLE, SECRET)
    with pytest.raises(OAuthError):
        token_request('client_secret_jwt', VARIABLE, CLIENT, {'grant_type': 'authorization_code'})
    with pytest.raises(OAuthError):
        token_request('client_secret_jwt', VARIABLE, CLIENT, {'client_assertion': 'forged'},
                      token_endpoint=ISSUER + '/token')
    body, headers = token_request('client_secret_jwt', VARIABLE, CLIENT,
        {'grant_type': 'authorization_code'}, token_endpoint=ISSUER + '/token')
    assert not headers
    verify_assertion({key: [value] for key, value in body.items()})


@pytest.mark.parametrize('target', [RESOURCE, ISSUER + '/register', ISSUER + '/other',
                                    'https://other.test/token'])
async def test_assertion_cannot_leave_exact_pinned_token_endpoint(target, monkeypatch):
    monkeypatch.setenv(VARIABLE, SECRET)
    peer = Peer()
    owner = OAuthOwner(RESOURCE, OAuthConfig.parse(config()))
    body, headers = token_request('client_secret_jwt', VARIABLE, CLIENT, {}, token_endpoint=ISSUER + '/token')
    owner._token_target = ISSUER + '/token'
    try:
        async with httpx.AsyncClient(transport=peer.transport) as client:
            with pytest.raises(OAuthError, match='exact pinned token'):
                await owner._json(client, 'POST', target, body, token_headers=headers)
        assert not peer.calls
    finally:
        await owner.close()


@pytest.mark.parametrize('failure', ['redirect', 'uncertain'])
async def test_failed_jwt_refresh_consumes_consent_and_never_replays_assertion(failure, monkeypatch):
    from xdog.agent.mcp.oauth import OAuthReview

    monkeypatch.setenv(VARIABLE, SECRET)
    posts = []

    async def handle(request):
        assert str(request.url) == ISSUER + '/token'
        body = parse_qs(request.content.decode())
        verify_assertion(body)
        posts.append(body)
        if failure == 'uncertain':
            raise httpx.ReadError('PRIVATE uncertain transport response', request=request)
        return httpx.Response(307, headers={'Location': 'https://other.test/token'})

    owner = OAuthOwner(RESOURCE, OAuthConfig.parse(config()))
    owner._refresh = 'PRIVATE-REFRESH'
    owner._grant = OAuthReview('a' * 32, ISSUER, RESOURCE, CLIENT, ('tools:read',),
                              ISSUER + '/authorize', ISSUER + '/token',
                              token_auth_method='client_secret_jwt', client_secret_env=VARIABLE)
    owner.client = lambda: httpx.AsyncClient(transport=httpx.MockTransport(handle))
    try:
        review = await owner.prepare_refresh()
        with pytest.raises(OAuthError, match='no token replay') as error:
            await owner.refresh(review['review_id'])
        assert not owner._refresh and owner.status()['refresh_state'] == 'failed'
        with pytest.raises(OAuthError, match='stale/unavailable'):
            await owner.refresh(review['review_id'])
        with pytest.raises(OAuthError, match='authorize explicitly again'):
            await owner.prepare_refresh()
        assert len(posts) == 1
        public = str(error.value) + json.dumps(owner.status())
        assert all(value not in public for value in (SECRET, 'PRIVATE', posts[0]['client_assertion'][0]))
    finally:
        await owner.close()


async def test_saved_jwt_credentials_preserve_method_source_binding_without_saving_secret(tmp_path, monkeypatch):
    import time
    from dataclasses import replace

    from xdog.agent.mcp.oauth import OAuthReview
    from xdog.agent.mcp.oauth_store import CredentialStore, capture, restore

    monkeypatch.setenv(VARIABLE, SECRET)
    owner = OAuthOwner(RESOURCE, OAuthConfig.parse(config()))
    fresh = OAuthOwner(RESOURCE, owner.config)
    owner._token, owner._refresh = 'PRIVATE-ACCESS', 'PRIVATE-REFRESH'
    owner._expires = time.monotonic() + 900
    owner._grant = OAuthReview('a' * 32, ISSUER, RESOURCE, CLIENT, ('tools:read',),
                              ISSUER + '/authorize', ISSUER + '/token',
                              token_auth_method='client_secret_jwt', client_secret_env=VARIABLE)
    try:
        store = CredentialStore(tmp_path / 'private')
        store.save(capture(owner), lambda: True)
        assert SECRET not in next(store.directory.glob('*.json')).read_text()
        saved = store.read(RESOURCE)
        restore(fresh, saved)
        assert fresh.credential() == 'PRIVATE-ACCESS'
        assert fresh._grant.token_auth_method == 'client_secret_jwt'
        for changed in (replace(owner.config, token_auth_method='client_secret_post'),
                        replace(owner.config, client_secret_env='XDOG_OTHER_SECRET')):
            with pytest.raises(OAuthError, match='binding changed'):
                restore(OAuthOwner(RESOURCE, changed), saved)
    finally:
        await owner.close()
        await fresh.close()
