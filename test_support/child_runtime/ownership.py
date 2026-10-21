"""Test-owned host loop and explicit checks for owned versus borrowed execution.

Coding must join its own service thread. Claw must settle child work without
stopping (or replacing) the application's existing loop. Neither case may leave
an actual child task or storage operation running after aclose.
"""

from __future__ import annotations

import asyncio
import threading
from contextlib import contextmanager


@contextmanager
def host_loop():
    ready = threading.Event()
    loops = []
    stops = []
    failures = []

    def run():
        try:
            with asyncio.Runner() as runner:

                async def serve():
                    loops.append(asyncio.get_running_loop())
                    stop = asyncio.Event()
                    stops.append(stop)
                    ready.set()
                    await stop.wait()

                runner.run(serve())
        except BaseException as exc:
            failures.append(exc)
            ready.set()

    thread = threading.Thread(target=run, name="acceptance-host-loop", daemon=True)
    thread.start()
    try:
        assert ready.wait(3), "Test host loop did not start"
        assert not failures, failures
        loop = loops[0]
        yield loop
    finally:
        if stops:
            loops[0].call_soon_threadsafe(stops[0].set)
        thread.join(5)
        assert not thread.is_alive(), "Actual test host owner failed to join"
        assert not failures, failures


class RuntimeOwnership:
    def __init__(self, borrowed_loop=None):
        self.borrowed_loop = borrowed_loop

    def assert_unstarted(self, owner):
        assert owner._loop is None
        if self.borrowed_loop is None:
            assert owner._thread is None
        else:
            assert not hasattr(owner, "_thread"), "Claw must not create a child service thread"

    def assert_running(self, owner):
        assert owner._loop is not None and owner._loop.is_running()
        if self.borrowed_loop is None:
            assert owner._thread is not None and owner._thread.is_alive()
        else:
            assert owner._loop is self.borrowed_loop
            assert not hasattr(owner, "_thread"), "Claw must reuse its actual host loop"

    def assert_closed(self, owner):
        assert owner.closed and not owner.active
        assert all(child.task is None or child.task.done() for child in owner._children.values())
        assert all(not child.queue_work for child in owner._children.values())
        assert owner._close_future is None or owner._close_future.done()
        if self.borrowed_loop is None:
            assert owner._thread is None or not owner._thread.is_alive(), "Cleanup skipped the actual owner join"
        else:
            assert not hasattr(owner, "_thread")
            assert self.borrowed_loop.is_running() and not self.borrowed_loop.is_closed()
            if owner._loop is not None:
                assert owner._loop is self.borrowed_loop, "A successor loop took execution ownership"

    async def emergency_cleanup(self, owner):
        # A failed assertion must still release Coding's real service thread;
        # never stop Claw's borrowed loop as if it belonged to the child runtime.
        if self.borrowed_loop is None and owner._thread is not None and owner._thread.is_alive():
            owner._loop.call_soon_threadsafe(owner._loop.stop)
            await asyncio.to_thread(owner._thread.join)
