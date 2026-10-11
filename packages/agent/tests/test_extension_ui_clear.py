from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from xdog.agent.extensions.api import ExtensionAPI
from xdog.agent.extensions.runtime import ExtensionRuntime


async def test_real_worker_bootstrap_clear_collision_reload_and_old_owner_ui_are_scoped(tmp_path):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parent / 'fixtures/extensions', root)
    runtime = ExtensionRuntime(['uiclear'], root, tmp_path)
    events = []
    runtime.publish = events.append
    try:
        await runtime.initialize()
        client = runtime._manager._clients['uiclear']
        assert runtime.snapshot()['ui']['uiclear:setup']['lines'] == ['BOOTSTRAP-LIVE']
        assert await runtime.command('ext-uiclear-show', '') == 'UI-SHOWN'
        assert 'uiclear:setup' not in runtime.snapshot()['ui']
        assert set(runtime.snapshot()['ui']) == {'uiclear:state', 'uiclear:details'}
        assert await runtime.command('ext-uiclear-hide', '') == 'UI-CLEARED'
        assert not runtime.snapshot()['ui']
        assert events[-1]['type'] == 'extension_ui' and not events[-1]['snapshot']['ui']
        await runtime.command('ext-uiclear-collide', '')
        assert runtime.snapshot()['ui']['uiclear:same']['kind'] == 'widget'
        await runtime.manage('reload', 'uiclear')
        new = runtime._manager._clients['uiclear']
        assert new is not client
        runtime.accept_ui('uiclear', [{'kind': 'widget', 'key': 'setup', 'lines': ['STALE']}], client=client)
        runtime.accept_ui('uiclear', [{'kind': 'status', 'key': 'setup', 'lines': [], 'clear': True}], client=client)
        assert runtime.snapshot()['ui']['uiclear:setup']['lines'] == ['BOOTSTRAP-LIVE']
        await runtime.aclose()
        runtime.accept_ui('uiclear', [{'kind': 'widget', 'key': 'late', 'lines': ['STALE']}], client=new)
        assert 'uiclear:late' not in runtime.snapshot()['ui']
    finally:
        await runtime.aclose()


def test_clear_api_copies_values_and_does_not_implicitly_treat_empty_widget_as_removal():
    api = ExtensionAPI({})
    lines = ['original']
    api.widget('key', lines)
    lines.append('later mutation')
    api.widget('empty', [])
    api.clear_widget('key')
    api.status('state', None)
    rows = api.drain_ui()
    assert rows[0]['lines'] == ['original']
    assert rows[1] == {'kind': 'widget', 'key': 'empty', 'lines': []}
    assert rows[2] == {'kind': 'widget', 'key': 'key', 'lines': [], 'clear': True}
    assert rows[3]['clear'] is True


@pytest.mark.parametrize('changes', [{'clear': 1}, {'clear': True, 'lines': ['not empty']},
    {'kind': 'notice', 'clear': True}, {'kind': 'unknown'}, {'key': 'bad:key'}, {'callback': 'not wire data'}])
def test_invalid_clear_batch_is_atomic_and_cannot_delete_other_namespaces(tmp_path, changes):
    runtime = ExtensionRuntime([], tmp_path, tmp_path)
    runtime.accept_ui('one', [{'kind': 'status', 'key': 'keep', 'lines': ['one']}])
    runtime.accept_ui('two', [{'kind': 'status', 'key': 'keep', 'lines': ['two']}])
    before = runtime.snapshot()['ui']
    with pytest.raises(ValueError):
        runtime.accept_ui('one', [{'kind': 'status', 'key': 'keep', 'lines': [], 'clear': True},
                                 {'kind': 'widget', 'key': 'next', 'lines': [], **changes}])
    assert runtime.snapshot()['ui'] == before
    runtime.accept_ui('one', [{'kind': 'status', 'key': 'keep', 'lines': [], 'clear': True}])
    assert 'two:keep' in runtime.snapshot()['ui'] and 'one:keep' not in runtime.snapshot()['ui']
