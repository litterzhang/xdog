from __future__ import annotations

import threading
import time

from xdog.tui.components.navigation_panel import NavigationPanel
from xdog.tui.keys import KeyEvent
from xdog.tui.navigation import NavigationEntry, NavigationFeed, NavigationSnapshot, NavigationWorker, navigation_page
from xdog.tui.utils import visible_width


def test_search_applies_to_all_entries_not_just_first_page_and_pages_are_bounded():
    entries = tuple(NavigationEntry(str(index), f"session-{index}") for index in range(120))
    first = navigation_page(entries, "sessions", "", 0)
    assert len(first.entries) == 50 and first.total == 120
    third = navigation_page(entries, "sessions", "", 100)
    assert len(third.entries) == 20
    target = navigation_page(entries, "sessions", "session-119", 0)
    assert [entry.id for entry in target.entries] == ["119"]


def test_worker_coalesces_and_fences_old_reads_and_closes_without_ui_mutation():
    entered, release = threading.Event(), threading.Event()
    calls = []

    def fetch(scope):
        calls.append(scope)
        if scope == "old":
            entered.set()
            assert release.wait(3)
        return (NavigationEntry(scope, scope),)

    worker = NavigationWorker(fetch)
    try:
        worker.request("old")
        assert entered.wait(3)
        worker.request("discarded")
        worker.request("new")
        release.set()
        deadline = time.monotonic() + 3
        while worker.snapshot().busy and time.monotonic() < deadline:
            time.sleep(0.005)
        assert worker.snapshot().entries[0].id == "new"
        assert calls == ["old", "new"]
    finally:
        release.set()
        worker.close()
        worker._thread.join(3)
        assert not worker._thread.is_alive()


def test_feed_ignores_stale_receipt_and_panel_search_tree_paste_and_tiny_layout():
    requests = []
    feed = NavigationFeed(lambda *args: requests.append(args))
    activated = []
    closed = []
    panel = NavigationPanel(feed, activated.append, lambda: closed.append(True))
    serial = requests[-1][0]
    entries = (NavigationEntry("one", "label", session_id="one"),)
    feed.publish(serial, NavigationSnapshot(entries=entries, total=1))
    panel.poll()
    panel.handle_input(KeyEvent("right"))
    new_serial = requests[-1][0]
    assert requests[-1][1] == "tree|one|"
    feed.publish(serial, NavigationSnapshot(entries=(), total=0))
    assert feed.snapshot().busy
    feed.publish(new_serial, NavigationSnapshot(scope="tree|one|", entries=entries, total=1))
    panel.poll()
    for budget in (0, 1, 2, 4, 12):
        panel.set_render_budget(budget)
        assert len(panel.render(20)) <= budget
        assert all(visible_width(row) <= 20 for row in panel.render(20))
    panel.handle_input(KeyEvent("/"))
    panel.handle_paste("query\x1b[2J")
    assert requests[-1][2] == "query"
    panel.handle_input(KeyEvent("escape"))
    assert not closed
    panel.handle_input(KeyEvent("escape"))
    assert closed == [True]
    assert not activated


def test_new_navigation_view_immediately_reports_its_actual_pending_read_and_cannot_activate_empty_state():
    requests, activated = [], []
    feed = NavigationFeed(lambda *args: requests.append(args))
    panel = NavigationPanel(feed, activated.append, lambda: None, scope='tree|owner|')
    assert panel._view.busy and panel._view.serial == requests[-1][0]
    assert 'loading' in panel.render(100)[0]
    panel.handle_input(KeyEvent('enter'))
    assert not activated
    entry = NavigationEntry('saved', 'Recovery', kind='branch', session_id='owner', branch_id='saved')
    feed.publish(requests[-1][0], NavigationSnapshot(scope='tree|owner|', entries=(entry,), total=1))
    panel.poll()
    assert not panel._view.busy and 'loading' not in panel.render(100)[0]
    panel.handle_input(KeyEvent('enter'))
    assert activated == [entry]


def test_synchronous_navigation_reply_is_not_replaced_by_a_false_pending_or_empty_snapshot():
    entry = NavigationEntry('owner', 'Current', session_id='owner')
    def response(serial, scope, query, offset):
        feed.publish(serial, NavigationSnapshot(scope=scope, entries=(entry,), total=1))
    feed = NavigationFeed(response)
    panel = NavigationPanel(feed, lambda value: None, lambda: None)
    assert not panel._view.busy and panel._view.entries == (entry,)


def test_session_rows_are_single_terminal_lines_before_and_after_arrow_navigation():
    from xdog.tui.main_buffer_renderer import MainBufferRenderer

    from packages.tui.tests.test_main_buffer_renderer import Screen

    feed = NavigationFeed()
    panel = NavigationPanel(feed, lambda entry: None, lambda: None)
    panel.set_render_budget(5)
    feed.publish(feed.snapshot().serial, NavigationSnapshot(total=3, entries=(
        NavigationEntry('one', 'first\ncontinued', detail='model\tworkspace'),
        NavigationEntry('two', 'second\r\ncontinued', detail='detail\nnext'),
        NavigationEntry('three', '\x1b[31mthird\x1b[0m\tname'),
    )))
    panel.poll()
    renderer = MainBufferRenderer(origin_row=2)
    screen = Screen(height=10, width=80)
    for key in (None, 'down', 'down', 'up', 'up'):
        if key is not None:
            panel.handle_input(KeyEvent(key))
        rows = panel.render(80)
        assert all('\n' not in row and '\r' not in row and '\t' not in row for row in rows)
        screen.feed(renderer.render(rows, 80, 10, transient_start=0))
        assert screen.rows[2:2 + len(rows)] == rows
        assert not screen.history
    screen.feed(renderer.render([], 80, 10, transient_start=0))
    assert screen.rows == ['shell before 1', 'shell before 2'] + [''] * 8


def test_navigation_error_and_search_are_single_terminal_lines():
    feed = NavigationFeed()
    panel = NavigationPanel(feed, lambda entry: None, lambda: None)
    feed.publish(feed.snapshot().serial, NavigationSnapshot(error='read failed\npath\tname'))
    panel.poll()
    panel.focused = True
    panel.handle_input(KeyEvent('/'))
    panel.handle_paste('query\twith\nwhitespace')
    assert all('\n' not in row and '\r' not in row and '\t' not in row for row in panel.render(80))
