"""UI016 list-only child queue inspection never borrows Agent/SDK mutation authority."""
from __future__ import annotations

import copy
import inspect

import pytest
from xdog.tui.components.child_queue_panel import ChildQueuePanel
from xdog.tui.keys import KeyEvent, KeyEventType
from xdog.tui.utils import strip_ansi, visible_width


def data():
    return {'child_id': 'child', 'revision': 'a' * 64, 'inputs': [
        {'id': 'input', 'content': 'ORIGINAL ' + 'x' * 2000 + ' FULL-END',
         'delivery': 'steer', 'selected': False, 'image_count': 0}]}


def make():
    sent, back = [], []
    panel = ChildQueuePanel('child', lambda: sent.append({'action': 'list'}), lambda: back.append(True))
    panel.focused = True
    panel.publish(data())
    panel.set_render_budget(9)
    return panel, sent, back


@pytest.mark.parametrize('key', ['e', 'x', 'd', 'u', 'j', 'k', 'delete'])
@pytest.mark.parametrize('stopped', [False, True])
def test_retired_queue_management_keys_and_paste_cannot_dispatch_or_edit(key, stopped):
    panel, sent, _ = make()
    panel.publish({**data(), 'stopped': stopped})
    before = copy.deepcopy(panel._rows)
    for event_type in (KeyEventType.PRESS, KeyEventType.REPEAT, KeyEventType.RELEASE):
        panel.render(80)
        panel.handle_input(KeyEvent(key, event_type=event_type))
        panel.handle_paste('LOCAL-UNSUBMITTED /quit\n')
        panel.handle_input(KeyEvent('enter', event_type=event_type))
    assert not sent and panel._rows == before
    for name in ('_input', '_edit', '_review', '_confirm', '_draft', '_pending_return', '_sent_action',
                 'send', 'returned'):
        assert not hasattr(panel, name)


def test_enter_is_full_readonly_inspection_and_scroll_never_confirms_an_operation():
    panel, sent, back = make()
    panel.handle_input(KeyEvent('enter'))
    painted = []
    for _ in range(100):
        painted.extend(panel.render(80))
        panel.handle_input(KeyEvent('pagedown'))
    source = '\n'.join(painted)
    assert 'FULL-END' in source and 'Inspection only' in source and 'no recovery' in source
    assert not sent and not back and panel._rows == data()['inputs']
    panel.handle_input(KeyEvent('escape'))
    assert not panel._details and not back
    panel.handle_input(KeyEvent('escape'))
    assert back == [True] and panel._closed and not sent


def test_refresh_is_explicit_list_only_coalesced_and_errors_do_not_edit_the_snapshot():
    panel, sent, _ = make()
    before = copy.deepcopy(panel._rows)
    panel.handle_input(KeyEvent('r', event_type=KeyEventType.REPEAT))
    panel.handle_input(KeyEvent('r', event_type=KeyEventType.RELEASE))
    panel.handle_paste('r\n')
    assert not sent
    panel.handle_input(KeyEvent('r'))
    panel.handle_input(KeyEvent('r'))
    assert sent == [{'action': 'list'}] and panel._busy
    panel.fail('Owner checkpoint changed; no replay\x1b[31m')
    assert not panel._busy and panel._rows == before and '\x1b' not in panel._error
    panel.handle_input(KeyEvent('r'))
    assert sent == [{'action': 'list'}, {'action': 'list'}]


def test_blur_withdrawal_and_retired_owner_cannot_send_fresh_reads_or_adopt_late_data():
    panel, sent, _ = make()
    panel.focused = False
    panel.refresh()
    panel.handle_input(KeyEvent('r'))
    panel.focused = True
    panel.enabled = False
    panel.refresh()
    assert not sent
    panel.enabled = True
    panel.handle_input(KeyEvent('r'))
    assert sent == [{'action': 'list'}]
    panel.close()
    panel.enabled = panel.focused = True  # Stale flags cannot resurrect a retired inspector.
    panel.refresh()
    panel.publish(data())
    panel.fail('Late stale failure')
    assert not panel._rows and len(sent) == 1 and panel.render(80) == []


@pytest.mark.parametrize('field', ['returned', 'recovered', 'image_receipt', 'return_prepared', 'return_token',
                                  'image_recovery', 'images'])
def test_mutation_return_and_stopped_recovery_receipts_are_refused_without_adoption(field):
    panel, sent, _ = make()
    before = copy.deepcopy(panel._rows)
    with pytest.raises(ValueError, match='list metadata only'):
        panel.publish({**data(), 'stopped': True, field: data()['inputs'][0]})
    assert panel._rows == before and not sent


@pytest.mark.parametrize('source', ['initial-task', 'crash-snapshot', 'durable-return'])
def test_stopped_uncertain_selected_and_attachment_metadata_remain_inspectable_without_recovery(source):
    panel, sent, _ = make()
    row = {**data()['inputs'][0], 'content': 'STORED UNSENT /quit', 'source': source,
           'uncertain': True, 'selected': True, 'image_count': 2}
    panel.publish({**data(), 'stopped': True, 'inputs': [row]})
    panel.set_render_budget(20)
    text = '\n'.join(panel.render(160))
    assert source in text and 'UNCERTAIN' in text and '2 attachments' in text and 'selected' in text
    for key in ('u', 'x', 'e', 'j', 'k'):
        panel.handle_input(KeyEvent(key))
    panel.handle_input(KeyEvent('enter'))
    inspected = []
    for _ in range(20):
        inspected.extend(panel.render(100))
        panel.handle_input(KeyEvent('pagedown'))
    assert source in '\n'.join(inspected) and not sent and panel._rows == [row]


@pytest.mark.parametrize('change', ['child', 'revision', 'duplicate', 'content', 'selected', 'count',
                                    'source', 'stopped', 'delivery-type', 'source-type'])
def test_invalid_scope_shape_and_revision_cannot_replace_current_metadata(change):
    panel, sent, _ = make()
    value = data()
    if change == 'child':
        value['child_id'] = 'foreign'
    elif change == 'revision':
        value['revision'] = 'z' * 64
    elif change == 'duplicate':
        value['inputs'].append(dict(value['inputs'][0]))
    elif change == 'content':
        value['inputs'][0]['content'] = 'x' * 16001
    elif change == 'selected':
        value['inputs'][0]['selected'] = 1
    elif change == 'count':
        value['inputs'][0]['image_count'] = True
    elif change == 'source':
        value['inputs'][0].update(source='initial-task', uncertain=True)
    elif change == 'delivery-type':
        value['inputs'][0]['delivery'] = []
    elif change == 'source-type':
        value['stopped'] = True
        value['inputs'][0].update(source=[], uncertain=True)
    else:
        value['stopped'] = 1
    before = copy.deepcopy(panel._rows)
    with pytest.raises(ValueError):
        panel.publish(value)
    assert panel._rows == before and not sent


def test_snapshot_is_copied_atomically_and_selection_tracks_id_across_refresh():
    panel, sent, _ = make()
    value = data()
    value['inputs'].append({**value['inputs'][0], 'id': 'second'})
    panel.publish(value)
    panel.handle_input(KeyEvent('down'))
    value['inputs'][1]['content'] = 'LATER SOURCE MUTATION'
    assert panel._rows[1]['content'] != value['inputs'][1]['content']
    panel.publish({**data(), 'inputs': list(reversed(panel._rows))})
    assert panel._rows[panel._selected]['id'] == 'second' and not sent


@pytest.mark.parametrize('width,budget', [(0, 8), (1, 8), (8, 2), (24, 0), (24, 3), (60, 10)])
def test_tiny_unicode_control_text_clipping_and_paste_never_gain_mutation_authority(width, budget):
    panel, sent, _ = make()
    panel.publish({**data(), 'inputs': [{**data()['inputs'][0], 'content': '宽 🐕\n\x1b[31m/quit'}]})
    panel.set_render_budget(budget)
    panel.handle_input(KeyEvent('enter'))
    for key in ('x', 'e', 'u', 'j', 'k', 'end', 'home', 'enter'):
        panel.handle_input(KeyEvent(key))
        rendered = panel.render(width)
        assert len(rendered) <= budget and all(visible_width(row) <= width for row in rendered)
        assert all('\x1b' not in strip_ansi(row) for row in rendered)
    panel.handle_paste('\n/quit')
    assert not sent


def test_readonly_api_has_no_action_callback_or_binary_publish_parameter():
    panel, sent, _ = make()
    assert list(inspect.signature(ChildQueuePanel).parameters) == ['child', 'refresh', 'back']
    assert list(inspect.signature(panel.publish).parameters) == ['value']
    for name in ('image_recovery_enabled', 'task_recovery_enabled', 'image_return_enabled'):
        assert not hasattr(panel, name)
        setattr(panel, name, True)  # Obsolete assignments cannot add a dispatch path.
    for key in ('u', 'x', 'k', 'j', 'e'):
        panel.handle_input(KeyEvent(key))
    assert not sent


@pytest.mark.parametrize('over', ['', 'inputs', 'backups', 'initial'])
def test_full_stopped_metadata_capacity_and_invalid_sibling_refusal_remain_bounded(over):
    panel, sent, _ = make()
    prototype = {**data()['inputs'][0], 'content': 'Stored metadata', 'image_count': 1}
    inputs = [{**prototype, 'id': str(i), 'source': 'crash-snapshot', 'uncertain': True} for i in range(32)]
    backups = [{**prototype, 'id': 'return-' + str(i), 'source': 'durable-return', 'uncertain': True}
               for i in range(32)]
    initial = [{**prototype, 'id': 'initial', 'source': 'initial-task', 'uncertain': False}]
    if over == 'inputs':
        inputs.append({**prototype, 'id': 'overflow'})
    elif over == 'backups':
        backups.append({**backups[0], 'id': 'overflow'})
    elif over == 'initial':
        initial.append({**initial[0], 'id': 'overflow'})
    value = {**data(), 'stopped': True, 'inputs': inputs + backups + initial}
    if over:
        before = copy.deepcopy(panel._rows)
        with pytest.raises(ValueError):
            panel.publish(value)
        assert panel._rows == before
    else:
        panel.publish(value)
        assert len(panel._rows) == 65
        panel.handle_input(KeyEvent('end'))
        assert panel._rows[panel._selected]['id'] == 'initial'
    assert not sent
