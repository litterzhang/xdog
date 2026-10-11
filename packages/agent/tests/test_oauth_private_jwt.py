from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest
from xdog.agent.mcp.oauth import OAuthConfig, OAuthError, OAuthOwner, validate_review
from xdog.agent.mcp.oauth_client_auth import async_token_request, token_request
from xdog.agent.mcp.oauth_private_jwt import validate_key

pytestmark = pytest.mark.skipif(os.name != 'posix' or not shutil.which('openssl'), reason='owned POSIX/OpenSSL signer')
ISSUER, RESOURCE = 'https://issuer.test', 'https://resource.test/mcp'
CLIENT, VARIABLE = 'registered-client', 'TEST_RSA_PEM'


@pytest.fixture
def key(monkeypatch):
    value = subprocess.run(['openssl', 'genpkey', '-algorithm', 'RSA', '-pkeyopt', 'rsa_keygen_bits:2048'],
                           capture_output=True, check=True).stdout
    monkeypatch.setenv(VARIABLE, value.decode())
    public = subprocess.run(['openssl', 'pkey', '-text_pub', '-noout'], input=value,
                            capture_output=True, check=True).stdout.decode()
    modulus = int(re.search(r'Modulus:\s*([0-9a-f:\s]+)Exponent:', public)[1].replace(':', '').replace('\n', '')
                  .replace(' ', ''), 16)
    exponent = int(re.search(r'Exponent: ([0-9]+)', public)[1])
    return value, modulus, exponent


def verify(assertion, key):
    header, claims, signature = assertion.split('.')
    def decode(value):
        return base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))
    assert json.loads(decode(header)) == {'alg': 'RS256', 'typ': 'JWT'}
    # Independent public RSA/PKCS#1 v1.5 verification, not the production signing helper.
    _, modulus, exponent = key
    size = (modulus.bit_length() + 7) // 8
    signature_bytes = decode(signature)
    assert len(signature_bytes) == size and int.from_bytes(signature_bytes) < modulus
    encoded = pow(int.from_bytes(signature_bytes), exponent, modulus).to_bytes(size)
    digest = bytes.fromhex('3031300d060960864801650304020105000420') + hashlib.sha256(
        (header + '.' + claims).encode()).digest()
    assert encoded == b'\x00\x01' + b'\xff' * (size - len(digest) - 3) + b'\x00' + digest
    data = json.loads(decode(claims))
    assert data['iss'] == data['sub'] == CLIENT and data['aud'] == ISSUER + '/token'
    assert data['exp'] - data['iat'] == 60 and len(data['jti']) >= 24
    return data


def config():
    return OAuthConfig.parse({'issuer': ISSUER, 'client_id': CLIENT, 'scopes': ['tools:read'],
        'token_auth_method': 'private_key_jwt', 'client_private_key_env': VARIABLE})


async def test_real_rs256_signature_fresh_assertions_source_bounds_and_auth_fields(key):
    first, headers = await async_token_request('private_key_jwt', VARIABLE, CLIENT,
                                               {'grant_type': 'authorization_code'},
                                               token_endpoint=ISSUER + '/token')
    second, _ = await async_token_request('private_key_jwt', VARIABLE, CLIENT, {'grant_type': 'refresh_token'},
                                         token_endpoint=ISSUER + '/token')
    assert not headers and 'client_secret' not in first and key[0].decode() not in json.dumps(first)
    one, two = verify(first['client_assertion'], key), verify(second['client_assertion'], key)
    assert one['jti'] != two['jti']
    with pytest.raises(OAuthError):
        await async_token_request('private_key_jwt', VARIABLE, CLIENT, {'client_assertion': 'forged'},
                                  token_endpoint=ISSUER + '/token')
    with pytest.raises(OAuthError):
        token_request('private_key_jwt', VARIABLE, CLIENT, {}, token_endpoint=ISSUER + '/token')


@pytest.mark.parametrize('value', ['', 'PRIVATE-UNSAFE-KEY', '-----BEGIN ENCRYPTED PRIVATE KEY-----',
                                   '\x00', 'x' * 65537])
async def test_invalid_key_source_refused_before_discovery(value, monkeypatch):
    monkeypatch.setenv(VARIABLE, value) if '\x00' not in value else None
    if '\x00' in value:
        monkeypatch.delenv(VARIABLE, raising=False)
    with pytest.raises(OAuthError):
        await validate_key(VARIABLE)


@pytest.mark.parametrize('bits', [1024])
async def test_weak_rsa_key_refused(bits, monkeypatch):
    value = subprocess.run(['openssl', 'genpkey', '-algorithm', 'RSA', '-pkeyopt', 'rsa_keygen_bits:' + str(bits)],
                           capture_output=True, check=True).stdout
    monkeypatch.setenv(VARIABLE, value.decode())
    with pytest.raises(OAuthError, match='2048'):
        await validate_key(VARIABLE)


async def test_real_owner_exchange_refresh_negotiation_and_no_public_key_or_assertion(key):
    calls, assertions = [], []
    async def peer(request):
        calls.append((request.method, request.url.path))
        assert 'authorization' not in request.headers and 'cookie' not in request.headers
        if request.url.host == 'resource.test':
            return httpx.Response(200, json={'resource': RESOURCE, 'authorization_servers': [ISSUER]})
        if request.method == 'GET':
            return httpx.Response(200, json={'issuer': ISSUER, 'authorization_endpoint': ISSUER + '/authorize',
                'token_endpoint': ISSUER + '/token', 'code_challenge_methods_supported': ['S256'],
                'response_types_supported': ['code'], 'token_endpoint_auth_methods_supported': ['private_key_jwt'],
                'token_endpoint_auth_signing_alg_values_supported': ['RS256']})
        body = parse_qs(request.content.decode())
        assert 'client_secret' not in body and body['resource'] == [RESOURCE]
        assertions.append(body['client_assertion'][0])
        verify(assertions[-1], key)
        return httpx.Response(200, json={'token_type': 'Bearer', 'access_token': 'PRIVATE-ACCESS',
            'refresh_token': 'PRIVATE-REFRESH', 'scope': 'tools:read', 'expires_in': 3600})
    owner = OAuthOwner(RESOURCE, config())
    owner.client = lambda: httpx.AsyncClient(transport=httpx.MockTransport(peer))
    try:
        review = validate_review(await owner.prepare(), prepared=True)
        assert review['token_auth_method'] == 'private_key_jwt' and review['client_secret_env'] == VARIABLE
        launch = await owner.start(review['review_id'])
        data = parse_qs(urlsplit(launch['authorization_url']).query)
        callback = urlsplit(data['redirect_uri'][0])
        reader, writer = await asyncio.open_connection(callback.hostname, callback.port)
        writer.write(('GET /callback?' + urlencode({'state': data['state'][0], 'code': 'owned-code'})
                      + ' HTTP/1.1\r\nHost: localhost\r\n\r\n').encode())
        await writer.drain()
        assert b'200 OK' in await reader.read()
        writer.close()
        await writer.wait_closed()
        async with asyncio.timeout(6):
            while owner.status()['state'] == 'waiting':
                await asyncio.sleep(0.003)
        assert owner.credential() == 'PRIVATE-ACCESS' and len(assertions) == 1
        refresh = await owner.prepare_refresh()
        await owner.refresh(refresh['review_id'])
        assert len(assertions) == 2 and assertions[0] != assertions[1]
        public = json.dumps(owner.status()) + repr(owner) + json.dumps(review)
        assert key[0].decode() not in public and all(value not in public for value in (*assertions, 'PRIVATE-ACCESS'))
    finally:
        await owner.close()


@pytest.mark.parametrize('methods,algorithms', [(['none'], ['RS256']), (['private_key_jwt'], ['HS256']),
                                                (['private_key_jwt'], [])])
async def test_private_key_method_and_rs256_must_be_advertised_without_any_fallback(key, methods, algorithms):
    posts = []
    async def peer(request):
        if request.url.host == 'resource.test':
            return httpx.Response(200, json={'resource': RESOURCE, 'authorization_servers': [ISSUER]})
        if request.method == 'GET':
            return httpx.Response(200, json={'issuer': ISSUER, 'authorization_endpoint': ISSUER + '/authorize',
                'token_endpoint': ISSUER + '/token', 'code_challenge_methods_supported': ['S256'],
                'response_types_supported': ['code'], 'token_endpoint_auth_methods_supported': methods,
                'token_endpoint_auth_signing_alg_values_supported': algorithms})
        posts.append(request)
        pytest.fail('No token POST without explicit method/algorithm negotiation')
    owner = OAuthOwner(RESOURCE, config())
    owner.client = lambda: httpx.AsyncClient(transport=httpx.MockTransport(peer))
    try:
        with pytest.raises(OAuthError, match='fallback'):
            await owner.prepare()
        assert not posts and owner.flow is None
    finally:
        await owner.close()


@pytest.mark.parametrize('changes', [{'registration': 'dynamic', 'client_id': ''},
    {'registration': 'metadata', 'client_id': 'https://client.test/client.json', 'callback_port': 8000},
    {'client_private_key_env': ''}, {'client_secret_env': 'SECRET'}])
def test_private_key_source_is_explicit_and_only_pre_registered(changes):
    with pytest.raises(OAuthError):
        OAuthConfig.parse({'issuer': ISSUER, 'client_id': CLIENT, 'scopes': ['tools:read'],
            'token_auth_method': 'private_key_jwt', 'client_private_key_env': VARIABLE, **changes})


async def test_retired_owner_cannot_post_a_valid_signed_assertion(key):
    body, headers = await async_token_request('private_key_jwt', VARIABLE, CLIENT, {}, token_endpoint=ISSUER + '/token')
    owner = OAuthOwner(RESOURCE, config())
    owner._token_target = ISSUER + '/token'
    owner.guard = lambda: False
    async def forbidden(request):
        pytest.fail('Retired signer owner cannot submit token authentication')
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as client:
            with pytest.raises(OAuthError):
                await owner._json(client, 'POST', ISSUER + '/token', body, token_headers=headers)
    finally:
        await owner.close()


async def test_owned_signer_cancellation_reaps_process_without_revealing_key(tmp_path, monkeypatch):
    import sys

    from xdog.agent.mcp import oauth_private_jwt
    helper = tmp_path / 'wait.py'
    marker = tmp_path / 'started'
    helper.write_text('import os,pathlib,sys,time\nassert "TEST_RSA_PEM" not in os.environ\n'
                      'pathlib.Path(sys.argv[1]).write_text("ready")\n'
                      'sys.stdin.buffer.read()\ntime.sleep(30)\n')
    process = []
    original = asyncio.create_subprocess_exec
    async def spawn(*args, **kwargs):
        child = await original(*args, **kwargs)
        process.append(child)
        return child
    monkeypatch.setattr(oauth_private_jwt, '_program', lambda: sys.executable)
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', spawn)
    task = asyncio.create_task(oauth_private_jwt._run([str(helper), str(marker)], b'PRIVATE-KEY-NEVER-PUBLIC'))
    async with asyncio.timeout(3):
        while not marker.exists():
            await asyncio.sleep(0.003)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(process) == 1 and process[0].returncode is not None
    assert 'PRIVATE-KEY' not in marker.read_text()


async def test_repeated_cancel_during_signer_spawn_keeps_process_owner_until_reaped(tmp_path, monkeypatch):
    import sys

    from xdog.agent.mcp import oauth_private_jwt
    ready, release = asyncio.Event(), asyncio.Event()
    children = []
    original = asyncio.create_subprocess_exec
    async def delayed(*args, **kwargs):
        child = await original(*args, **kwargs)
        children.append(child)
        ready.set()
        await release.wait()
        return child
    monkeypatch.setattr(oauth_private_jwt, '_program', lambda: sys.executable)
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', delayed)
    task = asyncio.create_task(oauth_private_jwt._run(['-c', 'import time;time.sleep(30)'], b'PRIVATE-PEM'))
    await asyncio.wait_for(ready.wait(), 3)
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done() and children[0].returncode is None
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 3)
    assert len(children) == 1 and children[0].returncode is not None


async def test_repeated_cancel_during_claim_descriptor_creation_always_closes_it(tmp_path, monkeypatch):
    import threading

    from xdog.agent.mcp import oauth_private_jwt
    ready, release = threading.Event(), threading.Event()
    files = []
    monkeypatch.setenv(VARIABLE, '-----BEGIN PRIVATE KEY-----\nDUMMY\n-----END PRIVATE KEY-----')
    async def validated(value):
        return None
    monkeypatch.setattr(oauth_private_jwt, '_validate_key', validated)
    original = oauth_private_jwt.tempfile.TemporaryFile
    def delayed(*args, **kwargs):
        file = original(*args, **kwargs)
        files.append(file)
        ready.set()
        release.wait(3)
        return file
    monkeypatch.setattr(oauth_private_jwt.tempfile, 'TemporaryFile', delayed)
    task = asyncio.create_task(oauth_private_jwt.assertion(VARIABLE, CLIENT, ISSUER + '/token'))
    async with asyncio.timeout(3):
        while not ready.is_set():
            await asyncio.sleep(0.003)
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done() and files and not files[0].closed
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 3)
    assert len(files) == 1 and files[0].closed


async def test_consent_withdrawal_during_async_source_validation_cannot_open_callback(key, monkeypatch):
    from xdog.agent.mcp import oauth_client_auth
    live = [True]
    owner = OAuthOwner(RESOURCE, config())
    async def peer(request):
        if request.url.host == 'resource.test':
            return httpx.Response(200, json={'resource': RESOURCE, 'authorization_servers': [ISSUER]})
        return httpx.Response(200, json={'issuer': ISSUER, 'authorization_endpoint': ISSUER + '/authorize',
            'token_endpoint': ISSUER + '/token', 'code_challenge_methods_supported': ['S256'],
            'response_types_supported': ['code'], 'token_endpoint_auth_methods_supported': ['private_key_jwt'],
            'token_endpoint_auth_signing_alg_values_supported': ['RS256']})
    owner.guard = lambda: live[0]
    owner.client = lambda: httpx.AsyncClient(transport=httpx.MockTransport(peer))
    try:
        review = await owner.prepare()
        original = oauth_client_auth.validate_source
        async def withdrawn(*args):
            await original(*args)
            live[0] = False
        monkeypatch.setattr(oauth_client_auth, 'validate_source', withdrawn)
        async def forbidden(*args, **kwargs):
            pytest.fail('Retired async validation cannot open a callback listener')
        monkeypatch.setattr(asyncio, 'start_server', forbidden)
        with pytest.raises(OAuthError, match='source validation'):
            await owner.start(review['review_id'])
        assert owner.flow is None and owner.review is None
    finally:
        await owner.close()
