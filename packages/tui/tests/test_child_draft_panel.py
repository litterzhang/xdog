"""Local draft review and identity fencing, no backend controls or implicit execution."""
from __future__ import annotations

import base64
from dataclasses import replace

import pytest
from xdog.tui.attachments import attachment
from xdog.tui.child_drafts import ChildDraft
from xdog.tui.components.child_draft_panel import ChildDraftPanel, draft_entries
from xdog.tui.keys import KeyEvent, KeyEventType


def setup():
    image = attachment(base64.b64decode(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5ioAAAAASUVORK5CYII='), 'retained.png')
    original = ChildDraft('UNSENT PHOTO TASK', 'inherit', {'model': 'old/model', 'tools': ['bash']}, (image,), True, 4)
    caches = {'old-context': {('start', 'recovered/old/input'): original},
              'current': {('start', ''): ChildDraft('CURRENT TASK', cursor=3)}}
    acts = []
    panel = ChildDraftPanel(lambda: draft_entries(caches, {'old-context': '/old/workspace · session old'}, 'current'),
                            lambda *args: acts.append(args), lambda: None)
    panel.focused = True
    panel.set_render_budget(8)
    return panel, caches, original, acts


def paint_review(panel):
    for _ in range(50):
        panel.render(90)
        if panel._visible:
            return
        panel.handle_input(KeyEvent('pagedown'))
    raise AssertionError('Full local draft review did not complete')


def test_search_old_context_copy_requires_full_physical_review_and_no_paste_or_repeat_commit():
    panel, _caches, original, acts = setup()
    panel.handle_input(KeyEvent('/'))
    panel.handle_paste('PHOTO')
    panel.handle_input(KeyEvent('enter'))
    panel.render(90)
    panel.handle_input(KeyEvent('enter'))
    panel.handle_input(KeyEvent('enter'))
    panel.handle_input(KeyEvent('enter', event_type=KeyEventType.REPEAT))
    panel.handle_paste('\n\n')
    assert not acts
    paint_review(panel)
    panel.set_render_budget(0)
    panel.render(90)
    panel.handle_input(KeyEvent('enter'))
    assert not acts
    panel.set_render_budget(8)
    paint_review(panel)
    panel.handle_input(KeyEvent('enter'))
    assert acts[0][0] == 'copy' and acts[0][1].original is original
    assert acts[0][1].value.images == original.images


@pytest.mark.parametrize('change', ['replace', 'config', 'remove'])
def test_changed_draft_invalidates_already_painted_review_without_host_action(change):
    panel, caches, original, acts = setup()
    panel.render(90)
    panel.handle_input(KeyEvent('x'))
    paint_review(panel)
    if change == 'replace':
        caches['old-context'][('start', 'recovered/old/input')] = replace(original, text='changed')
    elif change == 'config':
        original.config['tools'].append('filesystem')
    else:
        caches['old-context'].clear()
    panel.handle_input(KeyEvent('enter'))
    assert not acts and 'changed' in panel._error


def test_old_context_cannot_reopen_and_current_reopen_is_explicitly_reviewed():
    panel, _caches, _original, acts = setup()
    panel.render(90)
    panel.handle_input(KeyEvent('r'))
    assert panel._review is None and 'cannot' not in panel._error and 'not resumed' in panel._error
    panel.handle_input(KeyEvent('down'))
    panel.render(90)
    panel.handle_input(KeyEvent('r'))
    assert not acts
    paint_review(panel)
    panel.handle_input(KeyEvent('enter'))
    assert acts[0][0] == 'reopen' and acts[0][1].current


def test_image_preview_is_read_only_and_return_requires_fresh_full_receipt_painting():
    panel, _caches, original, acts = setup()
    panel.render(90)
    panel.handle_input(KeyEvent('enter'))
    paint_review(panel)
    assert panel._visible
    review = panel._review
    panel.handle_input(KeyEvent('p'))
    assert panel._preview.item.data == original.images[0].data
    assert not panel._visible and not panel._reviewed
    panel.render(90)
    panel.handle_paste('\n\n')
    panel.handle_input(KeyEvent('enter'))
    panel.handle_input(KeyEvent('enter', event_type=KeyEventType.REPEAT))
    assert not acts and panel._review is review
    panel.handle_input(KeyEvent('escape'))
    assert panel._preview is None and panel._review is review
    panel.handle_input(KeyEvent('enter'))
    assert not acts  # Preview/closing it is not painted consent.
    paint_review(panel)
    panel.handle_input(KeyEvent('enter'))
    assert len(acts) == 1 and acts[0][0] == 'copy'


def test_changed_source_during_preview_invalidates_review_and_does_not_apply_action():
    panel, caches, original, acts = setup()
    panel.render(90)
    panel.handle_input(KeyEvent('x'))
    panel.handle_input(KeyEvent('p'))
    assert panel._preview is not None
    caches['old-context'][('start', 'recovered/old/input')] = replace(original, text='new')
    panel.render(90)
    assert panel._preview is None and panel._review is None
    assert 'changed during preview' in panel._error and not acts


def test_image_preview_cycles_exact_attachments_without_changing_composer_or_source():
    panel, caches, original, acts = setup()
    second = replace(original.images[0], id='other-image', name='second.png')
    multiple = replace(original, images=(*original.images, second))
    caches['old-context'][('start', 'recovered/old/input')] = multiple
    panel.render(90)
    panel.handle_input(KeyEvent('p'))
    panel.handle_input(KeyEvent('right'))
    assert panel._preview.item is second
    panel.handle_input(KeyEvent('left'))
    assert panel._preview.item is multiple.images[0]
    assert caches['old-context'][('start', 'recovered/old/input')] is multiple and not acts
