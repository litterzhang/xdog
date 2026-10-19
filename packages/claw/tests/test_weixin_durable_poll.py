"""The actual upstream cursor cannot acknowledge volatile unaccepted messages."""

from __future__ import annotations

import asyncio

from xdog.agent.session_input import SessionInput
from xdog.claw.channels.weixin.monitor import _load_get_updates_buf, _save_get_updates_buf, run_monitor
from xdog.claw.channels.weixin.types import GetUpdatesResp
from xdog.claw.core.inputs.models import InputEnvelope, InputScope
from xdog.claw.core.inputs.store import InputStore

from packages.claw.tests.test_weixin_monitor import FakeApi, options, user_message, wait


async def test_cursor_and_dispatch_wait_for_actual_durable_acceptance(tmp_path):
    api = FakeApi()
    entered, release, dispatched = asyncio.Event(), asyncio.Event(), asyncio.Event()
    store = await InputStore.open(tmp_path / "private" / "input.sqlite3")
    scope = InputScope("main", tmp_path)

    async def accept(message):
        entered.set()
        await release.wait()
        await store.enqueue(
            InputEnvelope(scope, SessionInput(str(message.message_id), "human", "Weixin", "check", (), {}))
        )

    async def dispatch(message):
        dispatched.set()

    opts = options(tmp_path, api, dispatch, on_accept=accept)
    _save_get_updates_buf(tmp_path, "test", "previous")
    api.updates.put_nowait(GetUpdatesResp(msgs=(user_message(1),), get_updates_buf="next"))
    task = asyncio.create_task(run_monitor(opts))
    try:
        await wait(entered.wait())
        assert _load_get_updates_buf(tmp_path, "test") == "previous" and not dispatched.is_set()
        assert await store.get(scope, "1") is None
        release.set()
        await wait(dispatched.wait())
        assert (await store.get(scope, "1")).status == "pending"
        assert await wait(api.polls.get()) == ("previous", 35000)
        assert await wait(api.polls.get()) == ("next", 35000)
        assert _load_get_updates_buf(tmp_path, "test") == "next"
    finally:
        release.set()
        opts.cancel_event.set()
        await wait(task)
        await store.close()


async def test_partial_batch_failure_reoffers_stable_ids_without_duplicate_intake(tmp_path, monkeypatch):
    import xdog.claw.channels.weixin.monitor as monitor

    monkeypatch.setattr(monitor, "RETRY_DELAY_S", 0.001)
    api = FakeApi()
    store = await InputStore.open(tmp_path / "private" / "input.sqlite3")
    scope = InputScope("main", tmp_path)
    blocked, release, done = asyncio.Event(), asyncio.Event(), asyncio.Event()
    attempts = []
    fail_second = True

    async def accept(message):
        nonlocal fail_second
        attempts.append(message.message_id)
        if message.message_id == 2 and fail_second:
            fail_second = False
            blocked.set()
            await release.wait()
            raise OSError("intake disk unavailable")
        await store.enqueue(
            InputEnvelope(scope, SessionInput(str(message.message_id), "human", "Weixin", "check", (), {}))
        )
        if message.message_id == 2:
            done.set()

    async def dispatch(message):
        pass

    opts = options(tmp_path, api, dispatch, on_accept=accept)
    _save_get_updates_buf(tmp_path, "test", "previous")
    batch = GetUpdatesResp(msgs=(user_message(1), user_message(2)), get_updates_buf="next")
    api.updates.put_nowait(batch)
    task = asyncio.create_task(run_monitor(opts))
    try:
        await wait(blocked.wait())
        assert _load_get_updates_buf(tmp_path, "test") == "previous"
        assert [r.envelope.message.id for r in await store.records(scope)] == ["1"]
        api.updates.put_nowait(batch)
        release.set()
        await wait(done.wait())
        # The next poll proves the successful second acceptance's cursor committed.
        assert (await wait(api.polls.get()))[0] == "previous"
        assert (await wait(api.polls.get()))[0] == "previous"
        assert (await wait(api.polls.get()))[0] == "next"
        assert attempts == [1, 2, 1, 2]
        assert [r.envelope.message.id for r in await store.records(scope)] == ["1", "2"]
    finally:
        release.set()
        opts.cancel_event.set()
        await wait(task)
        await store.close()


async def test_receiver_stop_before_acceptance_never_advances_cursor(tmp_path):
    api = FakeApi()
    entered = asyncio.Event()

    async def accept(message):
        entered.set()
        await asyncio.Event().wait()

    async def dispatch(message):
        raise AssertionError("Unaccepted input cannot be dispatched")

    opts = options(tmp_path, api, dispatch, on_accept=accept)
    _save_get_updates_buf(tmp_path, "test", "previous")
    api.updates.put_nowait(GetUpdatesResp(msgs=(user_message(1),), get_updates_buf="next"))
    task = asyncio.create_task(run_monitor(opts))
    try:
        await wait(entered.wait())
    finally:
        opts.cancel_event.set()
        await wait(task)
    assert _load_get_updates_buf(tmp_path, "test") == "previous"
