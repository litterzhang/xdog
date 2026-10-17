"""Exercise dispatch through real owners, with deterministic admission/cleanup gates."""
from __future__ import annotations

import asyncio
import contextvars
import inspect
import threading
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from xdog.agent.extensions.runtime import ExtensionRuntime
from xdog.agent.mcp.manager import McpManager
from xdog.agent.mcp.stdio import McpError
from xdog.agent.supervision import ChildSupervisor, settle_on


async def signaled(event: threading.Event) -> None:
    async with asyncio.timeout(3):
        while not event.is_set():
            await asyncio.sleep(0.001)


@dataclass
class Harness:
    owner: McpManager | ChildSupervisor
    loop: asyncio.AbstractEventLoop
    dispatch: Callable[[Coroutine[Any, Any, Any]], Awaitable[Any]]
    host: bool


@pytest.fixture(params=['mcp', 'extension', 'supervision', 'host'])
async def harness(request: pytest.FixtureRequest, tmp_path: Path) -> AsyncIterator[Harness]:
    owner: McpManager | ChildSupervisor
    if request.param == 'mcp':
        owner = McpManager({}, tmp_path)
    elif request.param == 'extension':
        owner = ExtensionRuntime([], tmp_path, tmp_path)._manager
    else:
        owner = ChildSupervisor(lambda spec, publish: (_ for _ in ()).throw(AssertionError('unused factory')))
    loop = await asyncio.to_thread(owner._ensure_loop)
    host = request.param == 'host'
    dispatch = (lambda work: settle_on(loop, work)) if host else owner._bridge
    try:
        yield Harness(owner, loop, dispatch, host)
    finally:
        # Some admission tests deliberately close the flag without stopping the loop.
        if not loop.is_closed():
            owner._closed = False
        await owner.aclose()
        assert owner._thread is not None and not owner._thread.is_alive()
        assert loop.is_closed()


async def test_results_errors_concurrency_and_thread_ownership(harness: Harness) -> None:
    async def work(index: int) -> tuple[int, int]:
        assert asyncio.get_running_loop() is harness.loop
        await asyncio.sleep(0)
        return index, threading.get_ident()

    results = await asyncio.gather(*(harness.dispatch(work(i)) for i in range(24)))
    assert [row[0] for row in results] == list(range(24))
    assert harness.owner._thread is not None
    assert {row[1] for row in results} == {harness.owner._thread.ident}

    error = ValueError('remote failure')

    async def fail() -> None:
        raise error

    with pytest.raises(ValueError) as caught:
        await harness.dispatch(fail())
    assert caught.value is error


@pytest.mark.parametrize('late', ['success', 'error', 'cancel'])
async def test_repeated_cancel_joins_actual_cleanup(harness: Harness, late: str) -> None:
    started, cleaning, release, finished = (threading.Event() for _ in range(4))
    remote_tasks: list[asyncio.Task[Any]] = []
    errors: list[dict[str, Any]] = []
    harness.loop.call_soon_threadsafe(harness.loop.set_exception_handler, lambda loop, context: errors.append(context))

    async def work() -> int:
        task = asyncio.current_task()
        assert task is not None
        remote_tasks.append(task)
        started.set()
        try:
            return await asyncio.Future[int]()
        except asyncio.CancelledError:
            cleaning.set()
            try:
                await signaled(release)
            finally:
                finished.set()
            if late == 'error':
                raise ValueError('late cleanup failure')
            if late == 'cancel':
                raise
            return 7

    caller = asyncio.ensure_future(harness.dispatch(work()))
    try:
        await signaled(started)
        caller.cancel()
        await signaled(cleaning)
        for _ in range(4):
            caller.cancel()
            await asyncio.sleep(0.005)
            assert not caller.done() and not finished.is_set()
        assert remote_tasks[0].cancelling() == 1
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await caller
    assert finished.is_set() and remote_tasks[0].done()
    await asyncio.sleep(0)
    assert not errors


async def test_cancel_before_remote_start_disposes_without_execution(
    harness: Harness, monkeypatch: pytest.MonkeyPatch,
) -> None:
    blocked, release, submitted = (threading.Event() for _ in range(3))
    ran = False

    def block() -> None:
        blocked.set()
        assert release.wait(3)

    async def work() -> None:
        nonlocal ran
        ran = True

    coroutine = work()
    harness.loop.call_soon_threadsafe(block)
    await signaled(blocked)
    # Observe submission, not a sleep-based guess at startup completion.
    original = harness.loop.call_soon_threadsafe

    def schedule(callback: Callable[..., Any], *args: Any, **kwargs: Any) -> asyncio.Handle:
        handle = original(callback, *args, **kwargs)
        submitted.set()
        return handle

    monkeypatch.setattr(harness.loop, 'call_soon_threadsafe', schedule)
    caller = asyncio.ensure_future(harness.dispatch(coroutine))
    try:
        await signaled(submitted)
        caller.cancel()
        await asyncio.sleep(0.01)
        assert not caller.done()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await caller
        monkeypatch.setattr(harness.loop, 'call_soon_threadsafe', original)
    assert not ran and inspect.getcoroutinestate(coroutine) == inspect.CORO_CLOSED


async def test_startup_failure_disposes_input(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    if harness.host:
        return  # settle_on has no startup policy.

    def fail() -> asyncio.AbstractEventLoop:
        raise LookupError('startup failed')

    async def work() -> None:
        pytest.fail('must not execute')

    monkeypatch.setattr(harness.owner, '_ensure_loop', fail)
    coroutine = work()
    with pytest.raises(LookupError, match='startup failed'):
        await harness.dispatch(coroutine)
    assert inspect.getcoroutinestate(coroutine) == inspect.CORO_CLOSED


async def test_second_closed_guard_after_loop_acquisition(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    if harness.host:
        return  # Host-loop admission does not belong to settle_on.

    def close_during_startup() -> asyncio.AbstractEventLoop:
        with harness.owner._start_lock:
            harness.owner._closed = True
        return harness.loop

    ran = False

    async def work() -> None:
        nonlocal ran
        ran = True

    monkeypatch.setattr(harness.owner, '_ensure_loop', close_during_startup)
    coroutine = work()
    expected = RuntimeError if isinstance(harness.owner, ChildSupervisor) else McpError
    with pytest.raises(expected, match='closed'):
        await harness.dispatch(coroutine)
    assert not ran and inspect.getcoroutinestate(coroutine) == inspect.CORO_CLOSED


async def test_submission_failure_disposes_input(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: Any, **kwargs: Any) -> asyncio.Handle:
        raise RuntimeError('submission rejected')

    async def work() -> None:
        pytest.fail('must not execute')

    coroutine = work()
    try:
        with monkeypatch.context() as patch:
            patch.setattr(harness.loop, 'call_soon_threadsafe', fail)
            with pytest.raises(RuntimeError, match='submission rejected'):
                await harness.dispatch(coroutine)
        assert inspect.getcoroutinestate(coroutine) == inspect.CORO_CLOSED
    finally:
        coroutine.close()  # Baseline characterization must not emit an unawaited warning.


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


async def test_task_creation_failure_disposes_and_settles(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    async def work() -> None:
        pytest.fail('must not execute')

    coroutine = work()
    original = harness.loop.create_task

    def create(submitted: Any, *args: Any, **kwargs: Any) -> asyncio.Task[Any]:
        if submitted is coroutine:
            raise RuntimeError('task creation failed')
        return original(submitted, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(harness.loop, 'create_task', create)
        async with asyncio.timeout(3):
            with pytest.raises(RuntimeError, match='task creation failed'):
                await harness.dispatch(coroutine)
    assert inspect.getcoroutinestate(coroutine) == inspect.CORO_CLOSED


async def test_owner_admission_lock_covers_submission(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    if harness.host:
        return
    original = harness.loop.call_soon_threadsafe
    checks: list[bool] = []

    def submit(callback: Callable[..., Any], *args: Any, **kwargs: Any) -> asyncio.Handle:
        checks.append(harness.owner._start_lock.locked())
        return original(callback, *args, **kwargs)

    async def work() -> int:
        return 42

    with monkeypatch.context() as patch:
        patch.setattr(harness.loop, 'call_soon_threadsafe', submit)
        assert await harness.dispatch(work()) == 42
    assert checks == [True]


async def test_cancel_during_loop_acquisition_disposes_input(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    if harness.host:
        return
    acquiring, release, returned = (threading.Event() for _ in range(3))

    def acquire() -> asyncio.AbstractEventLoop:
        acquiring.set()
        assert release.wait(3)
        returned.set()
        return harness.loop

    async def work() -> None:
        pytest.fail('must not execute')

    coroutine = work()
    with monkeypatch.context() as patch:
        patch.setattr(harness.owner, '_ensure_loop', acquire)
        caller = asyncio.ensure_future(harness.dispatch(coroutine))
        try:
            await signaled(acquiring)
            caller.cancel()
            with pytest.raises(asyncio.CancelledError):
                await caller
            assert inspect.getcoroutinestate(coroutine) == inspect.CORO_CLOSED
        finally:
            release.set()
            await signaled(returned)
    # Returning from startup later must not resurrect canceled input.
    await asyncio.sleep(0)
    assert inspect.getcoroutinestate(coroutine) == inspect.CORO_CLOSED


async def test_real_close_wins_between_acquisition_and_admission(
    harness: Harness, monkeypatch: pytest.MonkeyPatch,
) -> None:
    if harness.host:
        return
    acquiring, release = threading.Event(), threading.Event()

    def acquire() -> asyncio.AbstractEventLoop:
        acquiring.set()
        assert release.wait(3)
        return harness.loop

    async def work() -> None:
        pytest.fail('must not execute')

    coroutine = work()
    with monkeypatch.context() as patch:
        patch.setattr(harness.owner, '_ensure_loop', acquire)
        caller = asyncio.ensure_future(harness.dispatch(coroutine))
        try:
            await signaled(acquiring)
            harness.owner.close()
            assert harness.owner._thread is not None
            await asyncio.to_thread(harness.owner._thread.join, 3)
            assert harness.loop.is_closed()
        finally:
            release.set()
        expected = RuntimeError if isinstance(harness.owner, ChildSupervisor) else McpError
        with pytest.raises(expected, match='closed'):
            await caller
    assert inspect.getcoroutinestate(coroutine) == inspect.CORO_CLOSED


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


async def test_remote_self_cancel_and_context_propagation(harness: Harness) -> None:
    label = contextvars.ContextVar('dispatch_test_label', default='unset')

    async def read() -> str:
        return label.get()

    async def deliver(value: str) -> str:
        token = label.set(value)
        try:
            return str(await harness.dispatch(read()))
        finally:
            label.reset(token)

    results = await asyncio.gather(deliver('one'), deliver('two'))
    assert tuple(results) == ('one', 'two')

    async def cancel() -> None:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await harness.dispatch(cancel())


async def test_settle_on_closed_host_disposes_input() -> None:
    loop = asyncio.new_event_loop()
    loop.close()

    async def work() -> None:
        pytest.fail('must not execute')

    coroutine = work()
    with pytest.raises(RuntimeError, match='Host service loop closed'):
        await settle_on(loop, coroutine)
    assert inspect.getcoroutinestate(coroutine) == inspect.CORO_CLOSED
