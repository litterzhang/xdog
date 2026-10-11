from __future__ import annotations

from xdog.tui.components.extension_navigation import ExtensionNavigation
from xdog.tui.keys import KeyEvent, KeyEventType


def panel():
    answers = []
    dialog = {'id': 'request', 'kind': 'session', 'operation': 'navigate', 'title': 'Reviewed navigation',
        'target': {'action': 'goto', 'session_id': 'target', 'branch_id': 'branch', 'index': 2}}
    result = ExtensionNavigation('navigator', dialog, answers.append, 'current')
    result.focused = True  # The actual clients assign focus before collecting review proof.
    return result, answers


def review(panel):
    for _ in range(100):
        panel.render(80)
        if panel.form._visible:
            return
        panel.handle_input(KeyEvent('pagedown'))
    raise AssertionError('Whole target review incomplete')


def test_navigation_default_deny_paste_repeat_hidden_resize_and_full_target_review():
    panel1, answers = panel()
    panel1.handle_paste('yes\n')
    panel1.handle_input(KeyEvent('right'))
    panel1.handle_input(KeyEvent('enter'))
    panel1.handle_input(KeyEvent('enter'))
    assert not answers
    review(panel1)
    panel1.set_render_budget(0)
    panel1.render(80)
    panel1.handle_input(KeyEvent('enter'))
    assert not answers
    panel1.set_render_budget(8)
    panel1.render(16)
    panel1.handle_input(KeyEvent('enter'))
    assert not answers
    review(panel1)
    panel1.handle_input(KeyEvent('enter', event_type=KeyEventType.REPEAT))
    assert not answers
    panel1.handle_input(KeyEvent('enter'))
    assert answers == [True]
    panel2, denied = panel()
    panel2.handle_input(KeyEvent('enter'))
    review(panel2)
    panel2.handle_input(KeyEvent('enter'))
    assert denied == [False]



def test_unfocused_navigation_cannot_accumulate_target_review_and_blur_revokes_seen_rows():
    wrapper, answers = panel()
    wrapper.focused = False
    wrapper.handle_input(KeyEvent('right'))
    wrapper.handle_input(KeyEvent('enter'))
    for _ in range(30):
        wrapper.render(80)
        wrapper.handle_input(KeyEvent('pagedown'))
    assert not wrapper.form._seen and not wrapper.form._visible and not answers
    wrapper.focused = True
    wrapper.render(80)
    assert not wrapper.form._visible
    review(wrapper)
    wrapper.focused = False
    wrapper.handle_input(KeyEvent('enter'))
    assert not wrapper.form._seen and not wrapper.form._visible and not answers
    wrapper.focused = True
    review(wrapper)
    wrapper.handle_input(KeyEvent('enter'))
    assert answers == [True]
