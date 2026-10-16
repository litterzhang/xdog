"""Exact private native review retains focus, bounds, cancellation and receipt safety."""
from __future__ import annotations

import copy
import hashlib
import json

import pytest
from xdog.tui.components.json_review import _json, _JsonReview
from xdog.tui.keys import KeyEvent, KeyEventType
from xdog.tui.utils import string_width


def event(body=None):
    body = body or {'request': 'UNTRUSTED DATA', 'response': 'PRIVATE OUTPUT'}
    encoded = _json(body)
    return {'type': 'json_review', 'phase': 'review', 'id': 'a' * 32, 'receipt': 'b' * 32,
            'sha256': hashlib.sha256(encoded.encode()).hexdigest(), 'body': body,
            'server': 'peer', 'lease': 'view', 'notice': 'Inspect all content before approving.'}


def paint(panel, width=50, budget=4):
    panel.set_render_budget(budget)
    observed = []
    for _ in range(1000):
        rows = panel.render(width)
        assert all(string_width(row) <= width for row in rows)
        observed.extend(rows[1:-1])
        if panel._visible:
            return observed
        panel.handle_input(KeyEvent('pagedown'))
    raise AssertionError('Exact native review never fully painted')


def test_focus_full_paint_distinct_press_and_exact_single_use_receipt():
    approved, canceled = [], []
    original = event()
    panel = _JsonReview(original, approved.append, lambda: canceled.append(True))
    panel.handle_input(KeyEvent('enter'))
    assert not approved
    panel.focused = True
    panel.handle_input(KeyEvent('enter'))
    assert not approved
    assert 'PRIVATE OUTPUT' in ''.join(paint(panel))
    panel.handle_paste('yes\n')
    panel.handle_input(KeyEvent('enter', event_type=KeyEventType.REPEAT))
    panel.handle_input(KeyEvent('enter', event_type=KeyEventType.RELEASE))
    assert not approved
    inspected = panel.event
    inspected['receipt'] = 'c' * 32
    inspected['body'].clear()
    panel.handle_input(KeyEvent('enter'))
    panel.handle_input(KeyEvent('enter'))
    assert len(approved) == 1 and approved[0]['receipt'] == original['receipt']
    assert approved[0]['sha256'] == original['sha256'] and not canceled
    assert not hasattr(panel, 'editor')  # No model/tool/context selection UI survives.


def test_focus_resize_and_zero_budget_invalidate_paint_without_automatic_approval():
    approved = []
    panel = _JsonReview(event(), approved.append, lambda: None)
    panel.focused = True
    paint(panel)
    panel.focused = False
    panel.focused = True
    panel.handle_input(KeyEvent('enter'))
    assert not approved
    paint(panel)
    panel.render(12)
    assert not panel._visible
    panel.set_render_budget(0)
    assert panel.render(80) == [] and not panel._visible
    panel.handle_input(KeyEvent('enter'))
    assert not approved


def test_disable_cannot_repaint_or_confirm_but_escape_still_cancels_once():
    approved, canceled = [], []
    panel = _JsonReview(event(), approved.append, lambda: canceled.append(True))
    panel.focused = True
    paint(panel)
    panel.disable('Host withdrawn')
    panel.render(100)
    panel.handle_input(KeyEvent('enter'))
    assert not approved and not panel._visible
    panel.handle_input(KeyEvent('escape'))
    panel.handle_input(KeyEvent('escape'))
    assert canceled == [True]


@pytest.mark.parametrize('fault', ['receipt', 'digest', 'phase', 'body', 'notice'])
def test_bad_review_refused_without_display_or_receipt_admission(fault):
    value = event()
    if fault == 'receipt':
        value['receipt'] = 'wrong'
    elif fault == 'digest':
        value['body']['forged'] = True
    elif fault == 'phase':
        value['phase'] = 'executing'
    elif fault == 'body':
        value['body'] = []
    else:
        value['notice'] = '\x1b[31munsafe'
    with pytest.raises(ValueError):
        _JsonReview(value, lambda _: None, lambda: None)


def test_long_unicode_literal_terminal_controls_and_boundary_spaces_remain_exact_data():
    body = {'text': '界🙂' * 150 + '\x1b]52;c;PRIVATE\x07 END-OUTPUT-TAIL' + 'word ' * 30 + '\x9b31m\u202eCONTROL END'}
    panel = _JsonReview(event(body), lambda _: None, lambda: None)
    panel.focused = True
    observed = paint(panel, width=40)
    assert all('\x1b' not in row and '\x07' not in row for row in observed)
    joined = ''.join(observed)
    assert '\\u001b' in joined and 'END-OUTPUT-TAIL' in joined
    assert panel.event['body'] == copy.deepcopy(body)
    encoded = ''.join(panel._lines[1:])
    assert json.loads(encoded) == body
    assert '\x9b' not in encoded and '\u202e' not in encoded
    assert '\\u009b' in encoded and '\\u202e' in encoded


@pytest.mark.parametrize('value', [{1: 'nonstring key'}, object(), float('nan'), '\ud800', ['x'] * 50001])
def test_json_snapshot_rejects_unsupported_or_unbounded_values(value):
    with pytest.raises(ValueError):
        _json(value)
