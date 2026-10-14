"""Sampling UI selection and full-paint approval do not imply host permission admission."""
from __future__ import annotations

import copy
import hashlib
import json

import pytest
from xdog.tui.components.mcp_sampling_panel import McpSamplingPanel
from xdog.tui.keys import KeyEvent, KeyEventType
from xdog.tui.utils import string_width


def event(phase='input', body=None):
    body = body or ({'jsonrpc': '2.0', 'id': 'request', 'method': 'sampling/createMessage', 'params': {
        'messages': [{'role': 'user', 'content': {'type': 'text', 'text': 'UNTRUSTED INPUT'}}],
        'includeContext': 'none', 'maxTokens': 20}} if phase == 'input' else
        {'model': 'host-model', 'role': 'assistant', 'content': {'type': 'text', 'text': 'PRIVATE OUTPUT'}})
    encoded = json.dumps(body, ensure_ascii=False, separators=(',', ':')).encode()
    return {'type': 'sampling_review', 'phase': phase, 'id': 'a' * 32, 'receipt': 'b' * 32,
            'sha256': hashlib.sha256(encoded).hexdigest(), 'body': body,
            'server': 'peer', 'lease': 'view', 'notice': 'Inspect all content before approving.'}


def select(panel, **changes):
    panel.editor.set_text(json.dumps({'model': 'host-model', 'max_tokens': 10,
                                    'context': 'none', 'tools': [], **changes}))
    panel.handle_input(KeyEvent('enter'))


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
    raise AssertionError('Sampling review never fully painted')


@pytest.mark.parametrize('phase', ['input', 'context', 'output'])
def test_each_phase_needs_focus_full_paint_distinct_press_and_exact_single_use(phase):
    approved, canceled = [], []
    original = event(phase)
    panel = McpSamplingPanel(original, ('host-model',), approved.append, lambda: canceled.append(True))
    panel.focused = True
    panel.handle_input(KeyEvent('enter'))
    assert not approved
    if phase == 'input':
        select(panel)
        assert panel._mode == 'review'
    panel.handle_input(KeyEvent('enter'))
    assert not approved
    text = ''.join(paint(panel))
    assert ('UNTRUSTED INPUT' if phase == 'input' else 'PRIVATE OUTPUT') in text
    panel.handle_input(KeyEvent('enter', event_type=KeyEventType.REPEAT))
    panel.handle_input(KeyEvent('enter', event_type=KeyEventType.RELEASE))
    assert not approved
    inspected = panel.event
    inspected['receipt'] = 'c' * 32
    inspected['body'].clear()
    panel.handle_input(KeyEvent('enter'))
    panel.handle_input(KeyEvent('enter'))
    assert len(approved) == 1 and approved[0]['receipt'] == original['receipt']
    assert approved[0]['phase'] == phase and approved[0]['sha256'] == original['sha256']
    assert ('selection' in approved[0]) == (phase == 'input')
    assert not canceled


def test_selection_edits_focus_and_resize_revoke_old_paint():
    approved = []
    panel = McpSamplingPanel(event(), ('host-model',), approved.append, lambda: None)
    panel.focused = True
    select(panel)
    paint(panel)
    panel.focused = False
    panel.focused = True
    panel.handle_input(KeyEvent('enter'))
    assert not approved
    paint(panel)
    panel.render(45)  # Width change invalidates prior complete page coverage.
    assert not panel._visible
    paint(panel, width=45)
    panel.handle_input(KeyEvent('e'))
    assert panel._mode == 'select' and panel.editor.focused and not panel._visible
    select(panel, max_tokens=7)
    assert panel._mode == 'review' and not panel._visible
    panel.handle_paste('FORGED INPUT')
    assert 'FORGED' not in panel.editor.get_text()
    paint(panel, width=45)
    panel.handle_input(KeyEvent('enter'))
    assert approved[0]['selection']['max_tokens'] == 7


@pytest.mark.parametrize('changes', [{'model': 'unlisted'}, {'max_tokens': True}, {'max_tokens': 21},
                                   {'context': 'allServers'}, {'tools': ['unproposed']}, {'tools': ['x', 'x']}])
def test_bad_or_expanded_selection_stays_editable_and_sends_nothing(changes):
    approved = []
    panel = McpSamplingPanel(event(), ('host-model',), approved.append, lambda: None)
    panel.focused = True
    select(panel, **changes)
    assert panel._mode == 'select' and not approved and panel._message
    assert json.loads(panel.editor.get_text())[next(iter(changes))] == next(iter(changes.values()))


@pytest.mark.parametrize('phase', ['input', 'context', 'output'])
def test_disable_cannot_repaint_or_confirm_but_escape_still_cancels_once(phase):
    approved, canceled = [], []
    panel = McpSamplingPanel(event(phase), ('host-model',), approved.append, lambda: canceled.append(True))
    panel.focused = True
    if phase == 'input':
        select(panel)
    paint(panel)
    panel.disable('Host withdrawn')
    panel.render(100)
    panel.handle_input(KeyEvent('enter'))
    assert not approved and not panel._visible
    panel.handle_input(KeyEvent('escape'))
    panel.handle_input(KeyEvent('escape'))
    assert canceled == [True]


def test_bad_review_refused_without_display_or_receipt_admission() -> None:

    def _check_case(fault):
        value = event()
        if fault == "receipt":
            value["receipt"] = "wrong"
        elif fault == "digest":
            value["body"]["params"]["maxTokens"] = 15
        elif fault == "phase":
            value["phase"] = "executing"
        elif fault == "body":
            value["body"] = []
        else:
            value["notice"] = "\x1b[31munsafe"
        with pytest.raises(ValueError):
            McpSamplingPanel(value, ("host-model",), lambda _: None, lambda: None)

    for fault in ["receipt", "digest", "phase", "body", "notice"]:
        _check_case(fault)


def test_long_unicode_and_literal_terminal_controls_remain_inspectable_not_executed():
    body = event('output')['body']
    body['content']['text'] = '界🙂' * 150 + '\x1b]52;c;PRIVATE\x07 END-OUTPUT-TAIL'
    panel = McpSamplingPanel(event('output', body), (), lambda _: None, lambda: None)
    panel.focused = True
    observed = paint(panel, width=40)
    assert all('\x1b' not in row and '\x07' not in row for row in observed)
    joined = ''.join(observed)
    assert '\\u001b' in joined and 'END-OUTPUT-TAIL' in joined
    assert panel.event['body'] == copy.deepcopy(body)



def test_review_display_preserves_boundary_spaces_and_escapes_c1_bidi_controls():
    body = event('output')['body']
    body['content']['text'] = 'word ' * 30 + '\x9b31m\u202eCONTROL END'
    panel = McpSamplingPanel(event('output', body), (), lambda _: None, lambda: None)
    panel.focused = True
    paint(panel, width=40)
    # Exact payload rows concatenate back to the original JSON values. Notice is
    # intentionally separate presentation text and excluded here.
    encoded = ''.join(panel._lines[1:])
    assert json.loads(encoded) == body
    assert '\x9b' not in encoded and '\u202e' not in encoded
    assert '\\u009b' in encoded and '\\u202e' in encoded
