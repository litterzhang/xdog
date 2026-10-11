"""Host adapter uses real private worker OAuth, never transports credential records."""
from __future__ import annotations

import asyncio
import json

import pytest
from xdog.agent.extensions.provider_oauth_host import ProviderAuthHost, auth_catalog

from packages.agent.tests.test_extension_provider_oauth import ACCESS, CODE, PROVIDER, REFRESH, owner


async def test_host_command_catalog_and_explicit_full_review_private_worker_login(tmp_path):
    runtime, dialogs = await owner(tmp_path), []
    host = None
    async def ask(extension, dialog):
        dialogs.append(dialog)
        assert host.owns(dialog)
        return CODE if dialog['kind'] == 'input' else True
    host = ProviderAuthHost(runtime, ask, lambda: True)
    try:
        assert (await host('list'))['providers'] == list(runtime.provider_auth_catalog())
        assert not dialogs
        result = await host('login ' + PROVIDER)
        assert result['ok'] and result['authenticated']
        assert [row['provider_auth']['phase'] for row in dialogs] == ['consent', 'on_auth', 'on_prompt']
        assert not any(host.owns(row) for row in dialogs)
        assert all(secret not in json.dumps([dialogs, result, auth_catalog(runtime), runtime.snapshot()])
                   for secret in (CODE, ACCESS, REFRESH))
        assert await runtime.command('ext-provideroauth-count', '') == 'login=1;refresh=0;key=0;models=1;stream=0'
    finally:
        await runtime.aclose()


@pytest.mark.parametrize('arguments', ['', 'list', 'menu'])
async def test_catalog_or_canceled_menu_never_starts_default_auth(tmp_path, arguments):
    runtime = await owner(tmp_path)
    async def ask(extension, dialog):
        return 'Cancel'
    try:
        await ProviderAuthHost(runtime, ask, lambda: True)(arguments)
        assert await runtime.command('ext-provideroauth-count', '') == 'login=0;refresh=0;key=0;models=0;stream=0'
    finally:
        await runtime.aclose()


@pytest.mark.parametrize('action', ['save', 'restore', 'forget'])
async def test_remote_storage_denied_before_review_or_native_helper(tmp_path, action):
    runtime, dialogs = await owner(tmp_path), []
    async def ask(extension, dialog):
        dialogs.append(dialog)
        return True
    try:
        with pytest.raises(ValueError, match='native-only'):
            await ProviderAuthHost(runtime, ask, lambda: True)(
                f'{action} {PROVIDER} secret-service {tmp_path}')
        assert not dialogs
    finally:
        await runtime.aclose()


async def test_host_revoke_while_sensitive_prompt_waits_settles_original_worker_and_withholds_catalog(tmp_path):
    runtime = await owner(tmp_path)
    held = asyncio.Event()
    async def ask(extension, dialog):
        if dialog['kind'] == 'input':
            held.set()
            await asyncio.Future()
        return True
    host = ProviderAuthHost(runtime, ask, lambda: True)
    work = asyncio.create_task(host('login ' + PROVIDER))
    try:
        await asyncio.wait_for(held.wait(), 5)
        host.revoke()
        with pytest.raises(ValueError):
            await asyncio.wait_for(work, 5)
        assert not runtime.provider_auth_catalog() and not runtime._provider_lock.locked()
    finally:
        await runtime.aclose()
        await asyncio.gather(work, return_exceptions=True)


@pytest.mark.parametrize('action', ['save', 'restore', 'forget'])
async def test_native_storage_requires_separate_exact_backend_namespace_review_and_denial_is_inert(tmp_path, action):
    runtime, dialogs = await owner(tmp_path), []
    namespace = tmp_path / 'not-created'
    async def ask(extension, dialog):
        dialogs.append(dialog)
        return False
    try:
        output = await ProviderAuthHost(runtime, ask, lambda: True, native_storage=True)(
            f'{action} {PROVIDER} secret-service {namespace}')
        assert output['ok'] is False and len(dialogs) == 1
        review = dialogs[0]['provider_auth']
        assert review['phase'] == 'consent' and review['action'] == action
        assert review['storage'] == {'backend': 'secret-service', 'namespace': str(namespace)}
        assert not namespace.exists()
    finally:
        await runtime.aclose()


@pytest.mark.parametrize('arguments', ['one two', 'ext-provider extra', 'ext-provider logout', '"', '""', '--native'])
def test_login_alias_extra_or_invalid_tokens_do_not_become_actions(arguments):
    from xdog.agent.extensions.provider_oauth_host import login_arguments
    with pytest.raises(ValueError, match='Usage'):
        login_arguments(arguments)


def test_login_alias_is_extension_only_and_requires_explicit_selection():
    from xdog.agent.extensions.provider_oauth_host import login_arguments
    assert login_arguments('') == 'menu'
    assert login_arguments(PROVIDER) == 'login ' + PROVIDER


async def test_modified_public_review_cannot_authorize_original_action_even_with_own_lease(tmp_path):
    runtime = await owner(tmp_path)
    host = None
    async def ask(extension, dialog):
        assert host.owns(dialog)
        dialog['provider_auth']['action'] = 'logout'
        assert not host.owns(dialog)
        return True
    host = ProviderAuthHost(runtime, ask, lambda: True)
    try:
        with pytest.raises(ValueError):
            await host('login ' + PROVIDER)
        assert await runtime.command('ext-provideroauth-count', '') == 'login=0;refresh=0;key=0;models=0;stream=0'
    finally:
        await runtime.aclose()
