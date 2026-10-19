"""Real task/lease ownership; no message queue or mock execution owner."""

from __future__ import annotations

import asyncio

import pytest
from xdog.claw.core.runtime.group_controller import GroupController


async def test_reserved_unstarted_operation_fences_input_until_its_actual_exit():

    controller = GroupController("main", asyncio.Semaphore(1))
    entered, release = asyncio.Event(), asyncio.Event()
    input_entered = asyncio.Event()
    lease = controller.reserve()
    assert lease is not None and controller.busy

    async def operation():
        async with controller.acquire(is_user=True):  # Same actual task, not a second owner.
            entered.set()
            await release.wait()
            assert controller.busy

    async def incoming():
        async with controller.acquire(is_user=True, input_key=("message", "attempt")):
            input_entered.set()

    waiter = asyncio.create_task(incoming())
    job = controller.start(lease, operation())
    try:
        await asyncio.wait_for(entered.wait(), 2)
        assert not input_entered.is_set()
        release.set()
        await asyncio.wait_for(job, 2)
        await asyncio.wait_for(waiter, 2)
        assert input_entered.is_set() and not controller.busy
    finally:
        release.set()
        await asyncio.gather(job, waiter, return_exceptions=True)


async def test_prestart_cancellation_closes_unentered_coroutine_and_releases_its_own_fence():

    controller = GroupController("main", asyncio.Semaphore(1))
    entered = []

    async def operation():
        entered.append(True)

    lease = controller.reserve()
    coroutine = operation()
    job = controller.start(lease, coroutine)
    job.cancel()
    await asyncio.gather(job, return_exceptions=True)
    assert not entered and not controller.busy
    assert coroutine.cr_frame is None  # Never leave an unawaited worker behind.


async def test_a_stale_or_foreign_release_cannot_release_a_successor():

    slots = asyncio.Semaphore(1)
    controller, foreign = GroupController("main", slots), GroupController("other", slots)
    old = controller.reserve()
    assert controller.release(old)
    successor = controller.reserve()
    assert not controller.release(old) and controller.busy
    assert not controller.release(foreign.reserve()) and controller.busy
    assert controller.release(successor) and not controller.busy


async def test_other_tasks_cannot_release_or_confirm_a_running_owner():

    controller = GroupController("main", asyncio.Semaphore(1))
    entered, release = asyncio.Event(), asyncio.Event()

    async def original():
        async with controller.acquire(is_user=True, input_key=("one", "attempt-one")):
            controller.confirm_input("one", "attempt-one", "session-one")
            entered.set()
            await release.wait()

    lease = controller.reserve()

    # Bind an input identity inside the same owner task explicitly.
    async def operation():
        await original()

    job = controller.start(lease, operation(), input_key=("one", "attempt-one"))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        assert controller.owns_input("one", "attempt-one")
        assert not controller.release(lease)
        with pytest.raises(ValueError, match="owner"):
            controller.confirm_input("one", "attempt-one", "forged-session")
        assert not controller.cancel_input("one", "wrong-attempt")
        assert not controller.cancel_input("other", "attempt-one")
    finally:
        release.set()
        await job
    assert not controller.owns_input("one", "attempt-one") and not controller.busy


async def test_input_cancellation_keeps_the_owner_until_real_cleanup_finishes():

    controller = GroupController("main", asyncio.Semaphore(1))
    entered, cleanup, release, successor = (asyncio.Event() for _ in range(4))

    async def original():
        async with controller.acquire(is_user=True, input_key=("one", "attempt-one")):
            controller.confirm_input("one", "attempt-one", "session-one")
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleanup.set()
                await release.wait()

    async def following():
        async with controller.acquire(is_user=True, input_key=("two", "attempt-two")):
            controller.confirm_input("two", "attempt-two", "session-one")
            successor.set()

    original_task = asyncio.create_task(original())
    await asyncio.wait_for(entered.wait(), 2)
    following_task = asyncio.create_task(following())
    try:
        assert controller.cancel_input("one", "attempt-one")
        await asyncio.wait_for(cleanup.wait(), 2)
        assert controller.busy and not successor.is_set()
        assert controller.cancel_input("one", "attempt-one")  # No repeated cancel into cleanup.
        release.set()
        await asyncio.gather(original_task, return_exceptions=True)
        await asyncio.wait_for(following_task, 2)
        assert not controller.cancel_input("one", "attempt-one")
    finally:
        release.set()
        await asyncio.gather(original_task, following_task, return_exceptions=True)


async def test_unconfirmed_or_mismatched_input_cannot_borrow_live_controls():

    controller = GroupController("main", asyncio.Semaphore(1))
    async with controller.acquire(is_user=True, input_key=("one", "attempt-one")):
        assert not controller.owns_input("one") and not controller.cancel_input("one", "attempt-one")
        with pytest.raises(ValueError):
            controller.confirm_input("one", "attempt-two", "session")
        controller.confirm_input("one", "attempt-one", "session")
        with pytest.raises(ValueError):
            controller.confirm_input("one", "attempt-one", "session")
        async with controller.acquire(is_user=True):
            assert controller.owns_input("one")
        assert controller.busy  # Nested exit never releases the outer owner.


async def test_waiting_for_background_capacity_does_not_block_an_independent_human_group():

    slots = asyncio.Semaphore(1)
    first, second, human = (GroupController(name, slots) for name in ("first", "second", "human"))
    entered, release = asyncio.Event(), asyncio.Event()

    async def waiting_background():
        entered.set()
        async with second.acquire():
            await release.wait()

    async with first.acquire():
        waiter = asyncio.create_task(waiting_background())
        await entered.wait()
        async with asyncio.timeout(2):
            async with human.acquire(is_user=True):
                assert human.busy and first.busy
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)
        assert not second.busy


async def test_a_foreign_or_already_used_reservation_cannot_start_an_operation():

    slots = asyncio.Semaphore(1)
    first, second = GroupController("first", slots), GroupController("second", slots)
    lease = first.reserve()
    with pytest.raises(ValueError):
        async with second.acquire(is_user=True, reservation=lease):
            pytest.fail("Foreign lease entered")
    assert first.release(lease)
    with pytest.raises(ValueError):
        async with first.acquire(is_user=True, reservation=lease):
            pytest.fail("Released lease entered")


@pytest.mark.asyncio
async def test_acquire_cleans_up_on_exception():
    """acquire() clears running even when the body raises."""
    q = GroupController('main', asyncio.Semaphore(2))
    with pytest.raises(ValueError):
        async with q.acquire():
            assert q.busy
            raise ValueError("boom")
    assert not q.busy

@pytest.mark.asyncio
async def test_acquire_user_bypasses_global_semaphore():
    """User messages should not be blocked by the global semaphore."""
    slots = asyncio.Semaphore(1)
    q = GroupController('busy', slots)
    acquired = []

    async def hold_background():
        async with q.acquire(is_user=False):
            acquired.append("bg")
            await asyncio.sleep(0.1)

    # Start a background task that holds the global semaphore
    bg_task = asyncio.create_task(hold_background())
    await asyncio.sleep(0.01)  # let it acquire

    # User message on a different group should NOT be blocked
    async with GroupController('human', slots).acquire(is_user=True):
        acquired.append("user")

    await bg_task
    # User acquired while background was still holding global sem
    assert acquired == ["bg", "user"]

async def test_busy_group_waiter_does_not_hold_global_capacity():
    slots = asyncio.Semaphore(1)
    q = GroupController('busy', slots)
    waiting = asyncio.Event()

    async def background():
        waiting.set()
        async with q.acquire():
            pass

    async with q.acquire(is_user=True):
        waiter = asyncio.create_task(background())
        await waiting.wait()
        # The only global slot is still available while the waiter waits for busy.
        async with asyncio.timeout(1):
            async with GroupController('other', slots).acquire():
                assert q.busy
    await waiter
