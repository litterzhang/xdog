"""Pure reply normalization never commits consent or mutates the caller's review."""
from __future__ import annotations

import copy

import pytest
from xdog.agent.mcp.oauth import normalize_auth_reply


def review():
    return {'review_id': 'a' * 32, 'issuer': 'https://issuer.invalid',
            'authorization_endpoint': 'https://issuer.invalid/authorize',
            'token_endpoint': 'https://issuer.invalid/token', 'resource': 'https://resource.invalid',
            'client_id': 'explicit', 'scopes': ['tools:read']}


@pytest.mark.parametrize('operation', ['save', 'restore', 'forget'])
def test_storage_reply_requires_matching_explicit_operation(operation):
    cached = review()
    data = {**cached, 'operation': 'credentials_other', 'storage_notice': 'review me'}
    before = copy.deepcopy((data, cached))
    with pytest.raises(ValueError, match='explicit credential storage consent'):
        normalize_auth_reply(f'auth_credentials_{operation}_prepare', data, review=cached)
    assert (data, cached) == before


def test_normalized_review_is_a_new_value_not_a_mutation():
    data, cached = review(), review()
    before = copy.deepcopy((data, cached))
    normalized, updated = normalize_auth_reply('auth_prepare', data, review=cached)
    assert normalized is updated and updated is not data and updated is not cached
    assert updated['tls_trust'] == 'system'
    assert (data, cached) == before


@pytest.mark.parametrize('data', [None, (), {'unexpected': object()}])
def test_reported_error_preserves_existing_cache_without_validation(data):
    cached = review()
    normalized, updated = normalize_auth_reply('auth_start', data, review=cached, error='operation failed')
    assert updated is cached
    assert normalized is data if isinstance(data, dict) else normalized == {}


def test_status_review_is_normalized_without_becoming_new_consent():
    cached, supplied = review(), review()
    data = {'review': supplied, 'state': 'prepared'}
    normalized, updated = normalize_auth_reply('auth_status', data, review=cached)
    assert updated is cached and normalized is not data and normalized['review'] is not supplied
    assert normalized['review']['tls_trust'] == 'system' and 'tls_trust' not in supplied
