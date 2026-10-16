from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from xdog.agent.extensions.providers import ExtensionProviderStream
from xdog.agent.extensions.runtime import ExtensionRuntime
from xdog.ai.types import Context, StreamOptions

ALIAS = 'ext-provideroauth-local/echo'
PROVIDER = 'ext-provideroauth-local'
ACCESS = 'PRIVATE-OAUTH-ACCESS-SENTINEL'
REFRESH = 'PRIVATE-OAUTH-REFRESH-SENTINEL'
CODE = 'PRIVATE-OAUTH-CODE-SENTINEL'


async def owner(tmp_path, settings=None):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parent / 'fixtures/extensions/provideroauth', root / 'provideroauth')
    result = ExtensionRuntime(['provideroauth'], root, tmp_path, settings={'provideroauth': settings or {}})
    await result.initialize()
    return result


async def test_real_worker_login_prompt_refresh_get_key_modified_models_and_logout_are_private(tmp_path):
    runtime = await owner(tmp_path)
    reviews = []

    async def ask(request):
        reviews.append(request)
        return CODE if request['phase'] == 'on_prompt' else True

    adapter = ExtensionProviderStream(runtime, lambda *args: pytest.fail('no fallback'))
    try:
        assert runtime.provider_auth_catalog() == ({'id': PROVIDER, 'name': 'Offline OAuth',
            'authenticated': False, 'expires': 0, 'refresh_available': False},)
        assert runtime.provider_model(ALIAS).context_window == 32000
        assert await runtime.command('ext-provideroauth-count', '') == 'login=0;refresh=0;key=0;models=0;stream=0'
        missing = adapter(ALIAS, Context(), StreamOptions())
        assert (await missing.collect())[-1].type == 'error'
        logged = await runtime.provider_auth_control(PROVIDER, 'login', ask=ask, alive=lambda: True)
        assert logged['ok'] and logged['authenticated'] and logged['refresh_available']
        assert [review['phase'] for review in reviews] == ['consent', 'on_auth', 'on_prompt']
        assert runtime.provider_model(ALIAS).context_window == 64000
        stream = adapter(ALIAS, Context(), StreamOptions())
        assert (await stream.collect())[-1].type == 'done'
        refreshed = await runtime.provider_auth_control(PROVIDER, 'refresh', ask=ask, alive=lambda: True)
        assert refreshed['ok'] and refreshed['authenticated']
        next_stream = adapter(ALIAS, Context(), StreamOptions())
        assert (await next_stream.collect())[-1].type == 'done'
        assert await runtime.command('ext-provideroauth-count', '') == 'login=1;refresh=1;key=2;models=2;stream=2'
        public = json.dumps(runtime._infos) + repr(reviews) + repr(logged) + repr(refreshed)
        assert all(secret not in public for secret in (ACCESS, REFRESH, CODE, 'private-account'))
        await runtime.provider_auth_control(PROVIDER, 'logout', ask=ask, alive=lambda: True)
        assert not runtime.provider_auth_catalog()[0]['authenticated']
        assert runtime.provider_model(ALIAS).context_window == 32000
        unavailable = adapter(ALIAS, Context(), StreamOptions())
        assert (await unavailable.collect())[-1].type == 'error'
        assert not runtime._provider_lock.locked()
    finally:
        await runtime.aclose()


async def test_denial_before_login_does_not_call_worker_and_missing_or_foreign_owner_cannot_prompt(tmp_path):
    runtime = await owner(tmp_path)
    reviews = []

    async def deny(request):
        reviews.append(request)
        return False

    try:
        result = await runtime.provider_auth_control(PROVIDER, 'login', ask=deny, alive=lambda: True)
        assert result['ok'] is False and len(reviews) == 1
        assert await runtime.command('ext-provideroauth-count', '') == 'login=0;refresh=0;key=0;models=0;stream=0'
        with pytest.raises(ValueError):
            await runtime.provider_auth_control(PROVIDER, 'login', ask=deny, alive=lambda: False)
        with pytest.raises(ValueError):
            await runtime.provider_auth_control('ext-other-local', 'login', ask=deny, alive=lambda: True)
        assert len(reviews) == 1
        with pytest.raises(Exception):
            await runtime._manager._bridge(runtime._rpc(
                'provideroauth', 'xdog/provider_auth', {'provider': 'local', 'action': 'login'}))
    finally:
        await runtime.aclose()


@pytest.mark.parametrize('condition', ['expired', 'bad_refresh', 'withdraw_login'])
async def test_real_callback_failure_expiry_or_withdrawal_never_replays_or_falls_back(tmp_path, condition):
    runtime = await owner(tmp_path, {condition: True})
    reviews = []

    async def ask(request):
        reviews.append(request)
        return CODE if request['phase'] == 'on_prompt' else True

    adapter = ExtensionProviderStream(runtime, lambda *args: pytest.fail('No native-provider fallback'))
    try:
        if condition == 'withdraw_login':
            with pytest.raises(ValueError, match='no replay'):
                await runtime.provider_auth_control(PROVIDER, 'login', ask=ask, alive=lambda: True)
            assert runtime.provider_model(ALIAS) is None
        else:
            result = await runtime.provider_auth_control(PROVIDER, 'login', ask=ask, alive=lambda: True)
            assert result['ok'] and result['authenticated'] is (condition != 'expired')
            if condition == 'bad_refresh':
                with pytest.raises(ValueError, match='no replay'):
                    await runtime.provider_auth_control(PROVIDER, 'refresh', ask=ask, alive=lambda: True)
        count = len(reviews)
        for _ in range(2):
            events = await adapter(ALIAS, Context(), StreamOptions()).collect()
            assert events[-1].type == 'error'
        assert len(reviews) == count and not runtime._provider_lock.locked()
        assert not any(row['authenticated'] for row in runtime.provider_auth_catalog())
        public = json.dumps(runtime.snapshot()) + json.dumps(runtime._infos) + repr(events) + repr(reviews)
        assert all(value not in public for value in (ACCESS, REFRESH, CODE, 'private-account'))
        if condition == 'expired':
            assert await runtime.command('ext-provideroauth-count', '') == 'login=1;refresh=0;key=0;models=1;stream=0'
    finally:
        await runtime.aclose()
