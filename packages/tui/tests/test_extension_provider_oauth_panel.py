"""Sensitive provider prompts never enter normal editor snapshots or render text."""
from __future__ import annotations

import hashlib
import json

import pytest
from xdog.tui.components.extension_provider_auth import ExtensionProviderAuth
from xdog.tui.keys import KeyEvent, KeyEventType


def dialog(phase):
    value = {'id': 'owner', 'kind': 'input' if phase == 'on_prompt' else 'confirm', 'title': 'Provider auth',
        'provider_auth_host': 'a' * 32, 'provider_auth': {'provider_id': 'ext-provider-local', 'name': 'Provider',
            'action': 'login', 'phase': phase, **({'url': 'https://inert.example/authorize',
                'instructions': 'Review all details'} if phase == 'on_auth' else {'prompt': 'Sensitive code'})}}
    value['provider_auth_digest'] = hashlib.sha256(json.dumps(
        value['provider_auth'], ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    return value


def paint(panel, width=70):
    panel.set_render_budget(5)
    for _ in range(300):
        panel.render(width)
        if panel._review._visible:
            return
        panel.handle_input(KeyEvent('pagedown'))
    raise AssertionError('Full provider review not painted')


@pytest.mark.parametrize('phase', ['consent', 'on_auth'])
def test_provider_auth_requires_all_paint_and_distinct_press_no_automatic_browser(phase):
    replies, opened = [], []
    panel = ExtensionProviderAuth('provider', dialog(phase), replies.append, open_url=opened.append)
    panel.focused = True
    panel.handle_input(KeyEvent('enter'))
    panel.handle_input(KeyEvent('o'))
    assert not replies and not opened
    paint(panel)
    panel.handle_input(KeyEvent('enter', event_type=KeyEventType.REPEAT))
    assert not replies and not opened
    if phase == 'on_auth':
        panel.handle_input(KeyEvent('o'))
        panel.handle_input(KeyEvent('o'))
        assert opened == ['https://inert.example/authorize'] and not replies
    panel.handle_input(KeyEvent('enter'))
    assert replies == [True]


@pytest.mark.parametrize('end', ['submit', 'cancel'])
def test_sensitive_draft_masked_no_generic_undo_kill_ring_snapshot_or_closed_retention(end):
    replies = []
    panel = ExtensionProviderAuth('provider', dialog('on_prompt'), replies.append)
    panel.focused = True
    panel.handle_paste('PREMATURE SECRET')
    assert not panel._secret
    paint(panel)
    panel.handle_input(KeyEvent('enter'))
    panel.handle_paste('SENSITIVE-CODE')
    assert not replies
    rendered = '\n'.join(panel.render(80))
    assert 'SENSITIVE-CODE' not in rendered + json.dumps(panel.dialog) + repr(panel._input.__dict__)
    assert '*' in rendered and not panel._input.get_text()
    panel.handle_input(KeyEvent('enter' if end == 'submit' else 'escape'))
    assert replies == ['SENSITIVE-CODE' if end == 'submit' else None]
    assert panel._secret == '' and 'SENSITIVE-CODE' not in repr(panel._input.__dict__)
    assert panel.render(80) == []


def test_prompt_does_not_accept_control_paste_or_replay_after_focus_resize():
    replies = []
    panel = ExtensionProviderAuth('provider', dialog('on_prompt'), replies.append)
    panel.focused = True
    paint(panel)
    panel.focused = False
    panel.focused = True
    panel.handle_input(KeyEvent('enter'))
    assert not panel._editing
    paint(panel, 90)
    panel.handle_input(KeyEvent('enter'))
    panel.handle_paste('bad\ncode')
    assert not panel._secret
    panel.handle_paste('code')
    panel.handle_input(KeyEvent('enter'))
    assert not replies
    panel.render(90)
    panel.handle_input(KeyEvent('enter'))
    assert replies == ['code']


def test_url_review_without_opener_labels_manual_action_and_never_promises_automatic_navigation():
    panel = ExtensionProviderAuth('provider', dialog('on_auth'), lambda value: None)
    panel.focused = True
    paint(panel, 100)
    assert 'Manual URL only' in panel.render(100)[-1]
