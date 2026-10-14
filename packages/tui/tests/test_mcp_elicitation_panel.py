"""Elicitation full-paint request/reply UI; no external browser or implicit data release."""
from __future__ import annotations

import hashlib
import json

import pytest
from xdog.tui.components.mcp_elicitation_panel import McpElicitationPanel
from xdog.tui.keys import KeyEvent, KeyEventType
from xdog.tui.utils import string_width


def event(phase='request', mode='form'):
    request = {'jsonrpc': '2.0', 'id': 'peer', 'method': 'elicitation/create', 'params': {
        'message': 'Review all requested data', 'requestedSchema': {'type': 'object',
            'properties': {'name': {'type': 'string'}}, 'required': ['name']}}}
    if mode == 'url':
        request['params'] = {'mode': 'url', 'message': 'Review external interaction',
                             'elicitationId': 'external', 'url': 'https://operator.example/flow'}
    body = request if phase == 'request' else {'request': request, 'response': {
        'action': 'accept', 'content': {'name': 'PRIVATE REVIEWED'}}}
    digest = hashlib.sha256(json.dumps(body, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    return {'type': 'elicitation_review', 'phase': phase, 'body': body, 'id': 'a' * 32,
            'receipt': 'b' * 32, 'sha256': digest, 'server': 'peer', 'lease': 'view',
            'notice': 'Inspect exact information before releasing it.'}


def paint(panel, width=40):
    panel.set_render_budget(4)
    rows = []
    for _ in range(1000):
        rendered = panel.render(width)
        assert all(string_width(row) <= width for row in rendered)
        rows.extend(rendered[1:-1])
        if panel._review._visible:
            return rows
        panel.handle_input(KeyEvent('pagedown'))
    raise AssertionError('Elicitation complete review did not paint')


def test_form_request_paint_then_edit_then_reply_paint_without_early_send():
    sent = []
    panel = McpElicitationPanel(event(), sent.append, lambda: None)
    panel.focused = True
    panel.handle_input(KeyEvent('enter'))
    assert panel._mode == 'request' and not sent
    assert 'requestedSchema' in ''.join(paint(panel))
    panel.handle_input(KeyEvent('enter'))
    assert panel._mode == 'form' and panel.editor.focused and not sent
    panel.editor.set_text('{"name":"PRIVATE EDITED"}')
    panel.handle_input(KeyEvent('enter'))
    assert panel._mode == 'reply' and not sent
    panel.handle_input(KeyEvent('enter'))
    assert not sent
    assert 'PRIVATE EDITED' in ''.join(paint(panel))
    panel.handle_input(KeyEvent('enter', event_type=KeyEventType.REPEAT))
    assert not sent
    panel.handle_input(KeyEvent('enter'))
    panel.handle_input(KeyEvent('enter'))
    assert len(sent) == 1 and sent[0]['response'] == {'action': 'accept', 'content': {'name': 'PRIVATE EDITED'}}
    assert sent[0]['phase'] == 'request' and sent[0]['receipt'] == 'b' * 32


@pytest.mark.parametrize('phase,mode', [('request', 'url'), ('response', 'form')])
def test_url_and_validated_response_phase_require_exact_paint_and_send_ack_only(phase, mode):
    sent = []
    panel = McpElicitationPanel(event(phase, mode), sent.append, lambda: None)
    panel.focused = True
    panel.handle_input(KeyEvent('enter'))
    assert not sent
    paint(panel)
    panel.handle_input(KeyEvent('enter'))
    assert sent[0]['response'] == {'action': 'accept'}  # No browser/nav helper is invoked.
    assert sent[0]['phase'] == phase


def test_focus_resize_and_reply_edit_revoke_old_paint_evidence():
    sent = []
    panel = McpElicitationPanel(event(), sent.append, lambda: None)
    panel.focused = True
    paint(panel)
    panel.focused = False
    panel.focused = True
    panel.handle_input(KeyEvent('enter'))
    assert not sent and panel._mode == 'request'
    paint(panel)
    panel.render(50)
    assert not panel._review._visible
    paint(panel, 50)
    panel.handle_input(KeyEvent('enter'))
    panel.editor.set_text('{"name":"first"}')
    panel.handle_input(KeyEvent('enter'))
    paint(panel)
    panel.handle_input(KeyEvent('e'))
    assert panel._mode == 'form' and panel.editor.focused
    panel.editor.set_text('{"name":"LATEST"}')
    panel.handle_input(KeyEvent('enter'))
    assert not panel._review._visible
    panel.handle_paste('ignored')
    paint(panel)
    panel.handle_input(KeyEvent('enter'))
    assert sent[0]['response']['content']['name'] == 'LATEST'


@pytest.mark.parametrize('value', ['not json', '[]', '{"unrequested":"PRIVATE"}', '{"name":{"nested":"PRIVATE"}}'])
def test_invalid_edit_kept_without_data_release(value):
    sent = []
    panel = McpElicitationPanel(event(), sent.append, lambda: None)
    panel.focused = True
    paint(panel)
    panel.handle_input(KeyEvent('enter'))
    panel.editor.set_text(value)
    panel.handle_input(KeyEvent('enter'))
    assert panel.editor.get_text() == value and panel._mode == 'form' and not sent


def test_decline_never_includes_private_edit_and_disabled_panel_cannot_reapprove():
    sent, canceled = [], []
    panel = McpElicitationPanel(event(), sent.append, lambda: canceled.append(True))
    panel.focused = True
    paint(panel)
    panel.handle_input(KeyEvent('enter'))
    panel.editor.set_text('{"name":"PRIVATE"}')
    panel.handle_input(KeyEvent('d', ctrl=True))
    assert sent[0]['response'] == {'action': 'decline'} and 'PRIVATE' not in str(sent)
    panel = McpElicitationPanel(event(), sent.append, lambda: canceled.append(True))
    panel.focused = True
    paint(panel)
    panel.disable('Host revoked')
    panel.render(100)
    panel.handle_input(KeyEvent('enter'))
    assert len(sent) == 1
    panel.handle_input(KeyEvent('escape'))
    assert canceled == [True]


def test_private_event_inspection_and_reply_controls_are_escaped_not_rendered():
    sent = []
    panel = McpElicitationPanel(event(), sent.append, lambda: None)
    panel.focused = True
    paint(panel)
    panel.handle_input(KeyEvent('enter'))
    panel.editor.set_text(json.dumps({'name': '界🙂 ' + '\x1b]52;c;PRIVATE\x07'}))
    panel.handle_input(KeyEvent('enter'))
    observed = ''.join(paint(panel))
    assert '\x1b' not in observed and '\\u001b' in observed
    panel.event['receipt'] = 'c' * 32
    panel.handle_input(KeyEvent('enter'))
    assert sent[0]['receipt'] == 'b' * 32


@pytest.mark.parametrize('value', ['{}', '{"name":true}', '{"name":5}', '{"name":["wrong"]}'])
def test_required_and_declared_type_edit_mistakes_keep_form_and_receipt(value):
    sent = []
    panel = McpElicitationPanel(event(), sent.append, lambda: None)
    panel.focused = True
    paint(panel)
    panel.handle_input(KeyEvent('enter'))
    panel.editor.set_text(value)
    panel.handle_input(KeyEvent('enter'))
    assert panel._mode == 'form' and not sent
    assert panel.editor.get_text() == value and panel.event['receipt'] == 'b' * 32


def test_malformed_review_refuses_without_adopting_or_echoing_data() -> None:

    def _check_case(fault):
        value = event()
        if fault == "missing-digest":
            del value["sha256"]
        elif fault == "digest":
            value["body"]["params"]["message"] = "changed"
        elif fault == "phase":
            value["phase"] = "executing"
        elif fault == "body":
            value["body"] = []
        else:
            value["receipt"] = "unowned"
        with pytest.raises(ValueError):
            McpElicitationPanel(value, lambda _: None, lambda: None)

    for fault in ["missing-digest", "digest", "phase", "body", "identity"]:
        _check_case(fault)


def test_invisible_and_tiny_viewports_cannot_accept_and_disabled_focus_stays_revoked():
    sent = []
    panel = McpElicitationPanel(event('response'), sent.append, lambda: None)
    panel.focused = True
    panel.set_render_budget(2)
    assert panel.render(80) == []
    panel.handle_input(KeyEvent('enter'))
    assert not sent
    panel.set_render_budget(8)
    assert panel.render(10) == []
    panel.handle_input(KeyEvent('enter'))
    assert not sent
    paint(panel)
    panel.disable('Host revoked')
    panel.focused = False
    panel.focused = True
    assert not panel.editor.focused and not panel._review.focused
    panel.render(80)
    panel.handle_input(KeyEvent('enter'))
    assert not sent


def test_url_open_separate_full_paint_once_never_acknowledges_or_completes():
    sent, opened = [], []
    panel = McpElicitationPanel(event('request', 'url'), sent.append, lambda: None, open_url=opened.append)
    panel.focused = True
    panel.handle_input(KeyEvent('o'))
    assert not opened and not sent
    paint(panel)
    panel.handle_input(KeyEvent('o', event_type=KeyEventType.REPEAT))
    assert not opened
    panel.handle_input(KeyEvent('o'))
    panel.handle_input(KeyEvent('o'))
    assert opened == ['https://operator.example/flow'] and not sent
    panel.handle_input(KeyEvent('enter'))
    assert sent[0]['response'] == {'action': 'accept'}


def test_disabled_or_unfocused_url_review_cannot_open():
    opened = []
    panel = McpElicitationPanel(event('request', 'url'), lambda _: None, lambda: None, open_url=opened.append)
    panel.focused = True
    paint(panel)
    panel.focused = False
    panel.handle_input(KeyEvent('o'))
    panel.focused = True
    panel.handle_input(KeyEvent('o'))
    assert not opened
    paint(panel)
    panel.disable('host withdrawn')
    panel.handle_input(KeyEvent('o'))
    assert not opened
