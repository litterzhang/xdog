"""Retired SDK entry points have no aliases or automatic data conversion."""
from importlib.util import find_spec

import pytest
from xdog.agent.extensions.providers import provider_catalog


def test_removed_projection_and_snapshot_aliases_are_not_exposed():
    from importlib import import_module
    agent = import_module('xdog.agent.agent')
    agent_loop = import_module('xdog.agent.agent_loop')
    snapshots = import_module('xdog.agent.snapshots')

    assert not hasattr(agent, '_default_convert_to_llm')
    assert not hasattr(agent_loop, '_default_convert')
    assert not hasattr(snapshots, 'decode_legacy_snapshot')
    assert not hasattr(snapshots, 'LegacySnapshot')
    assert find_spec('xdog.agent.external_event') is None


@pytest.mark.parametrize('token', [None, '', 'old-identity', True])
def test_provider_catalog_requires_current_registration_identity(token):
    entry = {'name': 'provider', 'models': [{'id': 'model', 'context_window': 100, 'max_tokens': 10}]}
    if token is not None:
        entry['registration_id'] = token
    with pytest.raises(ValueError, match='registration identity'):
        provider_catalog([entry])
