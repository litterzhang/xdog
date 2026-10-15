"""Worker settlement must be independent of whether its coroutine was entered."""
import asyncio
import threading

from xdog.tui.terminal_interception import HeldTerminalInput
from xdog.tui.terminal_worker import TerminalHookWorker


def test_terminal_worker_reports_settlement_when_cancelled_before_callback_entry(monkeypatch):
    worker = TerminalHookWorker(lambda event: None)
    entered, release, settled = threading.Event(), threading.Event(), threading.Event()
    called = []
    work = worker._work
    def gated(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        work(*args, **kwargs)
    monkeypatch.setattr(worker, '_work', gated)
    async def run(item):
        called.append('entered')
        await asyncio.Event().wait()
    def finished():
        called.append('settled')
        settled.set()
    try:
        assert worker.send(HeldTerminalInput(1, 'owner', 'text', 'prompt', 'key'), run, settled=finished)
        assert entered.wait(3)
        worker.cancel()
        release.set()
        assert settled.wait(3)
        worker._thread.join(3)
        assert called == ['settled']
        assert not worker._busy
    finally:
        release.set()
        worker.cancel()
