"""Private review receipts and whole-target visible consent, not an editor-read API."""
from __future__ import annotations

import pytest
from xdog.tui.components.extension_editor_write import ExtensionEditorWrite
from xdog.tui.editor_write import EditorWriteReviews
from xdog.tui.keys import KeyEvent, KeyEventType


def dialog(text='PROPOSED'):
    return {'kind': 'editor', 'operation': 'propose', 'id': 'broker', 'extension': 'owned', 'title': 'Draft proposal',
        'text': text, 'editor_write': {'id': 'proposal', 'invocation': 'callback', 'session_id': 'session',
                                    'child_id': '', 'method': 'tools/call'}}


@pytest.mark.parametrize('change', ['none', 'text', 'cursor', 'image', 'owner', 'revision', 'invocation', 'extension'])
def test_private_cas_one_use_draft_image_and_owner_checks(change):
    reviews, source, live = EditorWriteReviews(), ['PRIVATE', 7, ('image-one',)], [True]
    def replace(value):
        source[0] = value
        return True
    request = dialog()
    assert reviews.prepare(request, lambda: tuple(source), replace, lambda: live[0])
    receipt = reviews.answer(request, True)
    assert receipt['approved'] and set(receipt) == {'approved', 'revision'} and 'PRIVATE' not in str(receipt)
    event = {**request, 'revision': receipt['revision']}
    if change == 'text':
        source[0] = 'USER TEXT'
    elif change == 'cursor':
        source[1] = 0
    elif change == 'image':
        source[2] = ('image-two',)
    elif change == 'owner':
        live[0] = False
    elif change == 'revision':
        event['revision'] = 'b' * 64
    elif change == 'invocation':
        event['editor_write'] = {**request['editor_write'], 'invocation': 'other'}
    elif change == 'extension':
        event['extension'] = 'other'
    assert reviews.apply(event) is (change == 'none')
    assert not reviews.apply(event) and not reviews._pending
    assert (source[0] == 'PROPOSED') is (change == 'none')


def test_full_large_unicode_proposal_requires_every_current_width_review_row_and_press():
    replies = []
    request = dialog('\n'.join(f'{index}: 界👩\u200d💻 ' + 'x' * 45 for index in range(80)))
    panel = ExtensionEditorWrite('owned', request, replies.append)
    panel.focused = True
    panel.set_render_budget(9)
    panel.handle_input(KeyEvent('right'))
    panel.handle_input(KeyEvent('enter'))
    panel.render(40)
    panel.handle_input(KeyEvent('enter'))
    assert not replies
    for _ in range(300):
        panel.render(40)
        if panel.form._visible:
            break
        panel.handle_input(KeyEvent('pagedown'))
    assert panel.form._visible
    panel.render(30)  # Resize invalidates full review until all new-width rows are seen.
    panel.handle_input(KeyEvent('enter'))
    assert not replies
    for _ in range(400):
        panel.render(30)
        if panel.form._visible:
            break
        panel.handle_input(KeyEvent('pagedown'))
    assert panel.form._visible
    panel.handle_input(KeyEvent('enter', event_type=KeyEventType.REPEAT))
    assert not replies
    panel.handle_input(KeyEvent('enter'))
    assert replies == [True]


def test_stale_capacity_is_pruned_without_clearing_other_live_proposals(monkeypatch):
    import xdog.tui.editor_write as module
    clock, live, reviews = [100.0], [True], EditorWriteReviews()
    monkeypatch.setattr(module.time, 'monotonic', lambda: clock[0])
    for index in range(8):
        request = dialog()
        request['editor_write']['id'] = str(index)
        assert reviews.prepare(request, lambda: ('private', 7, ()), lambda text: True,
                               (lambda: live[0]) if index < 7 else lambda: True)
    request = dialog()
    assert not reviews.prepare(request, lambda: ('private', 7, ()), lambda text: True, lambda: True)
    live[0] = False
    assert reviews.prepare(request, lambda: ('private', 7, ()), lambda text: True, lambda: True)
    assert set(reviews._pending) == {'7', 'proposal'}
    receipt = reviews.answer(request, True)
    clock[0] += 301
    assert not reviews.apply({**request, **receipt})
    assert not reviews._pending
