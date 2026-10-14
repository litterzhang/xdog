from __future__ import annotations

import asyncio
import json
import time

import pytest
from xdog.agent.extensions.api import ExtensionAPI
from xdog.agent.extensions.provider_auth import ProviderAuth
from xdog.agent.extensions.provider_oauth import (
    OAuthBinding,
    OAuthCallbacks,
    ProviderOAuth,
    authorization_prompt,
    authorization_url,
    credentials,
    public_oauth,
)
from xdog.agent.extensions.providers import provider_catalog, registration

ROW = {'id': 'echo', 'context_window': 32000, 'max_tokens': 1000}
TOKEN = {'access': 'private-access', 'refresh': 'private-refresh', 'expires': 2**40, 'account_id': 'private-id'}


@pytest.mark.parametrize('value', [None, {}, {**TOKEN, 'access': ''}, {**TOKEN, 'refresh': None},
    {**TOKEN, 'expires': True}, {**TOKEN, 'expires': float('nan')}, {**TOKEN, 'expires': -1},
    {**TOKEN, 'expires': 2**1000}, {**TOKEN, 'access': 'a\r\nb'}, {**TOKEN, 'extra': b'private'},
    {**TOKEN, 'extra': 'x' * 65536}])
def test_private_credential_records_are_bounded_and_never_coerced(value):
    with pytest.raises(ValueError):
        credentials(value)


def test_worker_and_host_reject_unsafe_auth_url_before_review() -> None:

    def _check_case(value):
        with pytest.raises(ValueError):
            authorization_url(value, "")

    for value in [
        "http://auth.test",
        "file:///tmp/token",
        "javascript:alert(1)",
        "https://user:password@auth.test",
        "https://auth.test\n.evil.test",
        "https://auth.test:invalid",
        "https://auth.test/\u202eprivate",
        "https://[invalid",
        None,
        {},
    ]:
        _check_case(value)


@pytest.mark.parametrize('value', ['', None, 'x' * 4001])
def test_authorization_prompt_has_bounded_nonempty_shape(value):
    with pytest.raises(ValueError):
        authorization_prompt(value)


@pytest.mark.parametrize('change', [{'authenticated': 1}, {'refresh_available': 'yes'},
    {'expires': float('inf')}, {'expires': 2**1000}, {'name': 'unsafe\x1b'}, {'access': 'private'}])
def test_public_auth_metadata_does_not_accept_private_fields_or_coerced_status(change):
    with pytest.raises(ValueError):
        public_oauth({'name': 'Local', 'authenticated': False, 'expires': 0, 'refresh_available': False, **change})


async def test_oauth_registration_is_lazy_atomic_and_old_disposer_cannot_revoke_replacement():
    api, calls = ExtensionAPI({}), []

    def callback(*args):
        calls.append(args)
        return None

    oauth = ProviderOAuth('Local', callback, callback, callback)
    old = api.register_provider('local', [ROW], callback, oauth=oauth)
    declaration, bound = api.providers['local']
    assert isinstance(bound, OAuthBinding) and not calls
    assert not bound.status()['authenticated']
    assert provider_catalog([declaration], live=True)[0]['oauth']['name'] == 'Local'
    original_revision = api._provider_revision
    with pytest.raises(ValueError):
        api.register_provider('local', [ROW], callback, replace=True, oauth=oauth, auth=ProviderAuth(env='EXPLICIT'))
    assert api._provider_revision == original_revision and api.providers['local'][1] is bound
    api.register_provider('local', [ROW], callback, oauth=oauth, replace=True)
    old()
    assert 'local' in api.providers and api.providers['local'][1] is not bound
    with pytest.raises(ValueError, match='retired'):
        await bound.control('login', {})
    assert not calls and 'private-access' not in repr(oauth)


async def test_get_key_and_modify_models_receive_copies_without_mutating_stored_grant():
    api = ExtensionAPI({})
    token = {**TOKEN, 'expires': (time.time() + 600) * 1000}
    delivered = []

    def key(value):
        assert value['refresh'] == token['refresh']
        value['refresh'] = 'modified-copy'
        return value['access']

    def models(value, rows):
        value['account_id'] = 'changed-copy'
        return [{**rows[0], 'context_window': 64000}]

    async def stream(model, context, options, credential):
        delivered.append(credential)
        yield {'type': 'done'}

    api.register_provider('local', [ROW], stream,
                          oauth=ProviderOAuth('Local', lambda callbacks: token, lambda value: value, key, models))
    bound = api.providers['local'][1]
    await bound.control('login', {})
    token['refresh'] = 'caller-mutated-original'
    assert bound._credentials['refresh'] == 'private-refresh'
    token['refresh'] = 'private-refresh'
    for _ in range(2):
        iterator = await bound({}, {}, {})
        assert await anext(iterator) == {'type': 'done'}
        await iterator.aclose()
    assert len(delivered) == 2 and bound._credentials['account_id'] == 'private-id'
    assert api.providers['local'][0]['models'][0]['context_window'] == 64000
    assert 'private-access' not in json.dumps(api.providers['local'][0])
    assert registration('local', [ROW])['models'][0]['context_window'] == 32000
    with pytest.raises(ValueError):
        registration('local', [])  # Legacy registration still requires declared models.


async def test_login_callback_step_budget_and_owner_fence_are_independent_of_host_ui():
    class API:
        async def _ask(self, *args, **kwargs):
            return True

    callbacks = OAuthCallbacks(API(), 'local', 'Local', lambda: True)
    for _ in range(16):
        await callbacks.on_auth('https://auth.test', '')
    with pytest.raises(ValueError, match='budget'):
        await callbacks.on_auth('https://auth.test', '')
    retired = OAuthCallbacks(API(), 'local', 'Local', lambda: False)
    with pytest.raises(ValueError, match='owner'):
        await retired.on_auth('https://auth.test', '')


async def test_late_get_key_result_cannot_use_replaced_credential_or_registration():
    api, entered, release = ExtensionAPI({}), asyncio.Event(), asyncio.Event()
    token = {**TOKEN, 'expires': (time.time() + 600) * 1000}
    calls = []

    async def key(value):
        entered.set()
        await release.wait()
        return value['access']

    def stream(*args):
        calls.append(args)

    api.register_provider('local', [ROW], stream,
                          oauth=ProviderOAuth('Local', lambda callbacks: token, lambda value: value, key))
    bound = api.providers['local'][1]
    await bound.control('login', {})
    task = asyncio.create_task(bound({}, {}, {}))
    await entered.wait()
    api.register_provider('local', [ROW], stream, replace=True)
    release.set()
    with pytest.raises(ValueError, match='credentials changed'):
        await task
    assert not calls
