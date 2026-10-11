from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import asdict
from pathlib import Path

import pytest
from xdog.agent.extensions.api import ExtensionAPI
from xdog.agent.extensions.provider_auth import ProviderAuth, ProviderCredential
from xdog.agent.extensions.providers import ExtensionProviderStream, provider_catalog
from xdog.agent.extensions.runtime import ExtensionRuntime
from xdog.ai.types import Context, StreamOptions

ALIAS = 'ext-providerauth-local/echo'
SECRET = 'offline-ui044-sentinel-not-a-real-credential'


@pytest.mark.parametrize('kwargs', [
    {}, {'env': ''}, {'env': 'bad source'}, {'env': 'X' * 129}, {'env': True},
    {'env': 'SOURCE', 'resolver': lambda: 'value'}, {'resolver': 'literal-secret'},
    {'env': 'SOURCE', 'kind': 'unknown'},
])
def test_auth_binding_requires_one_explicit_valid_source(kwargs):
    with pytest.raises(ValueError):
        ProviderAuth(**kwargs)


@pytest.mark.parametrize('secret', [None, '', True, {}, 'a b', 'a\r\nb', 'a\x00b', '\ud800', 'x' * 8193])
def test_credential_values_are_bounded_nonempty_ascii_without_header_controls(secret):
    with pytest.raises(ValueError, match='Invalid provider credential'):
        ProviderCredential('bearer', secret)


async def test_registration_is_lazy_secret_free_atomic_and_retains_legacy_three_argument_api(monkeypatch):
    monkeypatch.setenv('UI044_PRIVATE_SOURCE', SECRET)
    api = ExtensionAPI({})
    rows = [{'id': 'echo', 'context_window': 1000, 'max_tokens': 100}]
    calls = []

    async def callback(*args):
        calls.append(args)
        return None

    binding = ProviderAuth(env='UI044_PRIVATE_SOURCE')
    dispose = api.register_provider('local', rows, callback, auth=binding)
    info, bound = api.providers['local']
    assert not calls and SECRET not in repr(binding) and 'UI044_PRIVATE_SOURCE' not in repr(binding)
    catalog = provider_catalog([info], live=True)
    assert 'auth' not in info and 'credential' not in json.dumps(catalog) and SECRET not in json.dumps(catalog)
    revision = api._provider_revision
    with pytest.raises(ValueError):
        api.register_provider('local', rows, callback, auth={'token': SECRET}, replace=True)
    assert api._provider_revision == revision and api.providers['local'][1] is bound
    await bound({}, {}, {})
    assert calls[-1][3].value == SECRET and SECRET not in repr(calls[-1][3])
    api.register_provider('local', rows, callback, replace=True)
    dispose()
    assert api.providers['local'][1] is callback
    await api.providers['local'][1]({}, {}, {})
    assert len(calls[-1]) == 3
    with pytest.raises(ValueError, match='registration changed'):
        await bound({}, {}, {})
    assert len(calls) == 2


async def test_async_resolver_replacement_fences_original_callback_before_admission():
    api = ExtensionAPI({})
    rows = [{'id': 'echo', 'context_window': 1000, 'max_tokens': 100}]
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def resolver():
        entered.set()
        await release.wait()
        return SECRET

    def callback(*args):
        calls.append(args)

    api.register_provider('local', rows, callback, auth=ProviderAuth(resolver=resolver))
    task = asyncio.create_task(api.providers['local'][1]({}, {}, {}))
    await entered.wait()
    api.register_provider('local', rows, callback, replace=True)
    release.set()
    with pytest.raises(ValueError, match='registration changed'):
        await task
    assert not calls


async def owner(tmp_path, monkeypatch, settings=None, *, secret=SECRET):
    if secret is None:
        monkeypatch.delenv('XDOG_UI044_TEST_KEY', raising=False)
    else:
        monkeypatch.setenv('XDOG_UI044_TEST_KEY', secret)
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parent / 'fixtures/extensions', root)
    runtime = ExtensionRuntime(['providerauth'], root, tmp_path, settings={'providerauth': settings or {}})
    await runtime.initialize()
    return runtime


@pytest.mark.parametrize('env', [True, False])
@pytest.mark.parametrize('kind', ['bearer', 'api_key'])
async def test_real_worker_lazy_auth_rotation_replacement_and_public_catalog_separation(
        tmp_path, monkeypatch, env, kind):
    runtime = await owner(tmp_path, monkeypatch, {'env': env, 'kind': kind})
    adapter = ExtensionProviderStream(runtime, lambda *args: pytest.fail('no base fallback'))
    try:
        initial = runtime.provider_models()
        assert await runtime.command('ext-providerauth-count', '') == 'CALLS=0;RESOLUTIONS=0'
        public = json.dumps(runtime._infos) + repr(tuple(asdict(model) for model in adapter.models()))
        assert SECRET not in public and 'XDOG_UI044_TEST_KEY' not in public
        assert 'resolver' not in public
        for index in range(3):
            stream = adapter(ALIAS, Context(), StreamOptions())
            events = await stream.collect()
            assert events[-1].type == 'done'
            assert (await stream.result()).content[0].text == 'AUTHENTICATED-OFFLINE-ANSWER'
            assert SECRET not in repr(events)
            if index == 0:
                await runtime.command('ext-providerauth-rotate', '')
            elif index == 1:
                await runtime.command('ext-providerauth-replace', '')
            assert runtime.provider_models() == initial
        assert await runtime.command('ext-providerauth-count', '') == f'CALLS=3;RESOLUTIONS={0 if env else 3}'
        await runtime.manage('unload', 'providerauth')
        failed = adapter(ALIAS, Context(), StreamOptions())
        assert (await failed.collect())[-1].type == 'error'
        assert not runtime.provider_models()
    finally:
        await runtime.aclose()


@pytest.mark.parametrize('settings,secret', [({'env': True}, None), ({'env': True}, 'bad\nvalue'),
                                            ({'mode': 'fail'}, SECRET), ({}, None)])
async def test_real_auth_failure_is_public_generic_healthy_and_never_calls_stream_or_replays(
        tmp_path, monkeypatch, settings, secret):
    runtime = await owner(tmp_path, monkeypatch, settings, secret=secret)
    process = runtime._manager._clients['providerauth'].process
    try:
        stream = ExtensionProviderStream(runtime, lambda *args: pytest.fail('no fallback'))(
            ALIAS, Context(), StreamOptions())
        events = await stream.collect()
        message = await stream.result()
        assert events[-1].type == 'error' and message.error_message == 'Extension provider credentials unavailable'
        assert SECRET not in repr(events) and process.returncode is None and runtime.provider_models()
        expected = 'CALLS=0;RESOLUTIONS=' + ('0' if settings.get('env') else '1')
        assert await runtime.command('ext-providerauth-count', '') == expected
        assert not runtime._provider_lock.locked()
    finally:
        await runtime.aclose()


async def test_real_auth_withdrawal_during_resolution_does_not_invoke_old_callback(tmp_path, monkeypatch):
    runtime = await owner(tmp_path, monkeypatch, {'mode': 'withdraw'})
    try:
        stream = ExtensionProviderStream(runtime, lambda *args: pytest.fail('no fallback'))(
            ALIAS, Context(), StreamOptions())
        assert (await stream.collect())[-1].type == 'error'
        assert not runtime.provider_models()
    finally:
        await runtime.aclose()


async def test_cancel_during_real_auth_resolution_reaps_uncertain_worker_without_callback_or_secret(
        tmp_path, monkeypatch):
    marker = tmp_path / 'entered'
    runtime = await owner(tmp_path, monkeypatch, {'mode': 'hold', 'marker': str(marker)})
    process = runtime._manager._clients['providerauth'].process
    cancel = asyncio.Event()
    try:
        stream = ExtensionProviderStream(runtime, lambda *args: pytest.fail('no fallback'))(
            ALIAS, Context(), StreamOptions(cancel=cancel))
        task = asyncio.create_task(stream.collect())
        async with asyncio.timeout(5):
            while not marker.exists():
                await asyncio.sleep(0.003)
            cancel.set()
            events = await task
        assert events[-1].type == 'error' and events[-1].stop_reason == 'aborted'
        assert (await stream.result()).stop_reason == 'aborted' and SECRET not in repr(events)
        assert process.returncode is not None and not runtime.provider_models()
    finally:
        await runtime.aclose()


async def test_missing_auth_can_recover_only_on_explicit_new_admission_without_catalog_replacement(
        tmp_path, monkeypatch):
    runtime = await owner(tmp_path, monkeypatch, secret=None)
    adapter = ExtensionProviderStream(runtime, lambda *args: pytest.fail('no fallback'))
    try:
        snapshot = runtime._manager._clients['providerauth']._provider_snapshot
        stream = adapter(ALIAS, Context(), StreamOptions())
        assert (await stream.collect())[-1].type == 'error'
        assert await runtime.command('ext-providerauth-count', '') == 'CALLS=0;RESOLUTIONS=1'
        await runtime.command('ext-providerauth-rotate', '')
        assert runtime._manager._clients['providerauth']._provider_snapshot == snapshot
        assert await runtime.command('ext-providerauth-count', '') == 'CALLS=0;RESOLUTIONS=1'
        fresh = adapter(ALIAS, Context(), StreamOptions())
        assert (await fresh.collect())[-1].type == 'done'
        assert await runtime.command('ext-providerauth-count', '') == 'CALLS=1;RESOLUTIONS=2'
    finally:
        await runtime.aclose()


async def test_authenticated_callback_private_exception_is_masked_and_uncertain_worker_withdrawn(tmp_path, monkeypatch):
    runtime = await owner(tmp_path, monkeypatch, {'mode': 'callback_fail'})
    process = runtime._manager._clients['providerauth'].process
    try:
        stream = ExtensionProviderStream(runtime, lambda *args: pytest.fail('no fallback'))(
            ALIAS, Context(), StreamOptions())
        events = await stream.collect()
        assert events[-1].type == 'error' and SECRET not in repr(events)
        assert (await stream.result()).error_message == 'Extension provider failed; no fallback/replay'
        assert process.returncode is not None and not runtime.provider_models()
    finally:
        await runtime.aclose()
