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
