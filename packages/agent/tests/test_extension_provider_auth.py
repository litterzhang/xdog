from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import pytest
from xdog.agent.extensions.api import ExtensionAPI
from xdog.agent.extensions.provider_auth import ProviderAuth, ProviderCredential
from xdog.agent.extensions.providers import provider_catalog
from xdog.agent.extensions.runtime import ExtensionRuntime

ALIAS = 'ext-providerauth-local/echo'
SECRET = 'offline-ui044-sentinel-not-a-real-credential'


def test_auth_binding_requires_one_explicit_valid_source() -> None:

    def _check_case(kwargs):
        with pytest.raises(ValueError):
            ProviderAuth(**kwargs)

    for kwargs in [
        {},
        {"env": ""},
        {"env": "bad source"},
        {"env": "X" * 129},
        {"env": True},
        {"env": "SOURCE", "resolver": lambda: "value"},
        {"resolver": "literal-secret"},
        {"env": "SOURCE", "kind": "unknown"},
    ]:
        _check_case(kwargs)


def test_credential_values_are_bounded_nonempty_ascii_without_header_controls() -> None:

    def _check_case(secret):
        with pytest.raises(ValueError, match="Invalid provider credential"):
            ProviderCredential("bearer", secret)

    for secret in [None, "", True, {}, "a b", "a\r\nb", "a\x00b", "\ud800", "x" * 8193]:
        _check_case(secret)


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
