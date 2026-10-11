"""Saved read ownership is coalesced and inert after close, including unavailable filesystem reads."""
import threading
import time

from xdog.tui.saved_source import SavedSourceWorker
from xdog.tui.transcript_browser import TranscriptRecord


def test_one_worker_coalesces_late_results_and_retires_closed_reads():
    events = []
    worker = SavedSourceWorker(events.append)
    entered, release = threading.Event(), threading.Event()
    def blocked():
        entered.set()
        assert release.wait(5)
        return (TranscriptRecord('old', 'user', 'input', 'old private data'),)
    try:
        worker.request('old', 'original-owner', blocked)
        assert entered.wait(3)
        original = worker._thread
        worker.request('new', 'new-owner', lambda: (TranscriptRecord('new', 'user', 'input', 'fresh'),))
        release.set()
        deadline = time.monotonic() + 3
        while not events and time.monotonic() < deadline:
            time.sleep(0.003)
        assert len(events) == 1 and events[0]['id'] == 'new'
        assert worker._thread is original
        entered.clear()
        release.clear()
        worker.request('closed', 'same-owner', blocked)
        assert entered.wait(3)
        worker.close()
        release.set()
        original.join(3)
        assert not original.is_alive() and len(events) == 1
    finally:
        release.set()
        worker.close()


def test_failed_saved_read_reports_unavailable_not_empty_or_live_fallback():
    events = []
    worker = SavedSourceWorker(events.append)
    def fail():
        raise OSError('private path details must not leak')
    try:
        worker.request('read', 'owner', fail)
        deadline = time.monotonic() + 3
        while not events and time.monotonic() < deadline:
            time.sleep(0.003)
        assert events[0]['type'] == 'saved_source_failed' and 'records' not in events[0]
        assert 'private path' not in events[0]['error']
    finally:
        worker.close()


def test_navigation_saved_view_gesture_requires_focus_press_and_does_not_activate():
    from xdog.tui.components.navigation_panel import NavigationPanel
    from xdog.tui.keys import KeyEvent, KeyEventType
    from xdog.tui.navigation import NavigationEntry, NavigationFeed, NavigationSnapshot
    viewed, activated = [], []
    feed = NavigationFeed()
    panel = NavigationPanel(feed, activated.append, lambda: None, on_view=viewed.append)
    entry = NavigationEntry('saved', 'Saved context', kind='branch', session_id='owner', branch_id='archive')
    feed.publish(feed._serial, NavigationSnapshot(entries=(entry,), total=1))
    panel.poll()
    panel.handle_input(KeyEvent('v'))
    assert not viewed and not activated
    panel.focused = True
    panel.handle_input(KeyEvent('v', event_type=KeyEventType.RELEASE))
    assert not viewed
    panel.handle_input(KeyEvent('v'))
    assert viewed == [entry] and not activated
