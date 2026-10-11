from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import json

import pytest
from xdog.agent.extensions.api import ExtensionAPI
from xdog.agent.extensions.provider_worker import ProviderCalls

ROW = {'id': 'echo', 'context_window': 1000, 'max_tokens': 100, 'reasoning': True,
       'supported_efforts': ['low'], 'input': ['text', 'image'], 'cost': {'input': 1},
       'vision_limits': {'max_prompt_images': 1, 'supported_media_types': ['image/png']}}


async def upload(worker, api, request=None):
    data = json.dumps({'context': {}, 'options': {}} if request is None else request).encode()
    await worker.request(api, 'xdog/provider_begin', {'id': 'transfer', 'bytes': len(data),
                                                    'sha256': hashlib.sha256(data).hexdigest()})
    await worker.request(api, 'xdog/provider_chunk', {'id': 'transfer', 'index': 0,
                                                    'data': base64.b64encode(data).decode()})
    return {'id': 'transfer', 'chunks': 1, 'provider': 'local', 'model': 'echo',
            'registration_id': api.providers['local'][0]['registration_id']}


class Iterator:
    def __init__(self, value):
        self.value = value
        self.closed = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if isinstance(self.value, BaseException):
            raise self.value
        return self.value

    async def aclose(self):
        self.closed += 1
        assert self.closed == 1


async def test_worker_gives_callback_deep_owned_metadata_not_live_nested_registration():
    api, worker = ExtensionAPI({}), ProviderCalls()
    original = copy.deepcopy(ROW)
    seen = []

    async def callback(model, context, options):
        seen.append(copy.deepcopy(model))
        model['cost']['input'] = 999
        model['supported_efforts'].append('high')
        model['input'].clear()
        model['vision_limits']['supported_media_types'].append('image/jpeg')
        return Iterator({'type': 'done'})

    api.register_provider('local', [ROW], callback)
    registered = copy.deepcopy(api.providers['local'][0])
    for _ in range(2):
        params = await upload(worker, api)
        token = await worker.request(api, 'xdog/provider_start', params)
        assert (await worker.request(api, 'xdog/provider_next', token))['event']['type'] == 'done'
    assert seen[0] == seen[1]
    assert api.providers['local'][0] == registered and ROW == original
    assert not worker.calls and not worker.counts and worker.transfer.file is None
    await worker.close()


@pytest.mark.parametrize('value', [StopAsyncIteration(), RuntimeError('private'), asyncio.CancelledError(),
                                  {'type': 'binary', 'data': b'private'}, {'type': 'usage', 'input': -1}])
async def test_worker_malformed_exhausted_or_cancelled_iterators_are_closed_exactly_once(value):
    api, worker = ExtensionAPI({}), ProviderCalls()
    iterator = Iterator(value)
    api.register_provider('local', [ROW], lambda *args: iterator)
    token = await worker.request(api, 'xdog/provider_start', await upload(worker, api))
    with pytest.raises((ValueError, RuntimeError, asyncio.CancelledError)):
        await worker.request(api, 'xdog/provider_next', token)
    assert iterator.closed == 1 and not worker.calls and not worker.counts
    await worker.request(api, 'xdog/provider_close', token)
    await worker.close()
    assert iterator.closed == 1


@pytest.mark.parametrize('count,size', [(4096, 0), (0, 8 * 1024 * 1024)])
async def test_worker_output_budget_failure_releases_iterator(count, size):
    api, worker = ExtensionAPI({}), ProviderCalls()
    iterator = Iterator({'type': 'text', 'text': 'hello'})
    api.register_provider('local', [ROW], lambda *args: iterator)
    token = await worker.request(api, 'xdog/provider_start', await upload(worker, api))
    worker.counts[token['id']] = count, size
    with pytest.raises(ValueError, match='budget'):
        await worker.request(api, 'xdog/provider_next', token)
    assert iterator.closed == 1 and not worker.calls and not worker.counts
    await worker.close()


@pytest.mark.parametrize('payload', [[], {'context': {}, 'options': {}, 'auth': 'private'},
                                     {'context': {}, 'options': []}, {'context': [], 'options': {}}])
async def test_worker_rejects_auth_injection_or_invalid_transfer_shape_before_callback(payload):
    api, worker = ExtensionAPI({}), ProviderCalls()
    api.register_provider('local', [ROW], lambda *args: pytest.fail('callback must not run'))
    with pytest.raises(ValueError, match='input/registration'):
        await worker.request(api, 'xdog/provider_start', await upload(worker, api, payload))
    assert worker.transfer.file is None and not worker.calls
    await worker.close()


async def test_failed_input_transfer_closes_spool_and_foreign_stream_close_cannot_destroy_upload():
    api, worker = ExtensionAPI({}), ProviderCalls()
    api.register_provider('local', [ROW], lambda *args: pytest.fail('callback must not run'))
    params = await upload(worker, api)
    spool = worker.transfer.file
    await worker.request(api, 'xdog/provider_close', {'id': 'foreign'})
    assert worker.transfer.file is spool and not spool.closed
    with pytest.raises(ValueError, match='checksum'):
        await worker.request(api, 'xdog/provider_start', {**params, 'chunks': 2})
    assert worker.transfer.file is None and spool.closed
    await worker.close()


async def test_worker_cannot_admit_stale_registration_or_concurrent_input():
    api, worker = ExtensionAPI({}), ProviderCalls()
    api.register_provider('local', [ROW], lambda *args: pytest.fail('callback must not run'))
    params = await upload(worker, api)
    with pytest.raises(ValueError, match='already owns'):
        await worker.request(api, 'xdog/provider_begin', {})
    api.register_provider('local', [ROW], lambda *args: pytest.fail('replacement must not run'), replace=True)
    with pytest.raises(ValueError, match='registration changed'):
        await worker.request(api, 'xdog/provider_start', params)
    assert worker.transfer.file is None and not worker.calls
    await worker.close()


@pytest.mark.parametrize('retire', ['replace', 'close', 'none'])
async def test_pending_admission_cannot_overlap_and_late_iterator_is_closed_if_owner_retired(retire):
    api, worker = ExtensionAPI({}), ProviderCalls()
    entered, release = asyncio.Event(), asyncio.Event()
    iterator = Iterator({'type': 'done'})

    async def callback(*args):
        entered.set()
        await release.wait()
        return iterator

    api.register_provider('local', [ROW], callback)
    params = await upload(worker, api)
    task = asyncio.create_task(worker.request(api, 'xdog/provider_start', params))
    await entered.wait()
    try:
        with pytest.raises(ValueError, match='already owns'):
            await worker.request(api, 'xdog/provider_begin', {})
        with pytest.raises(ValueError, match='already owns'):
            await worker.request(api, 'xdog/provider_start', params)
        if retire == 'replace':
            api.register_provider('local', [ROW], callback, replace=True)
        elif retire == 'close':
            await worker.close()
        release.set()
        if retire == 'none':
            token = await task
            assert (await worker.request(api, 'xdog/provider_next', token))['event']['type'] == 'done'
        else:
            with pytest.raises(ValueError, match='registration changed'):
                await task
        assert iterator.closed == 1 and not worker.calls and not worker.counts and not worker._starting
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await worker.close()


async def test_invalid_iterator_with_owned_async_close_is_not_leaked_during_admission():
    api, worker = ExtensionAPI({}), ProviderCalls()
    closed = []

    class InvalidIterator:
        async def aclose(self):
            closed.append(True)

    api.register_provider('local', [ROW], lambda *args: InvalidIterator())
    with pytest.raises(ValueError, match='iterator'):
        await worker.request(api, 'xdog/provider_start', await upload(worker, api))
    assert closed == [True] and not worker.calls and not worker.counts
    await worker.close()
