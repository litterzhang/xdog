"""Canonical custom-context display is explicit and never opaque metadata-as-user."""
from __future__ import annotations

from xdog.tui.child_browser import message_records
from xdog.tui.components.extension_context_message import ExtensionContextMessage
from xdog.tui.keys import KeyEvent


def test_context_source_is_custom_labeled_content_only_and_hidden_is_absent():
    source = {'role': 'custom', 'type': 'xdog.extension_context.v1', 'data': {
        'extension': 'fixture', 'custom_type': 'note', 'display': True,
        'content': [{'type': 'text', 'text': 'VISIBLE CONTENT'}], 'details': {'raw': 'OPAQUE NOT PROSE'}}}
    records = message_records([source])
    assert len(records) == 1 and records[0].role == 'custom' and 'fixture / note' in records[0].title
    assert records[0].body == 'VISIBLE CONTENT'
    source['data']['display'] = False
    assert message_records([source]) == ()


def test_context_review_false_default_full_paint_and_no_immediate_user_turn_claim():
    answers = []
    panel = ExtensionContextMessage('fixture', {'id': 'dialog', 'kind': 'session', 'operation': 'context_message',
        'title': 'Review context', 'custom_type': 'note', 'content': [{'type': 'text', 'text': 'REVIEW CONTENT'}],
        'display': False, 'details': {'opaque': 'ONLY REVIEW METADATA'}, 'delivery': 'next_turn',
        'image_summaries': []}, answers.append, 'original-session')
    panel.focused = True
    panel.handle_input(KeyEvent('enter'))
    panel.handle_input(KeyEvent('enter'))
    assert not answers
    for _ in range(200):
        panel.render(40)
        if panel.form._visible:
            break
        panel.handle_input(KeyEvent('pagedown'))
    assert panel.form._visible
    panel.handle_input(KeyEvent('enter'))
    assert answers == [False]
