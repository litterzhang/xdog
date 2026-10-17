"""Transactions and shared close operations settle before releasing ownership."""
from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from xdog.claw.acp.backend import ClawBackend
from xdog.claw.acp.inbox import GroupInbox
from xdog.claw.acp.server import ClawAcpServer


@pytest.mark.parametrize('fails', [False, True])
async def test_inbox_retains_lock_and_error_precedence_during_cancel(tmp_path, fails):
    inbox = GroupInbox(tmp_path / 'not-opened', 50)
    entered, release = threading.Event(), threading.Event()
    failure = ValueError('actual transaction failure')
    def work():
        entered.set()
        assert release.wait(5)
        if fails:
            raise failure
        return 'not delivered after cancellation'
    caller = asyncio.create_task(inbox._settle(work))
    closer = None
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        caller.cancel()
        await asyncio.sleep(0)
        caller.cancel()
        closer = asyncio.create_task(inbox.close())
        await asyncio.sleep(0)
        assert inbox._lock.locked() and not caller.done() and not closer.done()
        release.set()
        with pytest.raises(ValueError if fails else asyncio.CancelledError) as raised:
            await caller
        if fails:
            assert raised.value is failure
        await closer
        assert inbox._closed and not inbox._lock.locked()
    finally:
        release.set()
        await asyncio.gather(caller, *([closer] if closer is not None else []), return_exceptions=True)
        await inbox.close()


@pytest.mark.parametrize('owner_type', [ClawBackend, ClawAcpServer])
@pytest.mark.parametrize('fails', [False, True])
async def test_concurrent_close_waiters_share_one_owner_and_error_precedence(owner_type, fails):
    owner = (ClawBackend(SimpleNamespace(), SimpleNamespace()) if owner_type is ClawBackend
             else ClawAcpServer(Path('/tmp/not-bound-owned-work-test'), SimpleNamespace()))
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []
    failure = ValueError('actual close failure')
    async def close_work():
        calls.append('close')
        entered.set()
        await release.wait()
        if fails:
            raise failure
    owner._close = close_work
    first = asyncio.create_task(owner.close())
    second = asyncio.create_task(owner.close())
    try:
        await asyncio.wait_for(entered.wait(), 3)
        for _ in range(3):
            first.cancel()
            await asyncio.sleep(0)
        assert calls == ['close'] and not first.done() and not second.done()
        assert not owner._close_task.cancelled()
        release.set()
        outcomes = await asyncio.gather(first, second, return_exceptions=True)
        if fails:
            assert outcomes == [failure, failure]
        else:
            assert isinstance(outcomes[0], asyncio.CancelledError) and outcomes[1] is None
        assert calls == ['close'] and owner._close_task.done()
    finally:
        release.set()
        await asyncio.gather(first, second, return_exceptions=True)
