"""Actual Coding root review plus fail-closed older Claw UI, not complete UI-048."""
from __future__ import annotations

import json
import sys

from xdog.tui.components.mcp_roots_panel import McpRootsReview
from xdog.tui.keys import KeyEvent

from packages.agent.tests.test_mcp_roots import ROOTS, write_peer
from packages.coding.tests.test_saved_child_drafts import close, make, wait


def test_full_paint_focus_resize_and_single_use_root_confirmation():
    calls = []
    panel = McpRootsReview({'state': 'review', 'review_id': 'a' * 32, 'server': 'peer',
        'roots': ROOTS, 'notice': 'Review ' * 50}, calls.append, lambda: calls.append('cancel'))
    panel.focused = True
    panel.handle_input(KeyEvent('enter'))
    assert not calls
    panel.set_render_budget(4)
    for _ in range(100):
        panel.render(40)
        if panel._visible:
            break
        panel.handle_input(KeyEvent('pagedown'))
    assert panel._visible
    panel.focused = False
    panel.focused = True
    panel.handle_input(KeyEvent('enter'))
    assert not calls
    panel.render(80)  # Width change clears painted evidence.
    assert not panel._visible
    for _ in range(100):
        panel.render(80)
        if panel._visible:
            break
        panel.handle_input(KeyEvent('pagedown'))
    panel.handle_input(KeyEvent('enter'))
    panel.handle_input(KeyEvent('enter'))
    assert calls == ['a' * 32]


async def test_actual_coding_native_roots_form_review_commit_and_parent_preservation(tmp_path, monkeypatch):
    peer, log = tmp_path / 'peer.py', tmp_path / 'frames'
    write_peer(peer)
    settings = tmp_path / 'settings.json'
    settings.write_text(json.dumps({'mcp_servers': {'owned': {'command': sys.executable,
        'args': [str(peer), str(log), 'legacy'], 'roots': ROOTS}}}))
    ui, session, calls = make(tmp_path, 'coding', monkeypatch)
    try:
        await session.manage_mcp('connect', 'owned')
        ui._editor.set_text('PARENT UNSENT')
        ui._open_mcp()
        await wait(ui, lambda: not ui._mcp_panel._busy)
        panel = ui._mcp_panel
        panel.handle_input(KeyEvent('o'))
        assert panel._mode == 'roots'
        panel.handle_input(KeyEvent('enter'))
        await wait(ui, lambda: panel.roots_review is not None)
        review = panel.roots_review
        review.handle_input(KeyEvent('enter'))
        assert session.mcp._clients['owned'].root_capability.roots
        for _ in range(100):
            panel.render(80)
            if review._visible:
                break
            review.handle_input(KeyEvent('pagedown'))
        assert review._visible
        review.handle_input(KeyEvent('enter'))
        await wait(ui, lambda: panel.roots_review is None and not panel._busy)
        assert session.mcp._clients['owned'].root_capability.roots == ()
        assert ui._editor.get_text() == 'PARENT UNSENT' and not calls
        await session.mcp._bridge(session.mcp._clients['owned'].request('ping', {}))
        frames = [json.loads(line) for line in log.read_text().splitlines()]
        assert sum(row.get('method') == 'notifications/roots/list_changed' for row in frames) == 1
    finally:
        await close(ui, session)
        await session.mcp.aclose()


async def test_claw_unavailable_dynamic_roots_never_routes_as_prompt(tmp_path, monkeypatch):
    ui, session, calls = make(tmp_path, 'claw', monkeypatch)
    sent = []
    monkeypatch.setattr(ui, '_queue_request', sent.append)
    try:
        ui._open_mcp()
        sent.clear()
        ui._mcp_send('roots_prepare', 'peer', '[]', {})
        assert not sent and not calls
        assert 'unavailable' in ui._mcp_panel._message
    finally:
        await close(ui, session)


async def test_coding_close_retires_prepared_root_review_without_publishing(tmp_path, monkeypatch):
    peer, log = tmp_path / 'peer.py', tmp_path / 'frames'
    write_peer(peer)
    (tmp_path / 'settings.json').write_text(json.dumps({'mcp_servers': {'owned': {
        'command': sys.executable, 'args': [str(peer), str(log), 'legacy'], 'roots': ROOTS}}}))
    ui, session, calls = make(tmp_path, 'coding', monkeypatch)
    try:
        await session.manage_mcp('connect', 'owned')
        ui._open_mcp()
        await wait(ui, lambda: not ui._mcp_panel._busy)
        panel = ui._mcp_panel
        panel.handle_input(KeyEvent('o'))
        panel.handle_input(KeyEvent('enter'))
        await wait(ui, lambda: panel.roots_review is not None)
        identifier = panel.roots_review.review['review_id']
        lease = ui._mcp_auth_lease
        panel.handle_paste('[{"uri":"file:///forged"}]')
        assert panel._arguments.get_text() == '[]'
        ui._close_mcp()
        await wait(ui, lambda: not session.mcp._root_consents)
        assert session.mcp._clients['owned'].root_capability.roots
        try:
            await session.mcp.manage_roots('owned', 'commit', receipt=identifier, lease=lease, alive=lambda: True)
        except Exception:
            pass
        else:
            raise AssertionError('Closed view root receipt was admitted')
        assert not calls
    finally:
        await close(ui, session)
        await session.mcp.aclose()


def test_root_review_inspection_copy_cannot_change_painted_disclosure():
    calls = []
    panel = McpRootsReview({'state': 'review', 'review_id': 'a' * 32, 'server': 'peer',
        'roots': ROOTS, 'notice': 'Explicit disclosure'}, calls.append, lambda: None)
    panel.focused = True
    panel.render(100)
    assert panel._visible
    copied = panel.review
    copied['review_id'] = 'b' * 32
    copied['roots'][0]['uri'] = 'file:///changed'
    panel.handle_input(KeyEvent('enter'))
    assert calls == ['a' * 32] and panel.review['roots'] == ROOTS


def test_long_unicode_root_review_requires_every_wrapped_row_and_keeps_tail():
    calls, rendered = [], []
    uri = 'file:///explicit/' + '界🙂' * 200 + '/END-ROOT-TAIL'
    panel = McpRootsReview({'state': 'review', 'review_id': 'a' * 32, 'server': 'peer',
        'roots': [{'uri': uri}], 'notice': 'Inspect exact metadata'}, calls.append, lambda: None)
    panel.focused = True
    panel.set_render_budget(4)
    for _ in range(300):
        rows = panel.render(40)
        from xdog.tui.utils import string_width
        assert all(string_width(row) <= 40 for row in rows)
        rendered.extend(rows)
        if panel._visible:
            break
        panel.handle_input(KeyEvent('enter'))
        assert not calls
        panel.handle_input(KeyEvent('pagedown'))
    assert panel._visible and 'END-ROOT-TAIL' in ''.join(rendered)
    panel.handle_input(KeyEvent('enter'))
    assert calls == ['a' * 32]
