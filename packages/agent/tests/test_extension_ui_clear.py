from __future__ import annotations

import pytest
from xdog.agent.extensions.api import ExtensionAPI
from xdog.agent.extensions.runtime import ExtensionRuntime


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
