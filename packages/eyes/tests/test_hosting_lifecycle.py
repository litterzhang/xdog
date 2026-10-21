"""Owned shutdown joins late resources and retains every independent failure."""
from __future__ import annotations

import asyncio
import sqlite3

import pytest
from xdog.eyes.hosting.manager import ManagedObservers
from xdog.eyes.hosting.runtime import provider

from packages.eyes.tests.test_hosting import Host


class Resource:
    def __init__(self, error=None, *, hold=False):
        self.error, self.closed, self.calls = error, False, 0
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        if not hold:
            self.release.set()

    async def close(self):
        self.calls += 1
        self.entered.set()
        await self.release.wait()
        self.closed = True
        if self.error is not None:
            raise self.error

    async def aclose(self):
        await self.close()


def leaves(error):
    if isinstance(error, BaseExceptionGroup):
        return [item for nested in error.exceptions for item in leaves(nested)]
    return [error]


async def test_all_resources_join_despite_file_handler_subscription_and_journal_close_errors(tmp_path):
    host = Host(tmp_path)
    manager = await provider.open(host)
    report = manager._report_task
    file_error, handler_error = OSError('file close failed'), RuntimeError('handler close failed')
    unsubscribe_error, journal_error = ValueError('unsubscribe failed'), OSError('journal close failed')
    bad_file, held_file = Resource(file_error), Resource(hold=True)
    cancelled_file = Resource(asyncio.CancelledError())
    bad_handler, good_handler = Resource(handler_error), Resource()
    manager._files.update(bad=bad_file, held=held_file, cancelled=cancelled_file)
    manager._handlers.update(bad=bad_handler, good=good_handler)
    unsubscribe = manager._unsubscribe

    def failed_unsubscribe():
        unsubscribe()
        raise unsubscribe_error

    manager._unsubscribe = failed_unsubscribe
    close_journal = manager.store.close

    async def failed_journal_close():
        await close_journal()
        raise journal_error

    manager.store.close = failed_journal_close
    closing = asyncio.create_task(manager.aclose())
    await held_file.entered.wait()
    assert bad_file.closed and not closing.done()
    assert not host.opened[0].closed
    for _ in range(3):
        closing.cancel()
        await asyncio.sleep(0)
        assert not closing.done()
    held_file.release.set()
    with pytest.raises(ExceptionGroup) as failure:
        await closing
    assert {id(item) for item in leaves(failure.value)} == {
        id(file_error), id(handler_error), id(unsubscribe_error), id(journal_error)}
    assert all(resource.closed and resource.calls == 1 for resource in
               (bad_file, held_file, cancelled_file, bad_handler, good_handler))
    assert report.done() and host.opened[0].closed
    assert not host.routes and not host.listeners and not manager._files and not manager._handlers
    with pytest.raises(sqlite3.ProgrammingError, match='closed'):
        await host.opened[0].disk(lambda db: db.execute('SELECT 1'))
    with pytest.raises(ExceptionGroup) as repeated:
        await manager.aclose()
    assert repeated.value is failure.value  # The actual settled close is never started twice.


async def test_held_opening_late_report_and_operation_resources_join_despite_repeated_waiter_cancel(tmp_path):
    host = Host(tmp_path)
    manager = ManagedObservers(host)
    opening_entered, opening_release = asyncio.Event(), asyncio.Event()
    report_entered, report_stopping, report_release, report_closed = (asyncio.Event() for _ in range(4))
    late_file, late_handler = Resource(), Resource()
    open_store = manager._open_store

    async def report():
        report_entered.set()
        try:
            await asyncio.Future()
        finally:
            report_stopping.set()
            await report_release.wait()
            report_closed.set()

    async def held_opening():
        opening_entered.set()
        await opening_release.wait()
        await open_store()
        await report_entered.wait()

    async def operation():
        try:
            await manager._ensure_store()
        finally:
            # Cancellation of a producer can still finish publishing resources
            # while it settles the actual opening it was already awaiting.
            manager._files['late'] = late_file
            manager._handlers['late'] = late_handler

    manager._reports, manager._open_store = report, held_opening
    producer = asyncio.create_task(operation())
    manager._operations.add(producer)
    await opening_entered.wait()
    first = asyncio.create_task(manager.aclose())
    await asyncio.sleep(0)
    second = asyncio.create_task(manager.aclose())
    await asyncio.sleep(0)  # Both waiters have entered the owned close before cancellation.
    for _ in range(3):
        first.cancel()
        second.cancel()
        await asyncio.sleep(0)
        assert not first.done() and not second.done() and not manager._close_task.done()
    assert manager._report_task is None
    opening_release.set()
    await report_stopping.wait()
    assert manager._opening.done() and producer.done()
    assert not manager._report_task.done() and not host.opened[0].closed
    for _ in range(3):
        first.cancel()
        second.cancel()
        await asyncio.sleep(0)
        assert not first.done() and not second.done()
    report_release.set()
    for waiter in (first, second):
        with pytest.raises(asyncio.CancelledError):
            await waiter
    await manager.aclose()  # Expected producer/report cancellation is not a failure.
    assert manager._close_task.done() and not manager._close_task.cancelled()
    assert manager._report_task.done() and report_closed.is_set()
    assert late_file.closed and late_handler.closed and host.opened[0].closed
    assert not host.routes and not host.listeners


@pytest.mark.parametrize('cancel_startup', [False, True])
async def test_provider_retains_original_startup_error_and_actual_cleanup_failure(tmp_path, cancel_startup):
    host = Host(tmp_path)
    startup = asyncio.CancelledError() if cancel_startup else OSError('actual startup failure')
    cleanup = RuntimeError('actual cleanup failure')
    subscribe = host.subscribe_outputs

    async def subscribe_with_failed_close(callback):
        unsubscribe = await subscribe(callback)

        def failed_unsubscribe():
            unsubscribe()
            raise cleanup
        return failed_unsubscribe

    async def failed_start(callback):
        raise startup

    host.subscribe_outputs, host.subscribe_inputs = subscribe_with_failed_close, failed_start
    with pytest.raises(RuntimeError if cancel_startup else ExceptionGroup) as failure:
        await provider.open(host)
    expected = [cleanup] if cancel_startup else [startup, cleanup]
    assert {id(item) for item in leaves(failure.value)} == {id(item) for item in expected}
    assert len(leaves(failure.value)) == len(expected)  # The opening error is observed only once.
    assert host.opened[0].closed and not host.routes and not host.listeners
