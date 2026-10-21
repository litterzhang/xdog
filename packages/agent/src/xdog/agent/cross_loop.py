"""Remote submission and actual task settlement, without service ownership policy.

Owners acquire/start their loops, serialize admission against close, and retain
thread shutdown responsibility. This is deliberately not an owned-future join:
caller cancellation requests remote cancellation, then waits for real cleanup.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import threading
from collections.abc import Coroutine
from typing import Any, TypeVar

T = TypeVar('T')


def submit_remote(loop: asyncio.AbstractEventLoop, coroutine: Coroutine[Any, Any, T]) -> Coroutine[Any, Any, T]:
    """Submit synchronously under the owner's admission guard, then await settlement.

    The returned coroutine must be awaited immediately. Submission/start failures
    dispose the input. Cancellation before start prevents execution; after start
    it requests cancellation once, never cancels the result future, and joins the
    task's actual completion even under repeated caller cancellation. A canceled
    delivery consumes late errors but never publishes a late successful result.
    The owner must keep the loop alive through settlement.
    """
    result: concurrent.futures.Future[T] = concurrent.futures.Future()
    settled: concurrent.futures.Future[None] = concurrent.futures.Future()
    canceled = threading.Event()
    task_ref: list[asyncio.Task[T]] = []

    def done(completed: asyncio.Task[T]) -> None:
        if completed.cancelled():
            result.cancel()
        elif (error := completed.exception()) is not None:
            result.set_exception(error)
        else:
            result.set_result(completed.result())
        settled.set_result(None)

    def start() -> None:
        if canceled.is_set():
            coroutine.close()
            result.cancel()
            settled.set_result(None)
            return
        try:
            task = loop.create_task(coroutine)
        except BaseException as error:
            coroutine.close()
            result.set_exception(error)
            settled.set_result(None)
            return
        task_ref.append(task)
        task.add_done_callback(done)

    try:
        loop.call_soon_threadsafe(start)
    except BaseException:
        coroutine.close()
        raise

    async def wait() -> T:
        wrapped = asyncio.wrap_future(result)
        try:
            return await asyncio.shield(wrapped)
        except asyncio.CancelledError:
            canceled.set()

            def cancel() -> None:
                if task_ref and not task_ref[0].cancelling():
                    task_ref[0].cancel()

            # Do not substitute Future.cancel(): it can signal completion while
            # the remote task still owns files, metadata or admission slots.
            if not settled.done():
                try:
                    loop.call_soon_threadsafe(cancel)
                except RuntimeError:
                    # An orderly owner shutdown may have settled the task and
                    # closed its loop between the check and cancellation request.
                    if not settled.done():
                        raise
            joined = asyncio.wrap_future(settled)
            while not joined.done():
                try:
                    await asyncio.shield(joined)
                except asyncio.CancelledError:
                    continue
            if wrapped.done() and not wrapped.cancelled():
                wrapped.exception()
            else:
                wrapped.cancel()
            raise

    return wait()


async def settle_on(loop: asyncio.AbstractEventLoop, coroutine: Coroutine[Any, Any, T]) -> T:
    """Use an existing host loop, retaining actual cancellation/cleanup ownership."""
    if loop is asyncio.get_running_loop():
        return await coroutine
    if loop.is_closed():
        coroutine.close()
        raise RuntimeError("Host service loop closed")
    return await submit_remote(loop, coroutine)
