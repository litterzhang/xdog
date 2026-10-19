"""Actual Unix ACP observer execution streams through Core's per-message receipts."""
from __future__ import annotations

import asyncio

import pytest
from xdog.ai.types import AssistantMessage, DoneEvent, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream

from packages.claw.tests.eyes_helpers import at
from packages.claw.tests.test_observer_recipients import instruct, speak, until, weixin
from packages.claw.tests.test_weixin_application_intake import sent_parts


@pytest.mark.parametrize("stop", ["none", "notifications", "source"])
async def test_first_observer_message_arrives_before_second_response_finishes(tmp_path, stop):
    cancel_notifications = stop == "notifications"
    async with weixin(tmp_path) as (app, manager, channel, api, clock, model, server):
        instruct(model, at())
        await speak(channel, 1, "alice", "WATCH")
        (created,) = await manager.list("main")
        while not api.sent.empty():
            api.sent.get_nowait()
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []

        def stream(selected, context, options):
            number = len(calls)
            calls.append(context)
            parts = ((TextContent(text="first actual message"),
                      ToolCall(id="inspect", name="eyes", arguments={"action": "list"})) if number == 0 else
                     (TextContent(text="second actual message"),))
            message = AssistantMessage(content=parts, stop_reason="toolUse" if number == 0 else "stop")
            future = asyncio.get_running_loop().create_future()
            future.set_result(message)

            async def events():
                if number == 1:
                    entered.set()
                    await release.wait()
                yield DoneEvent(message=message)
            return EventStream.from_async_generator(events(), result_future=future)

        app._runtimes["main"]._active_session.agent.set_stream_fn(stream)
        clock.advance(240)
        try:
            await asyncio.wait_for(entered.wait(), 5)
            # Producer and UI/event drain run concurrently. Wait for actual
            # delivery while the second response is still held, not for an
            # incidental scheduling order at the second-call entry signal.
            first = await asyncio.wait_for(api.sent.get(), 3)
            assert sent_parts(first) == ("alice", "ctx:alice", "first actual message")
            status = await manager.status("main", created["id"])
            assert status["occurrences"][0]["execution_status"] == "running"
            if stop == "source":
                await manager.cancel("main", created["id"])
                state = await manager.status("main", created["id"])
                assert not state["enabled"] and state["reports_enabled"]
                assert state["occurrences"][0]["execution_status"] == "running"
            if cancel_notifications:
                await manager.cancel_notifications("main", created["id"])
                state = await manager.status("main", created["id"])
                assert state["enabled"] and not state["reports_enabled"]
                assert state["occurrences"][0]["execution_status"] == "running"
            release.set()

            async def completed():
                current = await manager.status("main", created["id"])
                wanted = "cancelled" if cancel_notifications else "delivered"
                return (current if current["occurrences"] and
                        current["occurrences"][0]["delivery_status"] == wanted else None)

            await until(completed)
            if not cancel_notifications:
                second = await asyncio.wait_for(api.sent.get(), 5)
                assert sent_parts(second) == ("alice", "ctx:alice", "second actual message")
                assert first.client_id != second.client_id
            assert api.sent.empty()
            outputs = await (await app.output_service()).store.records()
            message_id = status["occurrences"][0]["message_id"]
            callback = [row for row in outputs if row.envelope.message.id == message_id]
            assert [row.text for row in callback] == ["first actual message", "second actual message"]
            expected = ["delivered", "cancelled" if cancel_notifications else "delivered"]
            assert [row.status for row in callback] == expected
            assert len(calls) == 2
        finally:
            release.set()


async def test_delivery_fact_listener_failure_cannot_relabel_a_real_send_or_veto_execution(tmp_path):
    from xdog.claw.core.outputs.delivery import OutputDelivery
    from xdog.claw.core.outputs.store import OutputStore

    from packages.claw.tests.test_output_store import original

    store = await OutputStore.open(tmp_path / "private" / "outputs.sqlite3")
    sent = []

    async def sender(row):
        sent.append(row.text)

    def failed_projection(scope):
        raise RuntimeError("Optional readonly report projection unavailable")

    delivery = OutputDelivery(store, lambda row: sender)
    delivery.subscribe(failed_projection)
    try:
        await delivery.emit(original(tmp_path), "attempt", "channel", ("actual send",))
        assert sent == ["actual send"] and (await store.records())[0].status == "delivered"
    finally:
        await delivery.close()


async def test_notification_cancel_cannot_relabel_an_entered_unknown_send_as_unsent(tmp_path):
    async with weixin(tmp_path) as (app, manager, channel, api, clock, model, server):
        instruct(model, at())
        await speak(channel, 1, "alice", "WATCH")
        (created,) = await manager.list("main")
        while not api.sent.empty():
            api.sent.get_nowait()
        entered, release = asyncio.Event(), asyncio.Event()
        requests = []

        async def accepted_ack_lost(request):
            requests.append(request.msg)
            entered.set()
            await release.wait()
            raise ConnectionError("Recipient accepted output; ACK unavailable")

        api.send_message = accepted_ack_lost
        clock.advance(240)
        try:
            await asyncio.wait_for(entered.wait(), 5)
            await manager.cancel_notifications("main", created["id"])
            release.set()

            async def unknown():
                current = await manager.status("main", created["id"])
                rows = current["occurrences"]
                return current if rows and rows[0]["delivery_status"] == "unknown" else None

            current = await until(unknown)
            assert not current["reports_enabled"]
            await manager.cancel_notifications("main", created["id"])
            await (await app.output_service()).resume()
            assert len(requests) == 1 and model.calls.count("CALLBACK") == 1
            current = await manager.status("main", created["id"])
            assert current["occurrences"][0]["delivery_status"] == "unknown"
        finally:
            release.set()


async def test_bounded_report_projection_never_corrupts_json_or_truncates_core_send_facts(tmp_path):
    import json

    async with weixin(tmp_path) as (app, manager, channel, api, clock, model, server):
        instruct(model, at())
        await speak(channel, 1, "alice", "WATCH")
        (created,) = await manager.list("main")
        while not api.sent.empty():
            api.sent.get_nowait()
        detail = "海" * 3000

        async def unknown(request):
            raise ConnectionError(detail)

        api.send_message = unknown
        clock.advance(240)

        async def failed_send():
            current = await manager.status("main", created["id"])
            rows = current["occurrences"]
            return rows[0] if rows and rows[0]["delivery_status"] == "unknown" else None

        row = await until(failed_send)
        projected = json.loads(row["delivery_error"])
        assert len(row["delivery_error"].encode()) <= 4096
        assert projected[0]["error_truncated"] is True
        outputs = await (await app.output_service()).store.records()
        current = [item for item in outputs if item.envelope.message.id == row["message_id"]]
        assert current[0].error == detail and current[0].status == "unknown"
