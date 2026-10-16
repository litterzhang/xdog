"""Host adapter uses real private worker OAuth, never transports credential records."""
from __future__ import annotations

import pytest

from packages.agent.tests.test_extension_provider_oauth import PROVIDER


def test_login_alias_extra_or_invalid_tokens_do_not_become_actions() -> None:

    def _check_case(arguments):
        from xdog.agent.extensions.provider_oauth_host import login_arguments

        with pytest.raises(ValueError, match="Usage"):
            login_arguments(arguments)

    for arguments in ["one two", "ext-provider extra", "ext-provider logout", '"', '""', "--native"]:
        _check_case(arguments)


def test_login_alias_is_extension_only_and_requires_explicit_selection():
    from xdog.agent.extensions.provider_oauth_host import login_arguments
    assert login_arguments('') == 'menu'
    assert login_arguments(PROVIDER) == 'login ' + PROVIDER


async def test_thin_host_routes_explicit_callbacks_once_and_keeps_credentials_worker_private(tmp_path):
    import json

    from xdog.agent.extensions.provider_oauth_host import ProviderAuthHost
    from xdog.agent.extensions.providers import ExtensionProviderStream
    from xdog.ai.types import Context, StreamOptions

    from packages.agent.tests.test_extension_provider_oauth import ACCESS, ALIAS, CODE, REFRESH, owner

    runtime = await owner(tmp_path)
    dialogs = []
    host = None

    async def ask(provider, dialog):
        assert provider == PROVIDER and host.owns(dialog)
        assert not host.owns({**dialog, 'provider_auth_host': 'foreign'})
        dialogs.append(dialog)
        return CODE if dialog['kind'] == 'input' else True

    host = ProviderAuthHost(runtime, ask, lambda: True)
    try:
        assert (await host(''))['providers'] == (await host('list'))['providers']
        assert not dialogs
        assert await runtime.command('ext-provideroauth-count', '') == 'login=0;refresh=0;key=0;models=0;stream=0'
        for action in ('login', 'refresh', 'logout'):
            before = len(dialogs)
            result = await host(action + ' ' + PROVIDER)
            assert result['ok'] is True and result['authenticated'] is (action != 'logout')
            assert len(dialogs) - before == (3 if action == 'login' else 1)
            assert dialogs[before]['provider_auth']['phase'] == 'consent'
            assert dialogs[before]['provider_auth']['action'] == action
            assert all(not host.owns(dialog) for dialog in dialogs)
        adapter = ExtensionProviderStream(runtime, lambda *args: pytest.fail('No native-provider fallback'))
        assert (await adapter(ALIAS, Context(), StreamOptions()).collect())[-1].type == 'error'
        assert await runtime.command('ext-provideroauth-count', '') == 'login=1;refresh=1;key=0;models=2;stream=0'
        public = json.dumps(runtime.snapshot()) + json.dumps(runtime._infos) + repr(dialogs) + repr(result)
        assert all(private not in public for private in (ACCESS, REFRESH, CODE, 'private-account'))
        assert not runtime._provider_lock.locked()
    finally:
        await runtime.aclose()


@pytest.mark.parametrize('answer', [False, None])
async def test_thin_host_declined_consent_never_invokes_callback_or_implicit_alternate(tmp_path, answer):
    from xdog.agent.extensions.provider_oauth_host import ProviderAuthHost

    from packages.agent.tests.test_extension_provider_oauth import owner

    runtime = await owner(tmp_path)
    dialogs = []

    async def ask(provider, dialog):
        dialogs.append(dialog)
        return answer

    host = ProviderAuthHost(runtime, ask, lambda: True)
    try:
        result = await host('login ' + PROVIDER)
        assert result['ok'] is False and len(dialogs) == 1
        assert await runtime.command('ext-provideroauth-count', '') == 'login=0;refresh=0;key=0;models=0;stream=0'
        assert not host.owns(dialogs[0]) and not runtime._provider_lock.locked()
    finally:
        await runtime.aclose()


async def test_env_provider_without_optional_oauth_cannot_acquire_management_or_storage_authority(
        tmp_path, monkeypatch):
    from xdog.agent.extensions.provider_oauth_host import ProviderAuthHost

    from packages.agent.tests.test_extension_provider_auth import owner

    runtime = await owner(tmp_path, monkeypatch)

    async def forbidden(*args):
        pytest.fail('No registered OAuth callback may borrow host authentication dialogs')

    host = ProviderAuthHost(runtime, forbidden, lambda: True, native_storage=True)
    try:
        assert (await host('list'))['providers'] == []
        for action in ('login', 'refresh', 'logout'):
            with pytest.raises(ValueError, match='identity'):
                await host(action + ' ext-providerauth-local')
        with pytest.raises(ValueError, match='identity'):
            await host('save ext-providerauth-local secret-service ' + str(tmp_path / 'vault'))
        assert await runtime.command('ext-providerauth-count', '') == 'CALLS=0;RESOLUTIONS=0'
        assert not (tmp_path / 'vault').exists()
    finally:
        await runtime.aclose()


async def test_revoked_thin_host_cancels_inflight_worker_login_without_adopting_or_replaying(tmp_path):
    import asyncio
    import json

    from xdog.agent.extensions.provider_oauth_host import ProviderAuthHost
    from xdog.agent.extensions.providers import ExtensionProviderStream
    from xdog.ai.types import Context, StreamOptions

    from packages.agent.tests.test_extension_provider_oauth import ACCESS, ALIAS, CODE, REFRESH, owner

    marker = tmp_path / 'login-entered'
    runtime = await owner(tmp_path, {'hold_login': True, 'marker': str(marker)})

    async def ask(provider, dialog):
        return CODE if dialog['kind'] == 'input' else True

    host = ProviderAuthHost(runtime, ask, lambda: True)
    pending = asyncio.create_task(host('login ' + PROVIDER))
    try:
        async with asyncio.timeout(5):
            while not marker.exists():
                await asyncio.sleep(.005)
        host.revoke()
        with pytest.raises(ValueError, match='no replay'):
            await asyncio.wait_for(pending, 5)
        assert not host._busy and not runtime._provider_lock.locked()
        assert not any(row['authenticated'] for row in runtime.provider_auth_catalog())
        adapter = ExtensionProviderStream(runtime, lambda *args: pytest.fail('No native-provider fallback'))
        assert (await adapter(ALIAS, Context(), StreamOptions()).collect())[-1].type == 'error'
        public = json.dumps(runtime.snapshot()) + json.dumps(runtime._infos)
        assert all(value not in public for value in (ACCESS, REFRESH, CODE))
    finally:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        await runtime.aclose()
