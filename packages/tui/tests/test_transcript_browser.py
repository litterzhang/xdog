from __future__ import annotations

import time
from dataclasses import replace

from xdog.tui.components.transcript_panel import TranscriptPanel
from xdog.tui.keys import KeyEvent
from xdog.tui.transcript_browser import BrowserRequest, TranscriptBrowser, TranscriptRecord
from xdog.tui.utils import strip_ansi, visible_width


def wait(browser):
    deadline = time.monotonic() + 3
    while browser.snapshot().busy and time.monotonic() < deadline:
        time.sleep(0.002)
    view = browser.snapshot()
    assert not view.busy and not view.error
    return view


def test_full_history_search_every_occurrence_unicode_horizontal_and_no_truncation():
    records = (TranscriptRecord("one", "tool", "result", "\n".join(f"line-{i}" for i in range(25000))),
               TranscriptRecord("two", "assistant", "response", "x" * 2000 + "Straße STRASSE\nlast"))
    browser = TranscriptBrowser()
    request = BrowserRequest(records=records, query="strasse", match=0, width=20, height=4)
    try:
        browser.request(request)
        view = wait(browser)
        assert view.total == 25002 and view.cursor == 25000 and view.matches == 2 and view.match == 0
        assert "Straße" in next(row.text for row in view.rows if row.index == view.cursor)
        assert view.horizontal == 2000
        browser.request(replace(request, match=1))
        view = wait(browser)
        assert view.match == 1 and view.cursor == 25000
        assert "STRASSE" in next(row.text for row in view.rows if row.index == view.cursor)
        browser.request(replace(request, query="line-24999", match=0))
        assert wait(browser).cursor == 24999
        assert all(visible_width(row.text) <= 20 for row in browser.snapshot().rows)
    finally:
        browser.close()
        browser._thread.join(3)


def test_snapshot_coalescing_stable_cursor_after_growth_selection_and_clear():
    first = TranscriptRecord("one", "reasoning", "thought", "line")
    second = TranscriptRecord("two", "assistant", "response", "a\nb\nc")
    browser = TranscriptBrowser()
    try:
        browser.request(BrowserRequest(records=(first, second), cursor=2))
        assert wait(browser).cursor == 2
        browser.request(BrowserRequest(records=(replace(first, body="grew\nline\nextra"), second), cursor=2))
        assert wait(browser).cursor == 4  # Same source entry and source line, not old absolute row.
        for index in range(100):
            browser.request(BrowserRequest(records=(second,), cursor=index, anchor=0, width=8))
        view = wait(browser)
        assert view.cursor == 2 and view.version == 102 and all(row.selected for row in view.rows)
        assert TranscriptBrowser.selected_text((second,), 0, 2) == "a\nb\nc"
        browser.request(BrowserRequest())
        assert not wait(browser).rows
    finally:
        browser.close()
        browser._thread.join(3)
    prior = browser.snapshot()
    browser.request(BrowserRequest(records=(second,)))
    assert browser.snapshot() == prior


def test_panel_search_selection_paste_navigation_tiny_budgets_and_focus():
    records = (TranscriptRecord("one", "user", "input", "needle\nline"),
               TranscriptRecord("two", "tool", "bash", "secret needle\nlast"))
    closed, fullscreen = [], []
    panel = TranscriptPanel(lambda: records, lambda: closed.append(True), lambda: fullscreen.append(True))
    try:
        wait(panel.browser)
        panel.poll()
        panel.handle_input(KeyEvent("/"))
        panel.handle_paste("needle")
        wait(panel.browser)
        panel.poll()
        assert panel._view.matches == 2
        panel.handle_input(KeyEvent("enter"))
        panel.handle_input(KeyEvent("n"))
        wait(panel.browser)
        panel.poll()
        assert panel._view.cursor == 2
        panel.handle_input(KeyEvent("a"))
        wait(panel.browser)
        panel.poll()
        assert panel.selection == (2, 3)
        assert TranscriptBrowser.selected_text(panel.records, *panel.selection) == "secret needle\nlast"
        panel.handle_input(KeyEvent("escape"))
        assert not closed
        wait(panel.browser)
        panel.poll()
        assert panel.selection is None
        panel.handle_input(KeyEvent("f"))
        assert fullscreen == [True]
        for height in (0, 1, 2, 8, 24):
            panel.fullscreen = height == 24
            panel.set_render_budget(height)
            rows = panel.render(16)
            wait(panel.browser)
            panel.poll()
            rows = panel.render(16)
            assert len(rows) <= height and all(visible_width(row) <= 16 for row in rows)
        panel.handle_paste("ignored paste outside search")
        panel.handle_input(KeyEvent("escape"))
        assert closed == [True]
        assert "ignored" not in str(panel.records)
    finally:
        panel.close()
        panel.browser._thread.join(3)


def test_source_sanitization_preserves_text_without_terminal_commands():
    browser = TranscriptBrowser()
    try:
        browser.request(BrowserRequest(records=(TranscriptRecord("one", "system", "title", "\x1b[2Jtext\x00\tend"),)))
        row = wait(browser).rows[0]
        assert strip_ansi(row.text) == "text    end" and "\x1b" not in row.text
    finally:
        browser.close()
        browser._thread.join(3)


def test_end_refreshes_new_source_and_search_selection_survives_pending_geometry():
    records = [TranscriptRecord("one", "assistant", "response", "old"),
               TranscriptRecord("two", "assistant", "response", "target\ncomplete")]
    panel = TranscriptPanel(lambda: tuple(records), lambda: None, lambda: None)
    try:
        wait(panel.browser)
        panel.poll()
        panel.search("target")
        panel.handle_input(KeyEvent("a"))  # Queue entry selection BEFORE the search receipt.
        assert panel.selection is None
        wait(panel.browser)
        panel.poll()
        assert panel.selection == (1, 2)
        records.append(TranscriptRecord("three", "tool", "later", "new\nlatest"))
        panel.handle_input(KeyEvent("end"))
        wait(panel.browser)
        panel.poll()
        assert panel._view.cursor == 4 and panel._view.title == "tool: later"
        assert panel.selection is None
        panel.handle_input(KeyEvent("/"))
        panel.handle_paste("界e\u0301")
        panel.handle_input(KeyEvent("backspace"))
        assert panel._request.query.endswith("界")
        rows = panel.render(24)
        from xdog.tui.tui import CURSOR_MARKER
        assert CURSOR_MARKER in rows[-1]
    finally:
        panel.close()
        panel.browser._thread.join(3)


def test_casefold_expanded_character_matches_pan_to_original_grapheme():
    browser = TranscriptBrowser()
    try:
        request = BrowserRequest(records=(TranscriptRecord("one", "assistant", "text", "x" * 100 + "ß"),),
                                 query="s", match=1, width=1)
        browser.request(request)
        view = wait(browser)
        assert view.matches == 2 and view.match == 1
        assert view.horizontal == 100 and view.rows[0].text == "ß"
    finally:
        browser.close()
        browser._thread.join(3)
    assert not browser._records and not browser._lines


def test_remote_source_pending_search_and_latest_survive_old_view_receipts():
    refreshed = []
    panel = TranscriptPanel(lambda: (TranscriptRecord("old", "user", "old", "old source"),),
                            lambda: None, lambda: None, on_refresh=lambda: refreshed.append(True))
    try:
        wait(panel.browser)
        panel.poll()
        panel.set_source_status("loading", busy=True)
        panel.search("remote needle")
        wait(panel.browser)
        panel.poll()
        assert panel._request.match == 0  # Do not turn a pending search into a manual cursor.
        panel.replace_records((TranscriptRecord("new", "tool", "new", "x" * 50000 + "remote needle"),))
        wait(panel.browser)
        panel.poll()
        assert panel._view.matches == 1 and panel._view.match == 0
        panel.handle_input(KeyEvent("end"))
        assert refreshed == [True]
        panel.set_source_status("loading latest", busy=True)
        panel.render(24)  # Geometry may produce an old-dataset receipt before remote arrival.
        wait(panel.browser)
        panel.poll()
        panel.replace_records((TranscriptRecord("new", "tool", "new", "remote needle\nmore\nlatest"),))
        wait(panel.browser)
        panel.poll()
        assert panel._view.cursor == 2
    finally:
        panel.close()
        panel.browser._thread.join(3)


def test_completed_worker_is_not_settled_until_live_panel_has_adopted_its_view():
    records = (TranscriptRecord('one', 'system', 'resource', 'original resource snapshot'),)
    panel = TranscriptPanel(lambda: records, lambda: None, lambda: None)
    try:
        panel.render(120)  # Establish the same dimensions used by the subsequent render.
        panel.handle_input(KeyEvent('v'))
        completed = wait(panel.browser)
        # Model the race where the worker finishes just after poll adopted its
        # loading snapshot, before the UI tests settled or draws another frame.
        panel._view = replace(completed, busy=True, rows=(), total=0)
        panel._version = completed.version
        assert not panel.settled and panel.entry_id == ''
        assert panel.selection is None
        rendered = strip_ansi('\n'.join(panel.render(120)))
        assert 'Loading transcript' in rendered and 'No transcript available' not in rendered
        assert panel.poll() and panel.settled
        assert panel.entry_id == 'one' and panel.selection == (0, 0)
        assert 'original resource snapshot' in strip_ansi('\n'.join(panel.render(120)))
    finally:
        panel.close()
        panel.browser._thread.join(3)
