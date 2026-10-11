"""Explicit Linux URL-opening helper, owned off-UI-thread and never retried.

Opening can have external effects before cancellation. Only the helper created here
is terminated; an independently launched user browser/service is never killed.
"""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from urllib.parse import urlsplit


def open_reviewed_url(url: str, alive: Callable[[], bool], canceled: threading.Event) -> str:
    if (not isinstance(url, str) or not url or len(url.encode()) > 8192
            or any(ord(c) < 0x21 or 0x7f <= ord(c) <= 0x9f for c in url)):
        raise ValueError('Unsafe reviewed URL')
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None or parsed.password is not None):
        raise ValueError('Only explicitly reviewed HTTPS URLs can be opened')
    if canceled.is_set() or not alive():
        raise ValueError('URL view revoked before opening')
    if not sys.platform.startswith('linux') or not os.path.isfile('/usr/bin/xdg-open'):
        raise ValueError('Current Linux URL helper unavailable; no implicit fallback')
    # Explicit system helper, no shell/ambient BROWSER override. No URL or error
    # content enters diagnostics. Browser selection belongs to desktop configuration.
    env = {key: value for key, value in os.environ.items() if key not in {'BROWSER', 'PYTHONPATH', 'PYTHONHOME'}}
    if canceled.is_set() or not alive():
        raise ValueError('URL owner changed before helper admission')
    process = subprocess.Popen(['/usr/bin/xdg-open', url], stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
    try:
        deadline = time.monotonic() + 5
        while process.poll() is None:
            if canceled.is_set() or not alive() or time.monotonic() >= deadline:
                raise ValueError('URL opening revoked/timed out; external effects uncertain, no replay')
            time.sleep(.02)
        if canceled.is_set() or not alive():
            raise ValueError('URL view revoked after opening; external effects uncertain')
        if process.returncode != 0:
            raise ValueError('URL helper refused/failed; no automatic retry')
        return 'Reviewed URL passed to desktop helper; this is not interaction completion or login success'
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=.5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1)


class ReviewedUrlOpener:
    """One worker retained through actual completion; no history/model/transport policy."""
    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self._results: queue.SimpleQueue[tuple[Callable[[], bool], str]] = queue.SimpleQueue()

    @property
    def busy(self) -> bool:
        return self._thread is not None

    def start(self, url: str, alive: Callable[[], bool]) -> None:
        if self.busy:
            raise ValueError('URL helper still owns work; no repeat admission')
        self._cancel = threading.Event()
        canceled = self._cancel
        def work() -> None:
            try:
                message = open_reviewed_url(url, alive, canceled)
            except Exception:
                message = 'URL helper canceled/unavailable/failed; opening effects may be uncertain, no automatic retry'
            self._results.put((alive, message))
        thread = threading.Thread(target=work, daemon=True, name='xdog-reviewed-url')
        self._thread = thread
        try:
            thread.start()
        except RuntimeError:
            self._thread = None
            raise ValueError('URL helper worker unavailable; nothing opened') from None

    def cancel(self) -> None:
        self._cancel.set()

    def poll(self, notice: Callable[[str], None]) -> bool:
        if self._thread is None or self._thread.is_alive():
            return False
        self._thread = None
        alive, message = self._results.get_nowait()
        if alive() and not self._cancel.is_set():
            notice(message)
        return True
