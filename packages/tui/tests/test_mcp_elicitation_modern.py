"""Modern URL review acknowledges input, never claims legacy notice completion."""
from __future__ import annotations

import hashlib
import json

import pytest
from xdog.tui.components.mcp_elicitation_panel import McpElicitationPanel
from xdog.tui.keys import KeyEvent, KeyEventType

from packages.tui.tests.test_mcp_elicitation_panel import event, paint


def modern_event():
    value = event(mode='url')
    value['protocol'] = '2026-07-28'
    del value['body']['params']['elicitationId']
    value['sha256'] = hashlib.sha256(json.dumps(value['body'], ensure_ascii=False,
                                               separators=(',', ':')).encode()).hexdigest()
    return value


def test_modern_url_full_review_distinct_open_ack_and_no_invented_completion_identity():
    value, sent, opened = modern_event(), [], []
    panel = McpElicitationPanel(value, sent.append, lambda: None, open_url=opened.append)
    panel.focused = True
    panel.handle_input(KeyEvent('enter'))
    panel.handle_input(KeyEvent('o'))
    assert not sent and not opened
    paint(panel, 100)
    assert 'ack only; original request checks outcome' in panel.render(100)[0]
    panel.handle_input(KeyEvent('o', event_type=KeyEventType.REPEAT))
    assert not opened
    panel.handle_input(KeyEvent('o'))
    panel.handle_input(KeyEvent('o'))
    assert opened == ['https://operator.example/flow'] and not sent
    panel.handle_input(KeyEvent('enter'))
    assert sent[0]['response'] == {'action': 'accept'}
    assert 'elicitationId' not in json.dumps(panel.event)
    assert 'completion' not in json.dumps(sent)


@pytest.mark.parametrize('fault', ['legacy-without-id', 'modern-with-id', 'unknown-protocol'])
def test_version_mismatch_refuses_instead_of_silent_legacy_downgrade(fault):
    value = modern_event()
    if fault == 'legacy-without-id':
        del value['protocol']
    elif fault == 'modern-with-id':
        value['body']['params']['elicitationId'] = 'fake-notice-id'
    else:
        value['protocol'] = 'unknown'
    with pytest.raises(ValueError):
        McpElicitationPanel(value, lambda response: None, lambda: None)


def test_modern_url_focus_and_resize_invalidate_open_and_ack_then_decline_leaks_nothing():
    sent, opened = [], []
    panel = McpElicitationPanel(modern_event(), sent.append, lambda: None, open_url=opened.append)
    panel.focused = True
    paint(panel)
    panel.focused = False
    panel.focused = True
    panel.handle_input(KeyEvent('o'))
    panel.handle_input(KeyEvent('enter'))
    assert not opened and not sent
    paint(panel)
    panel.render(50)
    panel.handle_input(KeyEvent('o'))
    assert not opened
    panel.handle_input(KeyEvent('d', ctrl=True))
    assert sent[0]['response'] == {'action': 'decline'}
