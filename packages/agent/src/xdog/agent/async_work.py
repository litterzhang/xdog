"""Owned async work with explicit result/cancellation precedence.

These helpers settle work the caller already owns; they do not create service
owners, acquire locks, retry operations or replay effects. Keep resource scopes
and shared close-task creation at the call site.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import ParamSpec, TypeVar

P = ParamSpec('P')
T = TypeVar('T')


async def wait_owned(future: asyncio.Future[T]) -> T:
    """Join a Future/Task despite repeated caller cancellation; owned errors win.

    Cancellation never propagates to the owned future. Once it settles, report
    its actual error (including its own cancellation), otherwise re-raise any
    deferred caller cancellation instead of delivering a successful result.
    An already-done future is read immediately, as at transaction/close sites.
    """
    cancelled = False
    while not future.done():
        try:
            await asyncio.shield(future)
        except asyncio.CancelledError:
            cancelled = True
    result = future.result()
    if cancelled:
        raise asyncio.CancelledError
    return result


async def run_owned(work: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
    """Run a thread to completion before releasing ownership; caller cancel wins.

    Python threads cannot be forcibly canceled. A canceled delivery must wait
    for its thread, consume any late failure, then retain the original caller
    cancellation. Uncancelled results/errors and contextvars are unchanged.
    This intentionally differs from ``wait_owned``'s transaction error priority.
    """
    task = asyncio.create_task(asyncio.to_thread(work, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            await wait_owned(task)
        except (Exception, asyncio.CancelledError):
            pass  # Settle/retrieve the late result, never publish a canceled delivery.
        raise


async def settle_cleanup(*steps: Callable[[], Awaitable[object]], message: str = 'Resource cleanup failed') -> None:
    """Join every ordered cleanup step; actual errors win over waiter cancellation.

    Callers decide which resources/order they own. This adds no resource/session
    policy, retry, service loop or domain lifetime to the reusable runtime.
    """
    errors: list[BaseException] = []
    cancelled = False
    for step in steps:
        try:
            await wait_owned(asyncio.ensure_future(step()))
        except asyncio.CancelledError:
            cancelled = True
        except BaseException as exc:
            errors.append(exc)
    if len(errors) == 1:
        raise errors[0]
    if errors:
        raise BaseExceptionGroup(message, errors)
    if cancelled:
        raise asyncio.CancelledError
