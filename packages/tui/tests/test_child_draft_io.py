"""Durable draft worker ownership must not block a terminal frame or strand startup."""
from __future__ import annotations

import queue
import threading
import time

import pytest
from xdog.tui.child_draft_io import ChildDraftIO
from xdog.tui.child_draft_store import ChildDraftStore


class EmptyStore:
    def list(self):
        return ()


def finish(worker, delivered):
    deadline = time.monotonic() + 3
    while worker.busy and time.monotonic() < deadline:
        worker.poll(delivered.append)
        time.sleep(0.001)
    assert not worker.busy


def test_published_receipt_keeps_capacity_until_worker_exit_without_joining_ui(monkeypatch):
    published, release = threading.Event(), threading.Event()
    class HeldResults:
        def __init__(self):
            self.queue = queue.SimpleQueue()
        def put(self, result):
            self.queue.put(result)
            published.set()
            assert release.wait(3)
        def get_nowait(self):
            return self.queue.get_nowait()
    worker = ChildDraftIO()
    worker._results = HeldResults()
    delivered = []
    try:
        worker.start('list', EmptyStore(), owner='original-view')
        assert published.wait(3)
        thread = worker._thread
        def no_join(*args, **kwargs):
            raise AssertionError('UI polling must not join an owned worker')
        monkeypatch.setattr(thread, 'join', no_join)
        assert worker.poll(delivered.append) is False
        assert worker.busy and not delivered
        with pytest.raises(ValueError, match='still owns work'):
            worker.start('list', EmptyStore(), owner='other-view')
        release.set()
        finish(worker, delivered)
        assert delivered == [{'action': 'list', 'owner': 'original-view', 'saved': ()}]
        assert worker.poll(delivered.append) is False
    finally:
        release.set()
        if worker._thread is not None:
            finish(worker, delivered)


def test_failed_thread_start_releases_ownership_and_allows_fresh_operation(tmp_path, monkeypatch):
    worker = ChildDraftIO()
    called = []
    class Store:
        def list(self):
            called.append(True)
            return ()
    original = threading.Thread.start
    def fail(self):
        raise RuntimeError('PRIVATE STARTUP FAILURE')
    monkeypatch.setattr(threading.Thread, 'start', fail)
    with pytest.raises(ValueError, match='no operation admitted') as error:
        worker.start('list', Store(), owner='view')
    assert 'PRIVATE' not in str(error.value)
    assert not worker.busy and not called
    assert worker.poll(lambda event: pytest.fail('No failed-start receipt exists')) is False
    monkeypatch.setattr(threading.Thread, 'start', original)
    delivered = []
    worker.start('list', Store(), owner='fresh-view')
    finish(worker, delivered)
    assert called == [True] and delivered[0]['owner'] == 'fresh-view'


def test_delivery_failure_releases_worker_without_automatic_receipt_replay(tmp_path):
    worker = ChildDraftIO()
    worker.start('list', ChildDraftStore(tmp_path / 'absent'), owner='view')
    deadline = time.monotonic() + 3
    while worker._thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.001)
    def reject(event):
        raise RuntimeError('Owner rejected receipt')
    with pytest.raises(RuntimeError, match='rejected receipt'):
        worker.poll(reject)
    assert not worker.busy
    assert worker.poll(lambda event: pytest.fail('Rejected receipt must not be replayed')) is False
