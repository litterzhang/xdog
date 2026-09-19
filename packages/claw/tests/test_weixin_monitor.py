"""WeChat polling must not wait for the agent's message handler."""
from __future__ import annotations

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from xdog.claw.channels.weixin.api import WeixinApiClient
from xdog.claw.channels.weixin.monitor import (
    MonitorOpts,
    _load_get_updates_buf,
    _save_get_updates_buf,
    run_monitor,
)
from xdog.claw.channels.weixin.types import GetUpdatesResp, MessageType, WeixinMessage


class FakeApi(WeixinApiClient):
    def __init__(self):
        super().__init__("https://example.invalid", "")
        self.updates = asyncio.Queue()
        self.polls = asyncio.Queue()
        self.poll_cancelled = asyncio.Event()
        self.close = AsyncMock()

    async def get_updates(self, get_updates_buf="", timeout_ms=35000):
        self.polls.put_nowait((get_updates_buf, timeout_ms))
        try:
            response = await self.updates.get()
            if isinstance(response, Exception):
                raise response
            return response
        except asyncio.CancelledError:
            self.poll_cancelled.set()
            raise


def user_message(index):
    return WeixinMessage(message_id=index, from_user_id=f"user-{index}", message_type=MessageType.USER)


def options(tmp_path, api, on_message, **kwargs):
    return MonitorOpts(
        base_url="https://example.invalid",
        token="",
        account_id="test",
        state_dir=tmp_path,
        cancel_event=asyncio.Event(),
        on_message=on_message,
        api_client=api,
        **kwargs,
    )


async def wait(awaitable):
    return await asyncio.wait_for(awaitable, timeout=2)


@pytest.mark.asyncio
async def test_polls_while_handler_runs_and_dispatches_fifo(tmp_path):
    api = FakeApi()
    started = asyncio.Event()
    release = asyncio.Event()
    processed_all = asyncio.Event()
    processed = []

    async def on_message(msg):
        processed.append(msg.message_id)
        if msg.message_id == 1:
            started.set()
            await release.wait()
        if msg.message_id == 3:
            processed_all.set()

    opts = options(tmp_path, api, on_message)
    _save_get_updates_buf(tmp_path, "test", "previous")
    api.updates.put_nowait(GetUpdatesResp(
        msgs=(user_message(1),), get_updates_buf="first", longpolling_timeout_ms=12345,
    ))
    task = asyncio.create_task(run_monitor(opts))
    try:
        assert await wait(api.polls.get()) == ("previous", 35000)
        await wait(started.wait())
        assert await wait(api.polls.get()) == ("first", 12345)
        api.updates.put_nowait(GetUpdatesResp(
            msgs=(
                user_message(2),
                WeixinMessage(message_id=99, message_type=MessageType.BOT),
                user_message(3),
            ),
            get_updates_buf="second",
        ))
        assert await wait(api.polls.get()) == ("second", 12345)
        assert processed == [1]  # Poller advanced while the worker is still busy.
        assert _load_get_updates_buf(tmp_path, "test") == "second"
        release.set()
        await wait(processed_all.wait())
        assert processed == [1, 2, 3]
    finally:
        opts.cancel_event.set()
        await wait(task)
    assert api.poll_cancelled.is_set()
    api.close.assert_not_awaited()  # The channel owns this shared client.


@pytest.mark.asyncio
async def test_handler_failure_does_not_stop_queue_or_polling(tmp_path, caplog):
    api = FakeApi()
    finished = asyncio.Event()
    processed = []

    async def on_message(msg):
        processed.append(msg.message_id)
        if msg.message_id == 1:
            raise RuntimeError("handler failed")
        finished.set()

    opts = options(tmp_path, api, on_message)
    api.updates.put_nowait(GetUpdatesResp(msgs=(user_message(1), user_message(2))))
    task = asyncio.create_task(run_monitor(opts))
    try:
        await wait(finished.wait())
        assert processed == [1, 2]
        assert "Error processing message" in caplog.text
        assert "getUpdates error" not in caplog.text
        assert not task.done()
    finally:
        opts.cancel_event.set()
        await wait(task)


@pytest.mark.asyncio
async def test_full_queue_applies_backpressure_without_dropping_messages(tmp_path, caplog):
    api = FakeApi()
    release = asyncio.Event()
    finished = asyncio.Event()
    processed = []

    async def on_message(msg):
        processed.append(msg.message_id)
        await release.wait()
        if msg.message_id == 4:
            finished.set()

    opts = options(tmp_path, api, on_message, max_pending_messages=1)
    api.updates.put_nowait(GetUpdatesResp(
        msgs=tuple(user_message(i) for i in range(1, 5)), get_updates_buf="whole-batch",
    ))
    task = asyncio.create_task(run_monitor(opts))
    try:
        await wait(api.polls.get())
        # Allow the worker and poller to reach their blocked awaits.
        for _ in range(5):
            await asyncio.sleep(0)
        assert processed == [1]
        assert api.polls.empty()
        assert _load_get_updates_buf(tmp_path, "test") == ""
        assert "inbox full" in caplog.text
        release.set()
        await wait(finished.wait())
        assert await wait(api.polls.get()) == ("whole-batch", 35000)
        assert processed == [1, 2, 3, 4]
    finally:
        opts.cancel_event.set()
        await wait(task)


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_task", [False, True])
@pytest.mark.parametrize("full_queue", [False, True])
async def test_shutdown_cancels_worker_and_poller(tmp_path, cancel_task, full_queue, caplog):
    api = FakeApi()
    started = asyncio.Event()
    handler_cancelled = asyncio.Event()
    processed = []

    async def on_message(msg):
        processed.append(msg.message_id)
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            handler_cancelled.set()

    opts = options(tmp_path, api, on_message, max_pending_messages=1 if full_queue else 50)
    api.updates.put_nowait(GetUpdatesResp(msgs=tuple(user_message(i) for i in range(1, 5))))
    task = asyncio.create_task(run_monitor(opts))
    await wait(started.wait())
    if cancel_task:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await wait(task)
    else:
        opts.cancel_event.set()
        await wait(task)
    assert handler_cancelled.is_set()
    assert processed == [1]
    assert "pending in-memory messages" in caplog.text
    if not full_queue:
        assert api.poll_cancelled.is_set()
    assert not any(
        t.get_name() in ("weixin-poll", "weixin-dispatch", "weixin-stop")
        for t in asyncio.all_tasks()
    )


@pytest.mark.asyncio
async def test_monitor_closes_only_owned_client(tmp_path, monkeypatch):
    api = FakeApi()
    monkeypatch.setattr("xdog.claw.channels.weixin.monitor.WeixinApiClient", lambda *_: api)
    opts = replace(options(tmp_path, api, AsyncMock()), api_client=None)
    task = asyncio.create_task(run_monitor(opts))
    await wait(api.polls.get())
    opts.cancel_event.set()
    await wait(task)
    api.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_poll_failure_retries_without_losing_queued_work(tmp_path, monkeypatch):
    api = FakeApi()
    monkeypatch.setattr("xdog.claw.channels.weixin.monitor.RETRY_DELAY_S", 0)
    finished = asyncio.Event()

    async def on_message(msg):
        finished.set()

    opts = options(tmp_path, api, on_message)
    api.updates.put_nowait(RuntimeError("temporary network failure"))
    api.updates.put_nowait(GetUpdatesResp(msgs=(user_message(1),)))
    task = asyncio.create_task(run_monitor(opts))
    try:
        await wait(finished.wait())
        assert not task.done()
    finally:
        opts.cancel_event.set()
        await wait(task)


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [0, -1])
async def test_rejects_unbounded_or_negative_queue(tmp_path, limit):
    opts = options(tmp_path, FakeApi(), AsyncMock(), max_pending_messages=limit)
    with pytest.raises(ValueError, match="max_pending_messages"):
        await run_monitor(opts)
