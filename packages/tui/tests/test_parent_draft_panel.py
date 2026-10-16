"""Primary recovery is a bounded explicit selector, never a child/management UI."""
from __future__ import annotations

import base64

import pytest
from xdog.tui.attachments import attachment
from xdog.tui.child_drafts import ChildDraft
from xdog.tui.components.child_draft_panel import DraftEntry
from xdog.tui.components.parent_draft_panel import ParentDraftPanel
from xdog.tui.keys import KeyEvent, KeyEventType
from xdog.tui.utils import visible_width


def make(mode='restore', *, images=False, count=2):
    image = attachment(base64.b64decode(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5ioAAAAASUVORK5CYII='), '照片.png')
    entries = []
    for i in range(count):
        key = f'{i:032x}'
        draft = ChildDraft('未发送 /quit\n' + '宽 Unicode 🐕\n' * 9,
                           images=(image,) if images else (), cursor=1)
        entries.append(DraftEntry('saved-draft:parent/' + key, ('parent', key), draft, draft,
                                  f'{key} · stored label · source opaque-old-context · parent/old-slot', False))
    acts, canceled = [], []
    panel = ParentDraftPanel(lambda: tuple(entries), lambda *args: acts.append(args),
                             lambda: canceled.append(True), context='current', mode=mode)
    panel.focused = True
    panel.set_render_budget(8)
    if mode == 'save':
        panel.prepare_save(entries[0])
    return panel, entries, acts, canceled


def paint(panel, width=90):
    panel.handle_input(KeyEvent('home'))
    for _ in range(100):
        panel.render(width)
        if panel._visible:
            return
        panel.handle_input(KeyEvent('pagedown'))
    raise AssertionError('Review not fully painted')


def test_selector_exact_id_provenance_and_no_management_controls():
    panel, entries, acts, canceled = make()
    lines = panel.render(240)
    assert all(row.key[1] in '\n'.join(lines) for row in entries)
    assert 'source opaque-old-context' in '\n'.join(lines)
    for key in ('x', 'delete', 's', 'r', '/'):
        panel.handle_input(KeyEvent(key))
        assert panel._review is None and not panel._searching
    panel.handle_input(KeyEvent('down'))
    panel.render(240)
    panel.handle_input(KeyEvent('enter'))
    assert panel._review[1] is entries[1] and not acts
    paint(panel)
    assert 'opaque-old-context' in '\n'.join(panel._body)
    assert 'old-slot' in '\n'.join(panel._body)
    panel.handle_input(KeyEvent('enter'))
    assert acts == [('copy', entries[1])] and not canceled


@pytest.mark.parametrize('mode', ['save', 'restore'])
def test_review_requires_physical_paint_no_repeat_release_paste_or_small_view_authority(mode):
    panel, entries, acts, _ = make(mode)
    if mode == 'restore':
        panel.render(90)
        panel.handle_input(KeyEvent('enter'))
    panel.handle_input(KeyEvent('enter'))
    paint(panel)
    for event_type in (KeyEventType.REPEAT, KeyEventType.RELEASE):
        panel.handle_input(KeyEvent('enter', event_type=event_type))
    panel.handle_paste('\n\n')
    assert not acts
    for width, budget in ((1, 8), (24, 0), (23, 8)):
        panel.set_render_budget(budget)
        assert all(visible_width(row) <= width for row in panel.render(width))
        panel.handle_input(KeyEvent('enter'))
        assert not acts
    panel.set_render_budget(8)
    paint(panel)
    panel.focused = False
    panel.focused = True
    panel.handle_input(KeyEvent('enter'))
    assert not acts  # Focus roundtrip invalidates proof, even without a render.
    paint(panel)
    panel.handle_input(KeyEvent('enter'))
    assert acts == [('save' if mode == 'save' else 'copy', entries[0])]


def test_preview_return_requires_fresh_review_and_never_changes_source():
    panel, entries, acts, _ = make(images=True)
    panel.render(90)
    panel.handle_input(KeyEvent('enter'))
    paint(panel)
    panel.handle_input(KeyEvent('p'))
    assert panel._preview.item is entries[0].value.images[0]
    panel.render(90)
    panel.handle_input(KeyEvent('escape'))
    panel.handle_input(KeyEvent('enter'))
    assert not acts and not panel._reviewed
    paint(panel)
    panel.handle_input(KeyEvent('enter'))
    assert acts == [('copy', entries[0])]


@pytest.mark.parametrize('mode', ['loading', 'save', 'restore'])
def test_cancel_has_no_restore_save_or_deletion_effect(mode):
    panel, entries, acts, canceled = make(mode)
    lease = panel.view_lease
    panel.focused = False
    panel.handle_input(KeyEvent('escape'))
    assert not canceled and panel.view_lease != lease
    panel.focused = True
    panel.handle_input(KeyEvent('escape'))
    assert canceled == [True] and not acts and len(entries) == 2


def test_selector_capacity_is_bounded_and_has_no_latest_inference():
    panel, entries, _, _ = make(count=32)
    panel.render(90)
    assert panel._selected == entries[0].id
    # Rendering and reordering do not silently follow a new "latest" entry.
    entries.reverse()
    panel.render(90)
    assert panel._selected == entries[-1].id
    extra = entries[-1]
    entries.append(extra)
    with pytest.raises(ValueError, match='capacity'):
        panel.render(90)


def test_refused_save_requires_explicit_reopen_not_selector_or_replay():
    panel, _, acts, canceled = make('save')
    def refuse(*_):
        raise ValueError('Composer changed; reopen review')
    panel.act = refuse
    paint(panel)
    panel.handle_input(KeyEvent('enter'))
    assert panel._review is None and not acts
    assert 'Primary save not admitted' in '\n'.join(panel.render(90))
    for key in ('enter', 's', 'x', 'r', '/'):
        panel.handle_input(KeyEvent(key))
        assert panel._review is None and not panel._visible
    panel.handle_input(KeyEvent('escape'))
    assert canceled == [True]


def test_dismissed_primary_surface_releases_preview_review_and_transient_image_source():
    panel, entries, acts, _ = make(images=True)
    panel.render(90)
    panel.handle_input(KeyEvent('enter'))
    panel.handle_input(KeyEvent('p'))
    assert panel._preview_entry is entries[0] and panel._review[1] is entries[0]
    panel.close()
    assert panel.source() == () and panel._preview is None and panel._preview_entry is None
    assert panel._review is None and not panel._body and not panel.focused
    panel.handle_input(KeyEvent('enter'))
    assert not acts and entries[0].value.images  # Dismissal never changes independent source snapshots.
