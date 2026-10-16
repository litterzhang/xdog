from __future__ import annotations

import pytest
from xdog.tui.components.child_queue_panel import ChildQueuePanel
from xdog.tui.keys import KeyEvent, KeyEventType


def data():
    return {'child_id': 'child', 'revision': 'a' * 64, 'inputs': [
        {'id': 'input', 'content': 'ORIGINAL ' + 'x' * 2000 + ' FULL-END',
         'delivery': 'steer', 'selected': False, 'image_count': 0}]}


def make():
    sent, drafts = [], []
    panel = ChildQueuePanel('child', sent.append, lambda: None, drafts.append)
    panel.focused = True
    panel.publish(data())
    panel.set_render_budget(9)
    return panel, sent, drafts


def review(panel):
    for _ in range(100):
        panel.render(80)
        if panel._visible:
            return
        panel.handle_input(KeyEvent('pagedown'))
    raise AssertionError('Child input review did not settle')


@pytest.mark.parametrize('key,action', [('x', 'remove'), ('u', 'return'), ('k', 'move')])
def test_child_queue_actions_require_full_specific_painted_physical_review(key, action):
    panel, sent, _ = make()
    panel.render(80)
    panel.handle_input(KeyEvent(key))
    panel.handle_input(KeyEvent('enter'))
    panel.handle_paste('\n')
    panel.handle_input(KeyEvent('enter', event_type=KeyEventType.REPEAT))
    assert not sent
    panel.set_render_budget(2)
    panel.render(20)
    panel.handle_input(KeyEvent('enter'))
    assert not sent
    panel.set_render_budget(9)
    review(panel)
    panel.handle_input(KeyEvent('enter'))
    assert sent[0]['action'] == action and sent[0]['id'] == 'input' and sent[0]['revision'] == 'a' * 64


def test_failed_edit_preserves_local_draft_and_focus_without_submission():
    panel, sent, _ = make()
    panel.render(80)
    panel.handle_input(KeyEvent('e'))
    panel.render(80)
    panel._input.set_text('LOCAL-UNSUBMITTED /quit')
    panel.handle_input(KeyEvent('enter'))
    review(panel)
    panel.handle_input(KeyEvent('enter'))
    assert sent[0]['content'] == 'LOCAL-UNSUBMITTED /quit'
    panel.fail('Checkpoint already consumed input; no replay')
    assert panel._mode == 'edit' and panel._input.get_text() == 'LOCAL-UNSUBMITTED /quit'
    panel.focused = False
    assert not panel._input.focused
    panel.focused = True
    assert panel._input.focused and len(sent) == 1


def test_only_exact_reviewed_removal_receipt_recovers_child_draft():
    panel, sent, drafts = make()
    with pytest.raises(ValueError, match='match'):
        panel.publish({**data(), 'returned': data()['inputs'][0]})
    panel.render(80)
    panel.handle_input(KeyEvent('u'))
    review(panel)
    panel.handle_input(KeyEvent('enter'))
    assert not drafts and sent[0]['action'] == 'return'
    panel.publish({**data(), 'inputs': [], 'revision': 'b' * 64, 'returned': data()['inputs'][0]})
    assert drafts == [data()['inputs'][0]]


def test_selected_and_unnegotiated_queues_cannot_prepare_mutations():
    panel, sent, _ = make()
    panel.publish({**data(), 'inputs': [{**data()['inputs'][0], 'selected': True}]})
    panel.render(80)
    panel.handle_input(KeyEvent('x'))
    assert panel._confirm is None and not sent
    panel.enabled = False
    panel.handle_input(KeyEvent('u'))
    assert panel._confirm is None


def test_capability_withdrawal_after_paint_cannot_dispatch_review_or_consume_edit_draft():
    panel, sent, _ = make()
    panel.render(80)
    panel.handle_input(KeyEvent('x'))
    review(panel)
    panel.enabled = False
    panel.handle_input(KeyEvent('enter'))
    assert not sent
    panel.handle_input(KeyEvent('escape'))
    panel.enabled = True
    panel.render(80)
    panel.handle_input(KeyEvent('e'))
    panel._input.set_text('LOCAL-KEEP')
    panel.render(80)
    panel.enabled = False
    panel.handle_input(KeyEvent('enter'))
    assert panel._input.get_text() == 'LOCAL-KEEP' and not sent


def test_stopped_recovery_is_reviewed_non_destructive_copy_with_distinct_receipt():
    panel, sent, drafts = make()
    panel.publish({**data(), 'stopped': True})
    panel.render(80)
    panel.handle_input(KeyEvent('u'))
    review(panel)
    panel.handle_input(KeyEvent('enter'))
    assert sent[0]['action'] == 'recover' and not drafts
    panel.publish({**data(), 'stopped': True, 'recovered': data()['inputs'][0]})
    assert drafts == [{**data()['inputs'][0], 'stopped': True}]
    assert panel._rows == data()['inputs']


def test_stopped_edit_and_reorder_cannot_prepare_live_mutation():
    panel, sent, _ = make()
    panel.publish({**data(), 'stopped': True})
    panel.render(80)
    for key in ('e', 'k', 'j'):
        panel.handle_input(KeyEvent(key))
    assert panel._confirm is None and panel._mode == 'list' and not sent


def test_live_image_return_has_no_user_shortcut_review_or_backup_path():
    panel, sent, drafts = make()
    value = data()
    value['stopped'] = False
    value['inputs'][0]['image_count'] = 1
    panel.publish(value)
    # A stale host's old capability assignment cannot restore the removed UI.
    panel.image_return_enabled = True
    panel.set_render_budget(9)
    rendered = '\n'.join(panel.render(120))
    assert 'u return' not in rendered and 'SECOND' not in rendered
    panel.handle_input(KeyEvent('u'))
    panel.handle_input(KeyEvent('enter'))
    panel.close()
    assert panel._mode == 'list' and panel._confirm is None and not sent and not drafts
    with pytest.raises(ValueError, match='queue UI'):
        panel.publish({**value, 'return_prepared': value['inputs'][0], 'return_token': 'a' * 32})
    with pytest.raises(ValueError, match='Unsupported queue UI'):
        panel._review('return_prepare')
    assert not sent and not drafts


def test_crash_snapshot_source_is_reviewed_as_uncertain_not_an_unexecuted_queue_claim():
    panel, sent, drafts = make()
    value = data()
    value['stopped'] = True
    value['inputs'][0].update(source='crash-snapshot', uncertain=True)
    panel.publish(value)
    panel.render(80)
    panel.handle_input(KeyEvent('u'))
    assert panel._confirm['action'] == 'recover'
    review(panel)
    panel.handle_input(KeyEvent('enter'))
    assert sent[-1]['action'] == 'recover'
    panel.publish({**value, 'recovered': value['inputs'][0]})
    assert drafts[-1]['uncertain'] is True and drafts[-1]['source'] == 'crash-snapshot'
    assert drafts[-1]['stopped'] is True
