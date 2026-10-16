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
    caches = {'old-context': {('start', ''): ChildDraft('OLD SCOPE MUST NOT LEAK')},
              'current': {('start', 'recovered/current/input'): original,
                          ('start', ''): ChildDraft('CURRENT TASK', cursor=3)}}
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


def test_current_transient_reopen_requires_full_physical_review_no_paste_repeat_or_release_commit():
    panel, _caches, original, acts = setup()
    panel.render(90)
    panel.handle_input(KeyEvent('enter'))
    panel.handle_input(KeyEvent('enter'))
    for event_type in (KeyEventType.REPEAT, KeyEventType.RELEASE):
        panel.handle_input(KeyEvent('enter', event_type=event_type))
    panel.handle_paste('\n\n')
    assert not acts
    paint_review(panel)
    panel.set_render_budget(0)
    panel.render(90)
    panel.handle_input(KeyEvent('enter'))
    assert not acts
    panel.set_render_budget(8)
    panel.handle_input(KeyEvent('home'))
    paint_review(panel)
    panel.handle_input(KeyEvent('enter'))
    assert acts[0][0] == 'reopen' and acts[0][1].original is original
    assert acts[0][1].value.images == original.images


@pytest.mark.parametrize('change', ['replace', 'config', 'remove'])
def test_changed_draft_invalidates_already_painted_review_without_host_action(change):
    panel, caches, original, acts = setup()
    panel.render(90)
    panel.handle_input(KeyEvent('enter'))
    paint_review(panel)
    if change == 'replace':
        caches['current'][('start', 'recovered/current/input')] = replace(original, text='changed')
    elif change == 'config':
        original.config['tools'].append('filesystem')
    else:
        caches['current'].clear()
    panel.handle_input(KeyEvent('enter'))
    assert not acts and 'changed' in panel._error


def test_recovery_never_lists_old_context_or_disk_snapshots_and_has_no_management_actions():
    from xdog.tui.components.child_draft_panel import DraftEntry
    panel, caches, original, acts = setup()
    caches['saved-draft:disk'] = {('start', ''): ChildDraft('DISK MUST NOT LEAK')}
    rows = panel.source()
    assert len(rows) == 2 and all(row.current and row.scope == 'current' for row in rows)
    assert all('OLD SCOPE' not in row.value.text and 'DISK MUST' not in row.value.text for row in rows)
    before = dict(caches)
    for key in ('s', 'x', 'delete', 'r', '/'):
        panel.render(90)
        panel.handle_input(KeyEvent(key))
        panel.handle_paste('OLD SCOPE\n')
        assert panel._review is None and not panel._searching and not acts
    for action in ('copy', 'save', 'discard'):
        with pytest.raises(ValueError, match='transient reopen'):
            panel._prepare(action, rows[0])
    foreign = DraftEntry('old-context', ('start', ''), original, original, 'old', False)
    disk = replace(foreign, scope='saved-draft:disk', current=True)
    panel.source = lambda: (foreign, disk)
    assert panel._rows() == ()
    panel.render(90)
    panel.handle_input(KeyEvent('enter'))
    assert not acts and panel._review is None
    assert caches == before  # Filtering/refusal never deletes existing data.


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
    assert len(acts) == 1 and acts[0][0] == 'reopen'


def test_changed_source_during_preview_invalidates_review_and_does_not_apply_action():
    panel, caches, original, acts = setup()
    panel.render(90)
    panel.handle_input(KeyEvent('enter'))
    panel.handle_input(KeyEvent('p'))
    assert panel._preview is not None
    caches['current'][('start', 'recovered/current/input')] = replace(original, text='new')
    panel.render(90)
    assert panel._preview is None and panel._review is None
    assert 'changed during preview' in panel._error and not acts


def test_image_preview_cycles_exact_attachments_without_changing_composer_or_source():
    panel, caches, original, acts = setup()
    second = replace(original.images[0], id='other-image', name='second.png')
    multiple = replace(original, images=(*original.images, second))
    caches['current'][('start', 'recovered/current/input')] = multiple
    panel.render(90)
    panel.handle_input(KeyEvent('p'))
    panel.handle_input(KeyEvent('right'))
    assert panel._preview.item is second
    panel.handle_input(KeyEvent('left'))
    assert panel._preview.item is multiple.images[0]
    assert caches['current'][('start', 'recovered/current/input')] is multiple and not acts


@pytest.mark.parametrize('width,budget', [(0, 8), (1, 8), (23, 8), (90, 0), (90, 2)])
def test_tiny_unicode_or_hidden_review_cannot_confirm(width, budget):
    from xdog.tui.utils import visible_width
    panel, caches, original, acts = setup()
    caches['current']['start', 'recovered/current/input'] = replace(original, text='宽 🐕\n' * 20)
    panel.render(90)
    panel.handle_input(KeyEvent('enter'))
    panel.set_render_budget(budget)
    assert all(visible_width(row) <= width for row in panel.render(width))
    panel.handle_input(KeyEvent('enter'))
    assert not acts


def test_blur_roundtrip_requires_new_full_review_and_close_releases_view_images():
    panel, _caches, original, acts = setup()
    panel.render(90)
    panel.handle_input(KeyEvent('enter'))
    paint_review(panel)
    lease = panel.view_lease
    panel.focused = False
    panel.focused = True
    assert panel.view_lease != lease and not panel._reviewed
    panel.handle_input(KeyEvent('enter'))
    assert not acts
    panel.handle_input(KeyEvent('p'))
    assert panel._preview.item is original.images[0]
    panel.close()
    assert not panel.source() and panel._preview is None and panel._review is None
    panel.handle_input(KeyEvent('enter'))
    assert not acts
