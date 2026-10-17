"""Characterize both native consent publishers before sharing pure reply validation."""
from __future__ import annotations

import copy
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode


def review():
    return {'review_id': 'a' * 32, 'issuer': 'https://issuer.invalid',
            'authorization_endpoint': 'https://issuer.invalid/authorize',
            'token_endpoint': 'https://issuer.invalid/token', 'resource': 'https://resource.invalid',
            'client_id': 'explicit', 'scopes': ['tools:read']}


def launch():
    fields = {'response_type': 'code', 'client_id': 'explicit', 'resource': 'https://resource.invalid',
              'scope': 'tools:read', 'code_challenge_method': 'S256', 'state': 's' * 43,
              'code_challenge': 'c' * 43, 'redirect_uri': 'http://127.0.0.1:12345/callback'}
    return {'review_id': 'a' * 32, 'authorization_url': 'https://issuer.invalid/authorize?' + urlencode(fields)}


def reply(case):
    data, cached, error, valid = review(), review(), '', True
    if case == 'prepare':
        action = 'auth_prepare'
    elif case == 'refresh':
        action, data = 'auth_refresh_prepare', {**data, 'operation': 'refresh'}
    elif case.startswith('storage-'):
        operation = 'credentials_' + case.removeprefix('storage-')
        action, data = 'auth_' + operation + '_prepare', {**data, 'operation': operation, 'storage_notice': 'explicit'}
    elif case == 'status':
        action, data = 'auth_status', {'review': data}
    elif case == 'launch':
        action, data = 'auth_start', launch()
    elif case == 'passthrough':
        action, data = 'auth_cancel', {'state': 'cancelled'}
    elif case == 'reported-error':
        action, data, error = 'auth_start', None, 'operation failed'
    elif case == 'bad-data':
        action, data, valid = 'auth_prepare', None, False
    elif case == 'bad-review':
        action, data, valid = 'auth_prepare', {**data, 'token_endpoint': 'https://other.invalid/token'}, False
    elif case == 'bad-refresh':
        action, data, valid = 'auth_refresh_prepare', {**data, 'operation': 'login'}, False
    elif case == 'bad-storage-notice':
        action, data, valid = 'auth_credentials_save_prepare', {**data, 'operation': 'credentials_save'}, False
    elif case == 'bad-launch':
        action, data, valid = 'auth_start', {**launch(), 'review_id': 'b' * 32}, False
    else:
        assert case == 'missing-review'
        action, data, cached, valid = 'auth_start', launch(), None, False
    return action, data, cached, error, valid


@pytest.mark.parametrize('kind', [InteractiveMode, ChatApp], ids=['coding', 'claw'])
@pytest.mark.parametrize('case', ['prepare', 'refresh', 'storage-save', 'storage-restore', 'storage-forget',
                                  'status', 'launch', 'passthrough', 'reported-error', 'bad-data', 'bad-review',
                                  'bad-refresh', 'bad-storage-notice', 'bad-launch', 'missing-review'])
def test_native_consent_response_contract(kind, case):
    action, data, cached, error, valid = reply(case)
    before = copy.deepcopy(data)
    published, detached, rendered = [], [], []
    host = SimpleNamespace(_mcp_owner_context='original', _mcp_context_key=lambda: 'original',
        _mcp_auth_review=cached,
        _mcp_panel=SimpleNamespace(publish_auth=lambda *args: published.append(args)),
        _tui=SimpleNamespace(request_render=lambda: rendered.append(True)))
    def detach():
        detached.append(True)
        host._mcp_auth_review = None
    host._detach_mcp_auth = detach
    host._close_mcp = lambda: pytest.fail('Current response must not retire a successor context')
    kind._mcp_publish(host, action, data, error)
    assert len(published) == 1 and rendered == [True] and data == before
    published_action, normalized, published_error = published[0]
    assert published_action == action
    if valid:
        assert not detached and published_error == error
        if case in {'prepare', 'refresh', 'storage-save', 'storage-restore', 'storage-forget'}:
            assert normalized is host._mcp_auth_review and normalized['tls_trust'] == 'system'
        else:
            assert host._mcp_auth_review is cached
        if case == 'status':
            assert normalized['review']['tls_trust'] == 'system'
        if case == 'reported-error':
            assert normalized == {}
    else:
        assert detached == [True] and host._mcp_auth_review is None
        assert normalized == {} and published_error


@pytest.mark.parametrize('kind', [InteractiveMode, ChatApp], ids=['coding', 'claw'])
def test_unknown_removed_control_cannot_mutate_view_or_cached_review(kind):
    host = SimpleNamespace(_mcp_auth_review=review())
    # Must return before even reading native panel/context attributes.
    kind._mcp_publish(host, 'watch_resource', None, 'retired')
    assert host._mcp_auth_review == review()
