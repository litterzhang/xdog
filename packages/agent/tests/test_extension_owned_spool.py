"""Actual extension transfer/provider callers must own late spool resources."""
from __future__ import annotations

import asyncio
import tempfile
import threading
from types import SimpleNamespace

import pytest
import xdog.agent.extensions.runtime as runtime_module
import xdog.agent.extensions.transfer as transfer_module


def provider_owner():
    owner = object.__new__(runtime_module.ExtensionRuntime)
    async def initialize():
        pass
    owner.initialize = initialize
    profile = object()
    owner.provider_model = lambda identifier: profile
    owner._closed, owner._generation = False, 0
    owner._infos = {'test': {'providers': [{'name': 'fake', 'registration_id': 'registered',
                                          'models': [{'id': 'model'}]}]}}
    owner._manager = SimpleNamespace(_clients={'test': object()})
    return owner


@pytest.mark.parametrize('kind', ['context', 'provider'])
@pytest.mark.parametrize('fails', [False, True])
async def test_repeated_cancel_joins_actual_spool_and_closes_late_file(monkeypatch, kind, fails):
    entered, release, exited = threading.Event(), threading.Event(), threading.Event()
    files = []
    def held_spool(value):
        file = tempfile.TemporaryFile()
        files.append(file)
        entered.set()
        try:
            assert release.wait(5)
            if fails:
                raise ValueError('late spool failure')
            return file, 0, '0' * 64
        except BaseException:
            file.close()  # The real spool closes its own construction failure too.
            raise
        finally:
            exited.set()
    monkeypatch.setattr(transfer_module, 'spool', held_spool)
    before = asyncio.all_tasks()
    stream = None
    owner = provider_owner() if kind == 'provider' else object.__new__(runtime_module.ExtensionRuntime)
    if kind == 'provider':
        stream = owner.provider_events('ext-test-fake/model', {})
        caller = asyncio.create_task(anext(stream))
    else:
        caller = asyncio.create_task(owner._transfer_value('test', {}))
    workers = []
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        workers = [task for task in asyncio.all_tasks() - before
                   if task is not caller and task.get_coro().__name__ == 'to_thread']
        assert len(workers) == 1
        for _ in range(3):
            caller.cancel()
            await asyncio.sleep(0)
        assert not caller.done(), 'Caller released ownership before its real spool worker settled'
        assert not exited.is_set()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await caller
        assert exited.is_set() and files[0].closed, 'Late resource must close before canceled owner settles'
    finally:
        release.set()
        await asyncio.gather(caller, *workers, return_exceptions=True)
        if stream is not None:
            await stream.aclose()
        for file in files:
            file.close()


class TrackedFile:
    def __init__(self, file):
        self.file, self.close_calls = file, 0

    def __getattr__(self, name):
        return getattr(self.file, name)

    def close(self):
        self.close_calls += 1
        self.file.close()


def track_spools(monkeypatch):
    original = transfer_module.spool
    files = []
    def tracked(value):
        file, size, checksum = original(value)
        wrapper = TrackedFile(file)
        files.append(wrapper)
        return wrapper, size, checksum
    monkeypatch.setattr(transfer_module, 'spool', tracked)
    return files


@pytest.mark.parametrize('outcome', ['normal', 'body-error', 'explicit-close'])
async def test_owned_spool_preserves_manifest_and_closes_once(monkeypatch, outcome):
    import hashlib
    import json
    files = track_spools(monkeypatch)
    failure = ValueError('consumer failure')
    async def consume():
        async with transfer_module.owned_spool({'content': '中文'}) as (file, size, checksum):
            body = file.read()
            assert len(body) == size and hashlib.sha256(body).hexdigest() == checksum
            assert json.loads(body) == {'content': '中文'} and not file.closed
            if outcome == 'body-error':
                raise failure
            if outcome == 'explicit-close':
                file.close()
    if outcome == 'body-error':
        with pytest.raises(ValueError) as raised:
            await consume()
        assert raised.value is failure
    else:
        await consume()
    assert len(files) == 1 and files[0].closed and files[0].close_calls == 1


async def test_owned_spool_preserves_entry_failure_without_retry(monkeypatch):
    calls = []
    failure = ValueError('spool failed')
    def fail(value):
        calls.append(value)
        raise failure
    monkeypatch.setattr(transfer_module, 'spool', fail)
    with pytest.raises(ValueError) as raised:
        async with transfer_module.owned_spool('bad'):
            pytest.fail('Failed spool must not enter consumer body')
    assert raised.value is failure and calls == ['bad']


@pytest.mark.parametrize('fail_upload', [False, True])
async def test_actual_context_transfer_retains_checksum_chunks_and_cleanup(monkeypatch, fail_upload):
    files = track_spools(monkeypatch)
    owner = object.__new__(runtime_module.ExtensionRuntime)
    remote = transfer_module.ContextTransfer()
    methods = []
    payload = {'content': 'x' * (transfer_module.BLOCK + 100)}
    async def rpc(name, method, params):
        methods.append(method)
        if method == 'xdog/context_begin':
            remote.begin(params)
            return {}
        if method == 'xdog/context_chunk':
            if fail_upload:
                raise OSError('upload failure')
            remote.chunk(params)
            return {}
        if method == 'xdog/context_finish':
            assert params['operation'] == 'context'
            return remote.result({'echo': remote.finish(params)})
        if method == 'xdog/context_read':
            return remote.read(params)
        assert method == 'xdog/context_close'
        remote.close()
        return {}
    owner._rpc = rpc
    try:
        if fail_upload:
            with pytest.raises(OSError, match='upload failure'):
                await owner._transfer_value('test', payload)
        else:
            assert await owner._transfer_value('test', payload) == {'echo': payload}
            assert methods.count('xdog/context_chunk') == 2
            assert methods[-1] == 'xdog/context_close'
        assert len(files) == (1 if fail_upload else 2)  # Host upload plus the echo server's output spool.
        assert all(file.closed and file.close_calls == 1 for file in files)
    finally:
        remote.close()
        for file in files:
            if not file.closed:
                file.close()


@pytest.mark.parametrize('outcome', ['normal', 'upload-error', 'consumer-close'])
async def test_actual_provider_upload_closes_file_before_stream_close_and_releases_lock(monkeypatch, outcome):
    import base64
    import hashlib
    files = track_spools(monkeypatch)
    owner = provider_owner()
    owner._provider_lock = asyncio.Lock()
    async def bridge(coroutine):
        return await coroutine
    owner._manager._bridge, owner._manager._closed = bridge, False
    methods, blocks = [], []
    manifest = {}
    events = iter([{'type': 'text', 'text': 'hello'}, {'type': 'done', 'stop_reason': 'stop'}])
    async def rpc(name, method, params):
        methods.append(method)
        if method == 'xdog/provider_begin':
            manifest.update(params)
        elif method == 'xdog/provider_chunk':
            assert params['id'] == manifest['id'] and params['index'] == len(blocks)
            if outcome == 'upload-error':
                raise OSError('provider upload failed')
            blocks.append(base64.b64decode(params['data']))
        elif method == 'xdog/provider_start':
            body = b''.join(blocks)
            assert len(body) == manifest['bytes'] and hashlib.sha256(body).hexdigest() == manifest['sha256']
            assert params['chunks'] == len(blocks) and params['registration_id'] == 'registered'
            return {'id': 'a' * 32}
        elif method == 'xdog/provider_next':
            return {'event': next(events)}
        elif method == 'xdog/provider_close':
            assert files[0].closed, 'Preserve file-close-before-provider-close ordering'
        else:
            pytest.fail('Unexpected RPC: ' + method)
        return {}
    owner._rpc = rpc
    stream = owner.provider_events('ext-test-fake/model', {'prompt': 'x' * (transfer_module.BLOCK + 100)})
    try:
        if outcome == 'upload-error':
            with pytest.raises(OSError, match='provider upload failed'):
                await anext(stream)
            assert 'xdog/provider_close' not in methods
        elif outcome == 'consumer-close':
            assert await anext(stream) == {'type': 'text', 'text': 'hello'}
            await stream.aclose()
            assert methods[-1] == 'xdog/provider_close'
        else:
            assert [event async for event in stream] == [
                {'type': 'text', 'text': 'hello'}, {'type': 'done', 'stop_reason': 'stop'}]
            assert methods[-1] == 'xdog/provider_close'
        assert files[0].closed and files[0].close_calls == 1 and not owner._provider_lock.locked()
    finally:
        await stream.aclose()
        for file in files:
            if not file.closed:
                file.close()
