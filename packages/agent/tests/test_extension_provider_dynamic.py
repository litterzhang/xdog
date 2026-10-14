from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from xdog.agent.extensions.api import ExtensionAPI
from xdog.agent.extensions.runtime import ExtensionRuntime


async def owner(tmp_path):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parent / 'fixtures/extensions', root)
    runtime = ExtensionRuntime(['providerdynamic'], root, tmp_path)
    await runtime.initialize()
    return runtime


def test_api_explicit_replace_and_old_disposer_cannot_remove_a_successor():
    api = ExtensionAPI({})
    models = [{'id': 'one', 'context_window': 10000, 'max_tokens': 1000}]
    def callback(*args):
        return None
    old = api.register_provider('local', models, callback)
    initial = api.providers['local'][0]['registration_id']
    with pytest.raises(ValueError):
        api.register_provider('LOCAL', models, callback)
    with pytest.raises(ValueError):
        api.register_provider('local', models, callback, replace=1)
    new = api.register_provider('LOCAL', models, callback, replace=True)
    assert api.providers['local'][0]['registration_id'] != initial
    revision = api._provider_revision
    old()
    assert api._provider_revision == revision and 'local' in api.providers
    new()
    assert api._provider_revision == revision + 1 and not api.providers
    new()
    assert api._provider_revision == revision + 1
    assert not api.unregister_provider('local')
