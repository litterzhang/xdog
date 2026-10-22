"""MCP owner shutdown must settle actual executor IO, not just cancelled Tasks."""
from __future__ import annotations

import asyncio
import threading

import pytest
from xdog.agent.mcp import McpRegistry


async def test_cancelled_close_joins_default_executor_before_releasing_owner(tmp_path, monkeypatch):
    manager = McpRegistry({}, tmp_path)
    owner_loop = await asyncio.to_thread(manager._ensure_loop)
    owner = manager._thread
    assert owner is not None
    entered, release, finished = (threading.Event() for _ in range(3))
    shutdown_entered, shutdown_finished, close_entered = (threading.Event() for _ in range(3))
    shutdown_or_close, joining = (threading.Event() for _ in range(2))
    worker_threads = []
    committed = tmp_path / "owned-io.txt"
    original_shutdown = owner_loop.shutdown_default_executor
    original_close = owner_loop.close
    original_join = owner.join

    async def tracked_shutdown():
        shutdown_entered.set()
        shutdown_or_close.set()
        await original_shutdown()
        shutdown_finished.set()

    def tracked_close():
        close_entered.set()
        shutdown_or_close.set()  # A missing executor shutdown fails without waiting for a timeout.
        original_close()

    def tracked_join(*args, **kwargs):
        joining.set()
        return original_join(*args, **kwargs)

    def owned_io():
        worker_threads.append(threading.current_thread())
        entered.set()
        try:
            assert release.wait(10), "Test did not release its own executor worker"
            committed.write_text("actual IO settled", encoding="utf-8")
        finally:
            finished.set()

    async def run_io():
        await asyncio.to_thread(owned_io)

    monkeypatch.setattr(owner_loop, "shutdown_default_executor", tracked_shutdown)
    monkeypatch.setattr(owner_loop, "close", tracked_close)
    monkeypatch.setattr(owner, "join", tracked_join)
    work = closing = None
    try:
        work = asyncio.run_coroutine_threadsafe(run_io(), owner_loop)
        assert await asyncio.to_thread(entered.wait, 5)
        closing = asyncio.create_task(manager.aclose())
        assert await asyncio.to_thread(shutdown_or_close.wait, 5)
        assert shutdown_entered.is_set(), "Owner closed its loop without joining the default executor"
        assert await asyncio.to_thread(joining.wait, 5)

        for _ in range(3):
            assert closing.cancel()
            # cancel() queues the close caller ahead of this callback. Waiting for
            # the callback acknowledges a delivered cancellation, not elapsed time.
            acknowledged = asyncio.get_running_loop().create_future()
            asyncio.get_running_loop().call_soon(acknowledged.set_result, None)
            await acknowledged
            assert not closing.done(), "Cancelled aclose returned before its actual worker settled"
            assert owner.is_alive(), "Owner exited while its default-executor IO was still blocked"
            assert not close_entered.is_set() and not shutdown_finished.is_set()
            assert not finished.is_set() and not committed.exists()
            assert len(worker_threads) == 1 and worker_threads[0].is_alive()

        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(closing, 5)
        assert finished.is_set() and shutdown_finished.is_set() and close_entered.is_set()
        assert not owner.is_alive() and owner_loop.is_closed()
        assert not worker_threads[0].is_alive()
        assert committed.read_text(encoding="utf-8") == "actual IO settled"
        # Owner cleanup cancels the asyncio wrapper; it cannot cancel actual IO.
        assert work.cancelled()
    finally:
        # Release only this test's worker even when a lifecycle assertion fails.
        release.set()
        manager.close()
        try:
            if closing is not None:
                await asyncio.wait_for(asyncio.gather(closing, return_exceptions=True), 5)
            await asyncio.wait_for(manager.aclose(), 5)
            if work is not None:
                await asyncio.wait_for(asyncio.gather(asyncio.wrap_future(work), return_exceptions=True), 5)
        finally:
            # Also reap our actual worker if the regressed owner has already
            # exited without joining it. Do not rely on that broken close path.
            for worker in worker_threads:
                await asyncio.to_thread(worker.join, 5)
                assert not worker.is_alive(), "Test executor worker did not settle after release"
