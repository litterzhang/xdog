"""Exercise dispatch through real owners, with deterministic admission/cleanup gates."""

from __future__ import annotations

import asyncio
import inspect
import threading
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from xdog.agent.cross_loop import settle_on
from xdog.agent.extensions.runtime import ExtensionRuntime
from xdog.agent.mcp.registry import McpRegistry
from xdog.agent.mcp.stdio import McpError

from test_support.child_runtime.cross_loop import TestChildCrossLoop as TestChildCrossLoop


async def signaled(event: threading.Event) -> None:
    async with asyncio.timeout(3):
        while not event.is_set():
            await asyncio.sleep(0.001)


@dataclass
class Harness:
    thread_id: int
    ownership: Any
    closed_error: type[Exception]
    owner: McpRegistry
    loop: asyncio.AbstractEventLoop
    dispatch: Callable[[Coroutine[Any, Any, Any]], Awaitable[Any]]
    host: bool


@pytest.fixture(params=["mcp", "extension", "host"])
async def harness(request: pytest.FixtureRequest, tmp_path: Path) -> AsyncIterator[Harness]:
    owner: McpRegistry
    if request.param == "mcp":
        owner = McpRegistry({}, tmp_path)
    elif request.param == "extension":
        owner = ExtensionRuntime([], tmp_path, tmp_path)._manager
    else:
        owner = McpRegistry({}, tmp_path)
    loop = await asyncio.to_thread(owner._ensure_loop)
    host = request.param == "host"
    dispatch = (lambda work: settle_on(loop, work)) if host else owner._bridge
    try:
        yield Harness(owner._thread.ident, McpOwnership(), McpError, owner, loop, dispatch, host)
    finally:
        # Some admission tests deliberately close the flag without stopping the loop.
        if not loop.is_closed():
            owner._closed = False
        await owner.aclose()
        assert owner._thread is not None and not owner._thread.is_alive()
        assert loop.is_closed()


async def test_settle_on_same_loop_keeps_current_task_and_cancellation() -> None:
    current = asyncio.current_task()
    loop = asyncio.get_running_loop()

    async def identity() -> asyncio.Task[Any] | None:
        return asyncio.current_task()

    assert await settle_on(loop, identity()) is current
    cleaned = False

    async def work() -> None:
        nonlocal cleaned
        try:
            await asyncio.Future[None]()
        finally:
            cleaned = True

    caller = asyncio.create_task(settle_on(loop, work()))
    await asyncio.sleep(0)
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    assert cleaned


async def test_owner_shutdown_joins_remote_cleanup(harness: Harness) -> None:
    started, cleaning, release, finished = (threading.Event() for _ in range(4))

    async def work() -> None:
        started.set()
        try:
            await asyncio.Future[None]()
        finally:
            cleaning.set()
            try:
                await signaled(release)
            finally:
                finished.set()

    caller = asyncio.ensure_future(harness.dispatch(work()))
    try:
        await signaled(started)
        harness.owner.close()
        await signaled(cleaning)
        assert harness.owner._thread is not None and harness.owner._thread.is_alive()
        caller.cancel()
        await asyncio.sleep(0.01)
        caller.cancel()
        await asyncio.sleep(0.01)
        assert not caller.done() and not finished.is_set()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await caller
    assert finished.is_set()
    assert harness.owner._thread is not None
    await asyncio.to_thread(harness.owner._thread.join, 3)
    assert harness.loop.is_closed()


async def test_settle_on_closed_host_disposes_input() -> None:
    loop = asyncio.new_event_loop()
    loop.close()

    async def work() -> None:
        pytest.fail("must not execute")

    coroutine = work()
    with pytest.raises(RuntimeError, match="Host service loop closed"):
        await settle_on(loop, coroutine)
    assert inspect.getcoroutinestate(coroutine) == inspect.CORO_CLOSED


class McpOwnership:
    def assert_running(self, owner):
        assert owner._thread is not None and owner._thread.is_alive()
        assert owner._loop.is_running()

    def assert_closed(self, owner):
        assert owner._thread is not None and not owner._thread.is_alive()
        assert owner._loop.is_closed()
