"""Tests for message queue."""
import asyncio

import pytest
from xdog.claw.core.queue import MessageQueue
from xdog.claw.core.types import QueueMode, UserInput


@pytest.fixture
def queue():
    return MessageQueue(max_concurrent=2)

@pytest.mark.asyncio
async def test_enqueue_and_dequeue(queue):
    msg = UserInput(group_id="g1", content="hello")
    await queue.enqueue(msg)
    messages = await queue.dequeue("g1")
    assert len(messages) == 1
    assert messages[0].content == "hello"

@pytest.mark.asyncio
async def test_collect_mode_batches_messages(queue):
    await queue.enqueue(UserInput(group_id="g1", content="msg1"))
    await queue.enqueue(UserInput(group_id="g1", content="msg2"))
    messages = await queue.dequeue("g1")
    assert len(messages) == 2

@pytest.mark.asyncio
async def test_steer_mode_calls_callback(queue):
    steered = []
    async def on_steer(msg):
        steered.append(msg)
    await queue.enqueue(
        UserInput(group_id="g1", content="urgent"),
        mode=QueueMode.STEER,
        on_steer=on_steer,
    )
    assert len(steered) == 1

@pytest.mark.asyncio
async def test_steer_backlog_calls_follow_up_and_queues(queue):
    """STEER_BACKLOG calls on_follow_up and also appends to the queue."""
    followed = []
    async def on_follow_up(msg):
        followed.append(msg)
    await queue.enqueue(
        UserInput(group_id="g1", content="extra context"),
        mode=QueueMode.STEER_BACKLOG,
        on_follow_up=on_follow_up,
    )
    assert len(followed) == 1
    assert followed[0].content == "extra context"
    # Also queued for later processing
    messages = await queue.dequeue("g1")
    assert len(messages) == 1

@pytest.mark.asyncio
async def test_acquire_cleans_up_on_exception():
    """acquire() clears running even when the body raises."""
    q = MessageQueue(max_concurrent=2)
    with pytest.raises(ValueError):
        async with q.acquire("g1"):
            assert q.is_running("g1")
            raise ValueError("boom")
    assert not q.is_running("g1")

@pytest.mark.asyncio
async def test_acquire_user_bypasses_global_semaphore():
    """User messages should not be blocked by the global semaphore."""
    q = MessageQueue(max_concurrent=1)
    acquired = []

    async def hold_background():
        async with q.acquire("bg", is_user=False):
            acquired.append("bg")
            await asyncio.sleep(0.1)

    # Start a background task that holds the global semaphore
    bg_task = asyncio.create_task(hold_background())
    await asyncio.sleep(0.01)  # let it acquire

    # User message on a different group should NOT be blocked
    async with q.acquire("user_group", is_user=True):
        acquired.append("user")

    await bg_task
    # User acquired while background was still holding global sem
    assert acquired == ["bg", "user"]

@pytest.mark.asyncio
async def test_collect_with_debounce_waits_then_dequeues(queue):
    """debounce_ms > 0 sleeps first, then dequeues accumulated messages."""
    await queue.enqueue(UserInput(group_id="g1", content="msg1"))

    async def add_more():
        await asyncio.sleep(0.01)
        await queue.enqueue(UserInput(group_id="g1", content="msg2"))

    task = asyncio.create_task(add_more())
    # debounce for 50ms — should collect msg2 that arrives during the wait
    result = await queue.collect_with_debounce("g1", debounce_ms=50)
    await task
    assert len(result) == 2
    assert result[0].content == "msg1"
    assert result[1].content == "msg2"


async def test_busy_group_waiter_does_not_hold_global_capacity():
    q = MessageQueue(max_concurrent=1)
    waiting = asyncio.Event()

    async def background():
        waiting.set()
        async with q.acquire("busy"):
            pass

    async with q.acquire("busy", is_user=True):
        waiter = asyncio.create_task(background())
        await waiting.wait()
        # The only global slot is still available while the waiter waits for busy.
        async with asyncio.timeout(1):
            async with q.acquire("other"):
                assert q.is_running("other")
    await waiter
