import json
from dataclasses import replace

import httpx
import pytest
from xdog.eyes.models import HttpSourceDef
from xdog.eyes.sources.http import HttpTransport, Response, backoff
from xdog.eyes.sources.remote import RemoteSampler


class Transport(HttpTransport):
    def __init__(self, handler):
        super().__init__()
        self._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_json_projection_conditional_baseline_304_and_invalid_shape():
    requests = []
    responses = [httpx.Response(200, json={'items': [{'id': 'a', 'text': '中文'}]}, headers={'etag': 'v1'}),
                 httpx.Response(304), httpx.Response(200, json={'wrong': []}, headers={'etag': 'bad'})]
    def handle(request):
        requests.append(request)
        return responses.pop(0)
    sampler = RemoteSampler(Transport(handle))
    source = HttpSourceDef(id='s', url='https://example.test/items', pointer='/items', key_pointer='/id')
    try:
        first = await sampler.poll(source, {}, now=1)
        assert first.snapshot.valid and first.snapshot.records[0].key == 'a'
        assert '中文' in first.snapshot.records[0].value and first.state['etag'] == 'v1'
        same = await sampler.poll(source, first.state, now=61)
        assert same.snapshot.unchanged and same.snapshot.healthy
        assert requests[1].headers['if-none-match'] == 'v1'
        failed = await sampler.poll(source, same.state, now=121)
        assert not failed.snapshot.valid and not failed.snapshot.healthy
        assert failed.state['etag'] == 'v1' and failed.state['last_success'] == 61
        assert failed.state['failures'] == 1
    finally:
        await sampler.close()


async def test_http_status_is_read_only_without_body_or_latency_noise():
    responses = [httpx.Response(500, text='secret irrelevant body'), httpx.Response(200), httpx.Response(200)]
    sampler = RemoteSampler(Transport(lambda request: responses.pop(0)))
    source = HttpSourceDef(id='s', url='https://example.test/health', mode='status')
    try:
        failed = await sampler.poll(source, {}, now=0)
        assert not failed.snapshot.healthy and failed.snapshot.valid
        assert json.loads(failed.snapshot.records[0].value) == {'status_code': 500}
        second = await sampler.poll(source, failed.state, now=60)
        third = await sampler.poll(source, second.state, now=120)
        assert second.snapshot.records == third.snapshot.records
        assert second.state['failures'] == 0
    finally:
        await sampler.close()


@pytest.mark.parametrize('body', [b'[{"id":1},{"id":1}]', b'{"x":1,"x":2}', b'NaN', b'not-json',
                                 b'[{"no_id":1}]'])
async def test_invalid_json_never_becomes_an_empty_snapshot(body):
    sampler = RemoteSampler(Transport(lambda request: httpx.Response(200, content=body)))
    source = HttpSourceDef(id='s', url='https://example.test', key_pointer='/id')
    try:
        result = await sampler.poll(source, {'last_success': 1}, now=2)
        assert not result.snapshot.valid and result.state['last_success'] == 1
    finally:
        await sampler.close()


async def test_oversize_body_is_error_not_truncated_baseline():
    sampler = RemoteSampler(Transport(lambda request: httpx.Response(200, content=b'"'+b'x'*100+b'"')))
    try:
        result = await sampler.poll(HttpSourceDef(id='s', url='https://example.test', max_bytes=16), {}, now=1)
        assert not result.snapshot.valid and 'max_bytes' in result.snapshot.error
        assert 'last_success' not in result.state
    finally:
        await sampler.close()


@pytest.mark.parametrize('target', ['https://other.test/data', 'http://example.test/data',
                                     'https://user:password@example.test/data'])
async def test_redirect_never_forwards_bearer_to_other_origins(monkeypatch, target):
    monkeypatch.setenv('EYES_TEST_SECRET', 'my-private-token')
    seen = []
    def handle(request):
        seen.append(request)
        return httpx.Response(302, headers={'location': target})
    sampler = RemoteSampler(Transport(handle))
    try:
        result = await sampler.poll(HttpSourceDef(id='s', url='https://example.test/data',
                                                 bearer_env='EYES_TEST_SECRET'), {}, now=1)
        assert not result.snapshot.valid and len(seen) == 1
        assert 'my-private-token' not in repr(result)
    finally:
        await sampler.close()


async def test_secrets_and_library_errors_never_enter_checkpoints(monkeypatch):
    monkeypatch.setenv('EYES_TEST_SECRET', 'my-private-token')
    responses = [httpx.Response(200, json={'echo': 'my-private-token'})]
    def handle(request):
        if responses:
            return responses.pop()
        raise httpx.ConnectError('my-private-token and private URL', request=request)
    sampler = RemoteSampler(Transport(handle))
    source = HttpSourceDef(id='s', url='https://example.test', bearer_env='EYES_TEST_SECRET')
    try:
        success = await sampler.poll(source, {}, now=1)
        assert 'my-private-token' not in repr(success)
        failure = await sampler.poll(source, success.state, now=2)
        assert 'my-private-token' not in repr(failure) and 'private URL' not in repr(failure)
        insecure = await sampler.poll(replace(source, url='http://example.test'), {}, now=3)
        assert 'HTTPS' in insecure.snapshot.error
    finally:
        await sampler.close()


async def test_rate_limit_retry_is_honored_and_first_304_is_rejected():
    values = [httpx.Response(429, headers={'retry-after': '600'}), httpx.Response(304)]
    sampler = RemoteSampler(Transport(lambda request: values.pop(0)))
    try:
        source = HttpSourceDef(id='s', url='https://example.test')
        limited = await sampler.poll(source, {}, now=1)
        assert limited.delay == 600 and 601 <= limited.state['next_poll_at'] <= 602
        stale = await sampler.poll(source, {}, now=2)
        assert not stale.snapshot.valid and not stale.snapshot.unchanged
        assert backoff(Response(403, {'x-ratelimit-remaining': '0', 'x-ratelimit-reset': '1200'}, b''),
                       now=1000, attempt=0) == 200
    finally:
        await sampler.close()


async def test_transport_timeout_is_a_failure_fact_without_losing_baseline():
    import asyncio
    async def hang(request):
        await asyncio.sleep(10)
        return httpx.Response(200)
    sampler = RemoteSampler(Transport(hang))
    try:
        result = await sampler.poll(HttpSourceDef(id='s', url='https://example.test', timeout_seconds=.01),
                                    {'last_success': 1}, now=2)
        assert not result.snapshot.valid and result.state['last_success'] == 1
    finally:
        await sampler.close()


async def test_retry_after_longer_than_one_day_is_not_clamped_early():
    sampler = RemoteSampler(Transport(lambda request: httpx.Response(429, headers={'retry-after': '172800'})))
    try:
        result = await sampler.poll(HttpSourceDef(id='s', url='https://example.test'), {}, now=1)
        assert result.delay >= 172800
    finally:
        await sampler.close()


@pytest.mark.parametrize('secret', ['token\r\nInjected: yes', 'token"quoted', 'token\\escaped'])
async def test_invalid_bearer_values_never_reach_transport_or_checkpoints(monkeypatch, secret):
    monkeypatch.setenv('EYES_INVALID_TOKEN', secret)
    def forbidden(request):
        pytest.fail('Invalid bearer must be rejected before IO')
    sampler = RemoteSampler(Transport(forbidden))
    try:
        result = await sampler.poll(HttpSourceDef(id='s', url='https://example.test',
                                                 bearer_env='EYES_INVALID_TOKEN'), {}, now=1)
        assert not result.snapshot.valid and secret not in repr(result)
    finally:
        await sampler.close()


async def test_shared_connection_pool_never_shares_implicit_cookie_authority(monkeypatch):
    seen = []
    original = httpx.AsyncClient
    def handle(request):
        seen.append(request)
        return httpx.Response(200, json={'ok': True}, headers={'set-cookie': 'session=private-cookie; Path=/'})
    def client(**kwargs):
        return original(**kwargs, transport=httpx.MockTransport(handle))
    monkeypatch.setattr('xdog.eyes.sources.http.httpx.AsyncClient', client)
    monkeypatch.setenv('EYES_PRIVATE_TOKEN', 'private-token')
    sampler = RemoteSampler()
    try:
        await sampler.poll(HttpSourceDef(id='private', url='https://example.test/private',
                                         bearer_env='EYES_PRIVATE_TOKEN'), {}, now=1)
        result = await sampler.poll(HttpSourceDef(id='public', url='https://example.test/public'), {}, now=2)
        assert result.snapshot.valid and len(seen) == 2
        assert seen[0].headers['authorization'] == 'Bearer private-token'
        assert 'authorization' not in seen[1].headers and 'cookie' not in seen[1].headers
        assert 'private-cookie' not in repr(result)
    finally:
        await sampler.close()
