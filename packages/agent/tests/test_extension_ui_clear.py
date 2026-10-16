from __future__ import annotations

import pytest
from xdog.agent.extensions.api import ExtensionAPI
from xdog.agent.extensions.runtime import ExtensionRuntime


def test_simple_status_clear_and_notify_remain_without_widget_registration():
    api = ExtensionAPI({})
    assert not hasattr(api, 'widget') and not hasattr(api, 'clear_widget')
    api.status('key', 'original')
    api.status('empty', '')
    api.clear_status('key')
    api.notify('notice')
    rows = api.drain_ui()
    assert rows[0] == {'kind': 'status', 'key': 'key', 'lines': ['original']}
    assert rows[1] == {'kind': 'status', 'key': 'empty', 'lines': ['']}
    assert rows[2] == {'kind': 'status', 'key': 'key', 'lines': [], 'clear': True}
    assert rows[3] == {'kind': 'notice', 'key': '', 'lines': ['notice']}


@pytest.mark.parametrize('changes', [{'clear': 1}, {'clear': True, 'lines': ['not empty']},
    {'kind': 'notice', 'clear': True}, {'kind': 'unknown'}, {'key': 'bad:key'}, {'callback': 'not wire data'}])
def test_invalid_clear_batch_is_atomic_and_cannot_delete_other_namespaces(tmp_path, changes):
    runtime = ExtensionRuntime([], tmp_path, tmp_path)
    runtime.accept_ui('one', [{'kind': 'status', 'key': 'keep', 'lines': ['one']}])
    runtime.accept_ui('two', [{'kind': 'status', 'key': 'keep', 'lines': ['two']}])
    before = runtime.snapshot()['ui']
    with pytest.raises(ValueError):
        runtime.accept_ui('one', [{'kind': 'status', 'key': 'keep', 'lines': [], 'clear': True},
                                 {'kind': 'status', 'key': 'next', 'lines': [], **changes}])
    assert runtime.snapshot()['ui'] == before
    runtime.accept_ui('one', [{'kind': 'status', 'key': 'keep', 'lines': [], 'clear': True}])
    assert 'two:keep' in runtime.snapshot()['ui'] and 'one:keep' not in runtime.snapshot()['ui']


def test_worker_widget_transport_and_snapshot_are_rejected(tmp_path):
    from xdog.agent.extensions.runtime import validate_snapshot
    runtime = ExtensionRuntime([], tmp_path, tmp_path)
    with pytest.raises(ValueError):
        runtime.accept_ui('one', [{'kind': 'widget', 'key': 'layout', 'lines': ['text']}])
    assert runtime.snapshot()['ui'] == {}
    with pytest.raises(ValueError):
        validate_snapshot({'extensions': [], 'commands': {},
                           'ui': {'one:layout': {'kind': 'widget', 'lines': ['text']}}})
