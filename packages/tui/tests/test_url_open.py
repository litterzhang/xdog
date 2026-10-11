"""Owned offline desktop helper lifecycle, never opens a user's browser/vault."""
from __future__ import annotations

import subprocess
import sys
import threading
import time

import pytest
from xdog.tui.url_open import ReviewedUrlOpener, open_reviewed_url


@pytest.mark.parametrize('behavior', ['success', 'failure', 'held'])
def test_owned_url_helper_no_shell_environment_override_and_only_owned_cleanup(tmp_path, monkeypatch, behavior):
    import xdog.tui.url_open as module
    script = tmp_path / 'owned.py'
    script.write_text('import sys,time\n' + ('time.sleep(30)\n' if behavior == 'held' else '')
                      + ('sys.exit(1)\n' if behavior == 'failure' else ''))
    real = subprocess.Popen
    owned = []
    def launch(args, **kwargs):
        assert args == ['/usr/bin/xdg-open', 'https://operator.example/flow']
        assert not kwargs.get('shell') and 'BROWSER' not in kwargs['env']
        child = real([sys.executable, '-I', str(script)], **kwargs)
        owned.append(child)
        return child
    monkeypatch.setattr(module.subprocess, 'Popen', launch)
    monkeypatch.setenv('BROWSER', 'UNREVIEWED-SHELL')
    monkeypatch.setattr(module.os.path, 'isfile', lambda path: True)
    started = time.monotonic()
    def alive():
        return behavior != 'held' or time.monotonic() - started < .1
    if behavior == 'success':
        notice = open_reviewed_url('https://operator.example/flow', alive, threading.Event())
        assert 'not interaction completion' in notice
    else:
        with pytest.raises(ValueError):
            open_reviewed_url('https://operator.example/flow', alive, threading.Event())
    assert all(child.poll() is not None for child in owned)
    assert time.monotonic() - started < 3


@pytest.mark.parametrize('url', ['file:///private', 'http://example.test', 'https://u:p@host/flow',
                               'https://host/\nunsafe'])
def test_unreviewed_or_unsafe_url_never_launches(url, monkeypatch):
    import xdog.tui.url_open as module
    def fail(*args, **kwargs):
        pytest.fail('Unsafe URL must not start helper')
    monkeypatch.setattr(module.subprocess, 'Popen', fail)
    with pytest.raises(ValueError):
        open_reviewed_url(url, lambda: True, threading.Event())


def test_worker_owns_capacity_until_actual_exit_and_closed_view_gets_no_receipt(monkeypatch):
    import xdog.tui.url_open as module
    entered, release = threading.Event(), threading.Event()
    def held(url, alive, canceled):
        entered.set()
        assert release.wait(3)
        return 'done'
    monkeypatch.setattr(module, 'open_reviewed_url', held)
    owner, notices = ReviewedUrlOpener(), []
    try:
        owner.start('https://operator.example', lambda: True)
        assert entered.wait(2)
        with pytest.raises(ValueError, match='owns work'):
            owner.start('https://operator.example', lambda: True)
        owner.cancel()
        assert owner.busy and not owner.poll(notices.append)
        release.set()
        deadline = time.monotonic() + 3
        while owner.busy and time.monotonic() < deadline:
            owner.poll(notices.append)
            time.sleep(.001)
        assert not owner.busy and not notices
    finally:
        release.set()
