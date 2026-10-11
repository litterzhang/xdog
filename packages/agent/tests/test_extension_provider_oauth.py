from __future__ import annotations

import asyncio
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


@pytest.mark.parametrize('phase', ['on_auth', 'on_prompt'])
async def test_cancelled_review_quarantines_uncertain_login_without_public_credentials_or_model_fallback(
        tmp_path, phase):
    runtime = await owner(tmp_path)
    process = runtime._manager._clients['provideroauth'].process

    async def ask(request):
        return None if request['phase'] == phase else True

    try:
        with pytest.raises(ValueError, match='no replay'):
            await runtime.provider_auth_control(PROVIDER, 'login', ask=ask, alive=lambda: True)
        assert process.returncode is not None and not runtime.provider_models()
        assert not runtime._provider_lock.locked()
    finally:
        await runtime.aclose()


@pytest.mark.parametrize('retire', ['cancel', 'host', 'worker'])
async def test_pending_prompt_original_owner_withdrawal_cancels_and_reaps_before_return(tmp_path, retire):
    runtime = await owner(tmp_path)
    entered = asyncio.Event()
    live = [True]
    process = runtime._manager._clients['provideroauth'].process

    async def ask(request):
        if request['phase'] == 'on_prompt':
            entered.set()
            await asyncio.Event().wait()
        return True

    task = asyncio.create_task(runtime.provider_auth_control(PROVIDER, 'login', ask=ask, alive=lambda: live[0]))
    try:
        async with asyncio.timeout(5):
            await entered.wait()
            if retire == 'cancel':
                task.cancel()
            elif retire == 'host':
                live[0] = False
            else:
                await runtime.manage('unload', 'provideroauth')
            with pytest.raises((ValueError, asyncio.CancelledError)):
                await task
        assert process.returncode is not None and not runtime.provider_models() and not runtime._provider_lock.locked()
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await runtime.aclose()


@pytest.mark.parametrize('settings', [{'bad_models': True}, {'withdraw_login': True}])
async def test_invalid_modified_catalog_or_registration_withdrawal_cannot_publish_credentials(tmp_path, settings):
    runtime = await owner(tmp_path, settings)

    async def ask(request):
        return CODE if request['phase'] == 'on_prompt' else True

    try:
        with pytest.raises(ValueError, match='no replay'):
            await runtime.provider_auth_control(PROVIDER, 'login', ask=ask, alive=lambda: True)
        assert not runtime.provider_models()
    finally:
        await runtime.aclose()


async def test_expired_access_is_not_used_or_implicitly_refreshed_until_separate_review(tmp_path):
    runtime = await owner(tmp_path, {'expired': True})

    async def ask(request):
        return CODE if request['phase'] == 'on_prompt' else True

    try:
        await runtime.provider_auth_control(PROVIDER, 'login', ask=ask, alive=lambda: True)
        assert not runtime.provider_auth_catalog()[0]['authenticated']
        adapter = ExtensionProviderStream(runtime, lambda *args: pytest.fail('no fallback'))
        assert (await adapter(ALIAS, Context(), StreamOptions()).collect())[-1].type == 'error'
        assert await runtime.command('ext-provideroauth-count', '') == 'login=1;refresh=0;key=0;models=1;stream=0'
        await runtime.provider_auth_control(PROVIDER, 'refresh', ask=ask, alive=lambda: True)
        assert (await adapter(ALIAS, Context(), StreamOptions()).collect())[-1].type == 'done'
    finally:
        await runtime.aclose()


async def test_reviewed_auth_waiter_cannot_apply_to_a_new_credential_revision(tmp_path):
    runtime = await owner(tmp_path)
    entered, release, second_reviewed = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def first_ask(request):
        if request['phase'] == 'on_prompt':
            entered.set()
            while not release.is_set():
                await asyncio.sleep(.005)
            return CODE
        return True

    async def next_ask(request):
        second_reviewed.set()
        return True

    first = asyncio.create_task(runtime.provider_auth_control(PROVIDER, 'login', ask=first_ask, alive=lambda: True))
    second = None
    try:
        async with asyncio.timeout(5):
            await entered.wait()
            second = asyncio.create_task(runtime.provider_auth_control(
                PROVIDER, 'logout', ask=next_ask, alive=lambda: True))
            await second_reviewed.wait()
            release.set()
            assert (await first)['authenticated']
            with pytest.raises(ValueError, match='no replay'):
                await second
        assert runtime.provider_auth_catalog()[0]['authenticated'] and not runtime._provider_lock.locked()
        assert await runtime.command('ext-provideroauth-count', '') == 'login=1;refresh=0;key=0;models=1;stream=0'
    finally:
        for task in (first, second):
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        await runtime.aclose()


async def test_uncertain_refresh_error_clears_private_grant_and_never_replays_or_exposes_it(tmp_path):
    runtime = await owner(tmp_path, {'bad_refresh': True})
    process = runtime._manager._clients['provideroauth'].process

    async def ask(request):
        return CODE if request['phase'] == 'on_prompt' else True

    try:
        await runtime.provider_auth_control(PROVIDER, 'login', ask=ask, alive=lambda: True)
        with pytest.raises(ValueError, match='no replay') as error:
            await runtime.provider_auth_control(PROVIDER, 'refresh', ask=ask, alive=lambda: True)
        assert REFRESH not in str(error.value) and ACCESS not in str(error.value)
        assert process.returncode is not None and not runtime.provider_models()
        assert not runtime._provider_lock.locked()
    finally:
        await runtime.aclose()


async def test_authentication_callback_logs_entries_are_not_model_history_or_generic_ui(tmp_path):
    runtime = await owner(tmp_path, {'noisy_login': True})
    events = []
    runtime.publish = events.append

    async def ask(request):
        return CODE if request['phase'] == 'on_prompt' else True

    try:
        assert (await runtime.provider_auth_control(PROVIDER, 'login', ask=ask, alive=lambda: True))['authenticated']
        assert runtime.entries.for_extension('provideroauth') == []
        public = repr(events) + repr(runtime._ui)
        assert ACCESS not in public and REFRESH not in public
    finally:
        await runtime.aclose()


@pytest.mark.parametrize('retire', ['cancel', 'unload'])
async def test_worker_auth_poll_allows_native_cleanup_before_process_teardown(tmp_path, retire):
    marker, cleanup = tmp_path / 'entered', tmp_path / 'closed'
    runtime = await owner(tmp_path, {'slow_store_cleanup': True, 'marker': str(marker), 'cleanup_marker': str(cleanup)})
    process = runtime._manager._clients['provideroauth'].process

    async def ask(request):
        return CODE if request['phase'] == 'on_prompt' else True

    task = None
    try:
        await runtime.provider_auth_control(PROVIDER, 'login', ask=ask, alive=lambda: True)
        task = asyncio.create_task(runtime.provider_auth_control(PROVIDER, 'save', ask=ask, alive=lambda: True,
            storage={'backend': 'secret-service', 'namespace': str(tmp_path / 'not-created')}))
        async with asyncio.timeout(5):
            while not marker.exists():
                await asyncio.sleep(.005)
            if retire == 'cancel':
                task.cancel()
            else:
                await runtime.manage('unload', 'provideroauth')
            with pytest.raises((ValueError, asyncio.CancelledError)):
                await task
        assert cleanup.read_text() == 'closed' and process.returncode is not None
        assert not (tmp_path / 'not-created').exists() and not runtime.provider_models()
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await runtime.aclose()


async def test_credential_model_filter_can_hide_all_models_without_losing_auth_controls(tmp_path):
    runtime = await owner(tmp_path, {'empty_models': True})

    async def ask(request):
        return CODE if request['phase'] == 'on_prompt' else True

    try:
        await runtime.provider_auth_control(PROVIDER, 'login', ask=ask, alive=lambda: True)
        assert runtime.provider_auth_catalog()[0]['authenticated'] and not runtime.provider_models()
        adapter = ExtensionProviderStream(runtime, lambda *args: pytest.fail('no fallback'))
        assert (await adapter(ALIAS, Context(), StreamOptions()).collect())[-1].type == 'error'
        await runtime.provider_auth_control(PROVIDER, 'logout', ask=ask, alive=lambda: True)
        assert runtime.provider_model(ALIAS).context_window == 32000
    finally:
        await runtime.aclose()
