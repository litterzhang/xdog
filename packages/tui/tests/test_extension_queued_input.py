"""Queued input review shows exact bounded descriptors; consent is false by default."""
from __future__ import annotations

import copy

import pytest
from xdog.tui.components.extension_navigation import ExtensionNavigation
from xdog.tui.components.extension_queued_input import ExtensionQueuedInput
from xdog.tui.keys import KeyEvent, KeyEventType


def dialog():
    return {'id': 'request', 'kind': 'session', 'operation': 'queue_input', 'title': 'Review queue',
            'text': ' /quit ', 'delivery': 'follow_up', 'images': [],
            'host_message': '[User-approved message requested by extension queuedrequest]\n\n /quit ',
            'image_summaries': [{'mime_type': 'image/png', 'bytes': 100, 'sha256': 'a' * 64}]}


def review(panel):
    for _ in range(150):
        panel.render(80)
        if panel.form._visible:
            return
        panel.handle_input(KeyEvent('pagedown'))
    raise AssertionError('Whole queued input was not reviewed')


def test_exact_message_delivery_and_descriptors_require_focused_whole_review_not_paste_repeat_or_release():
    raw, answers = dialog(), []
    panel = ExtensionQueuedInput('queuedrequest', raw, answers.append, 'original child')
    assert isinstance(panel, ExtensionNavigation)
    panel.focused = True
    panel.handle_paste('yes\n')
    panel.handle_input(KeyEvent('right'))
    panel.handle_input(KeyEvent('enter'))
    assert not answers
    # Snapshot cannot be mutated by the caller after mounting the review.
    raw['image_summaries'][0]['sha256'] = 'b' * 64
    assert panel.dialog['image_summaries'][0]['sha256'] == 'a' * 64
    review(panel)
    panel.handle_input(KeyEvent('enter', event_type=KeyEventType.REPEAT))
    panel.handle_input(KeyEvent('enter', event_type=KeyEventType.RELEASE))
    assert not answers
    panel.focused = False
    panel.handle_input(KeyEvent('enter'))
    assert not answers and not panel.form._visible
    panel.focused = True
    review(panel)
    panel.set_render_budget(0)
    panel.render(80)
    panel.handle_input(KeyEvent('enter'))
    assert not answers
    panel.set_render_budget(8)
    panel.render(16)
    panel.handle_input(KeyEvent('enter'))
    assert not answers
    review(panel)
    panel.handle_input(KeyEvent('enter'))
    assert answers == [True]


def test_default_false_and_escape_never_accept_queued_input():
    answers = []
    panel = ExtensionQueuedInput('queuedrequest', dialog(), answers.append, 'parent')
    panel.focused = True
    panel.handle_input(KeyEvent('enter'))
    review(panel)
    panel.handle_input(KeyEvent('enter'))
    assert answers == [False]
    denied = []
    panel = ExtensionQueuedInput('queuedrequest', dialog(), denied.append, 'parent')
    panel.focused = True
    panel.handle_input(KeyEvent('escape'))
    assert denied == [False]


@pytest.mark.parametrize('changes', [
    {'images': [{'data': 'binary'}]}, {'images': ()}, {'operation': 'navigate'}, {'kind': 'form'},
    {'delivery': []}, {'delivery': 'nextTurn'}, {'host_message': 'other text'}, {'text': '\x00'},
    {'title': []}, {'image_summaries': {}}, {'image_summaries': [None]},
    {'image_summaries': [{'mime_type': [], 'bytes': 1, 'sha256': 'a' * 64}]},
    {'image_summaries': [{'mime_type': 'image/png', 'bytes': True, 'sha256': 'a' * 64}]},
    {'image_summaries': [{'mime_type': 'image/png', 'bytes': 0, 'sha256': 'a' * 64}]},
    {'image_summaries': [{'mime_type': 'image/png', 'bytes': 1, 'sha256': 'A' * 64}]},
    {'image_summaries': [{'mime_type': 'image/png', 'bytes': 1, 'sha256': 'a' * 64, 'data': 'binary'}]},
    {'image_summaries': [{'mime_type': 'image/png', 'bytes': 1, 'sha256': 'a' * 64}] * 5},
    {'image_summaries': [{'mime_type': 'image/png', 'bytes': 4 * 1024 * 1024, 'sha256': 'a' * 64}] * 2},
    {'image_refs': ['foreign draft']},
])
def test_renderer_refuses_malformed_or_hidden_binary_projections(changes):
    value = copy.deepcopy(dialog())
    value.update(changes)
    with pytest.raises(ValueError):
        ExtensionQueuedInput('queuedrequest', value, lambda _: None, 'parent')


def test_bounded_host_child_metadata_is_distinct_from_registered_extension_identity():
    value = {**dialog(), 'child_id': '0123456789ab', 'queued_input_generation': 2}
    panel = ExtensionQueuedInput('queuedrequest', value, lambda _: None, 'Child 0123456789ab')
    assert panel.dialog['child_id'] == '0123456789ab'
    for changes in ({'child_id': []}, {'child_id': 'foreign'}, {'queued_input_generation': True},
                    {'queued_input_generation': -1}):
        with pytest.raises(ValueError):
            ExtensionQueuedInput('queuedrequest', {**value, **changes}, lambda _: None, 'Child')
    with pytest.raises(ValueError):
        ExtensionQueuedInput('Child 0123456789ab · queuedrequest', value, lambda _: None, 'Child')
