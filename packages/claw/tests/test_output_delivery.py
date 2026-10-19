"""Channel transport failures cannot change completed Agent execution facts."""

from __future__ import annotations

import asyncio

import pytest
from xdog.claw.core.inputs.models import InputScope
from xdog.claw.core.runtime.input_adapter import freeze_input
from xdog.claw.core.types import UserInput

from packages.claw.tests.eyes_helpers import ScriptedModel
from packages.claw.tests.test_weixin_application_intake import application, sent_parts, text
from packages.claw.tests.test_weixin_monitor import wait


def envelope(tmp_path, group="main"):
    return freeze_input(
        UserInput(group_id=group, content="original", sender="alice", channel="weixin"), tmp_path, message_id="one"
    )


async def test_ack_loss_keeps_actual_success_and_history_without_duplicate_send(tmp_path):
    model = ScriptedModel()
    orch, channel, api = await application(tmp_path, model)

    async def ack_lost(request):
        api.sent.put_nowait(request.msg)
        raise ConnectionError("network accepted but ACK lost")

    api.send_message = ack_lost
    offered = text(1, "alice", "original")
    try:
        await channel._accept_inbound(offered)
        inputs = await orch.input_service()
        scope = InputScope("main", tmp_path)
        submitted = (await inputs.store.records(scope))[0]
        receipt = await wait(inputs.wait(scope, submitted.envelope.message.id))
        assert receipt.status == "succeeded" and receipt.response == "Scheduled report"
        assert receipt.error == ""
        outputs = await orch.output_service()
        rows = await outputs.store.records(scope)
        assert len(rows) == 1 and rows[0].status == "unknown"
        accepted = await wait(api.sent.get())
        assert sent_parts(accepted) == ("alice", "ctx:alice", "Scheduled report")
        assert accepted.client_id == rows[0].id
        history = orch._runtimes["main"].get_or_create_session().agent.messages
        assert any("Scheduled report" in str(message.content) for message in history)
        await channel._accept_inbound(offered)
        await channel._on_inbound(offered)
        await outputs.resume()
        assert model.calls == ["original"] and api.sent.empty()
    finally:
        await orch.stop()


async def test_positive_not_sent_restarts_the_same_output_without_another_model(tmp_path):
    from xdog.claw.core.outputs.delivery import DeliveryNotSent

    model = ScriptedModel()
    orch, channel, api = await application(tmp_path, model)

    async def rejected(request):
        raise DeliveryNotSent("explicit API rejection before acceptance")

    api.send_message = rejected
    try:
        await channel._accept_inbound(text(1, "alice", "original"))
        inputs = await orch.input_service()
        scope = InputScope("main", tmp_path)
        submitted = (await inputs.store.records(scope))[0]
        assert (await wait(inputs.wait(scope, submitted.envelope.message.id))).status == "succeeded"
        rows = await (await orch.output_service()).store.records(scope)
        assert len(rows) == 1 and rows[0].status == "pending"
        output_id = rows[0].id
    finally:
        await orch.stop()
    fresh_model = ScriptedModel()
    orch, channel, api = await application(tmp_path, fresh_model)
    channel._user_id_map["main"] = "not-the-original"
    try:
        outputs = await orch.output_service()
        await outputs.resume()
        accepted = await wait(api.sent.get())
        assert accepted.client_id == output_id
        assert sent_parts(accepted) == ("alice", "ctx:alice", "Scheduled report")
        rows = await outputs.store.records(scope)
        assert rows[0].id == output_id and rows[0].status == "delivered"
        await outputs.resume()
        assert api.sent.empty() and fresh_model.calls == []
    finally:
        await orch.stop()


async def test_delivery_is_independent_per_scope_and_never_replays_delivered_chunks(tmp_path):
    from xdog.claw.core.outputs.delivery import OutputDelivery
    from xdog.claw.core.outputs.store import OutputStore

    store = await OutputStore.open(tmp_path / "private" / "output.sqlite3")
    entered, release = asyncio.Event(), asyncio.Event()
    sent = []

    async def send(row):
        if row.envelope.scope.group_id == "blocked":
            entered.set()
            await release.wait()
        sent.append((row.envelope.scope.group_id, row.ordinal, row.text))

    delivery = OutputDelivery(store, lambda row: send)
    slow = asyncio.create_task(delivery.emit(envelope(tmp_path, "blocked"), "execution-1", "channel", ("slow",)))
    try:
        await wait(entered.wait())
        assert await wait(delivery.emit(envelope(tmp_path), "execution-1", "channel", ("progress", "done"))) == 2
        assert sent == [("main", 0, "progress"), ("main", 1, "done")]
        release.set()
        await wait(slow)
        await delivery.resume()
        assert sent == [("main", 0, "progress"), ("main", 1, "done"), ("blocked", 0, "slow")]
    finally:
        release.set()
        await asyncio.gather(slow, return_exceptions=True)
        await delivery.close()


async def test_missing_original_endpoint_stays_pending_without_borrowing_another_tui(tmp_path):
    from xdog.claw.channels.tui.channel import TuiChannel
    from xdog.claw.core.outputs.delivery import OutputDelivery
    from xdog.claw.core.outputs.store import OutputStore
    from xdog.claw.core.runtime.channel_adapter import ChannelReplies

    store = await OutputStore.open(tmp_path / "private" / "output.sqlite3")
    registry = ChannelReplies()
    new_connection = TuiChannel()
    registry.register(new_connection)
    seen = []

    async def send(group_id, body):
        seen.append(body)

    new_connection.send_message = send
    original = freeze_input(
        UserInput(group_id="main", content="old input", channel="tui", sender="user"),
        tmp_path,
        message_id="old-connection-input",
    )
    delivery = OutputDelivery(store, registry.resolve)
    try:
        await delivery.emit(original, "execution-1", "old-connection-binding", ("only for old client",))
        assert seen == [] and (await store.records())[0].status == "pending"
    finally:
        await delivery.close()


async def test_closing_owned_send_marks_unknown_and_never_erases_accepted_output(tmp_path):
    from xdog.claw.core.outputs.delivery import OutputDelivery
    from xdog.claw.core.outputs.store import OutputStore

    store = await OutputStore.open(tmp_path / "private" / "output.sqlite3")
    entered = asyncio.Event()

    async def send(row):
        entered.set()
        await asyncio.Event().wait()

    delivery = OutputDelivery(store, lambda row: send)
    emitting = asyncio.create_task(delivery.emit(envelope(tmp_path), "execution-1", "channel", ("generated text",)))
    await wait(entered.wait())
    emitting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await emitting
    await wait(delivery.close())
    reopened = await OutputStore.open(store.path)
    try:
        row = (await reopened.records())[0]
        assert row.status == "unknown" and row.text == "generated text"
    finally:
        await reopened.close()


async def test_cancellation_after_begin_commit_before_sender_remains_proven_not_sent(tmp_path):
    from xdog.claw.core.outputs.delivery import OutputDelivery
    from xdog.claw.core.outputs.store import OutputStore

    store = await OutputStore.open(tmp_path / "private" / "output.sqlite3")
    entered = asyncio.Event()
    begin = store.begin

    async def delayed_begin(identifier):
        await begin(identifier)
        entered.set()
        await asyncio.Event().wait()

    store.begin = delayed_begin
    sent = []

    async def send(row):
        sent.append(row.text)

    delivery = OutputDelivery(store, lambda row: send)
    emitting = asyncio.create_task(delivery.emit(envelope(tmp_path), "execution-1", "channel", ("generated text",)))
    await wait(entered.wait())
    await wait(delivery.close())
    await asyncio.gather(emitting, return_exceptions=True)
    reopened = await OutputStore.open(store.path)
    try:
        assert sent == [] and (await reopened.records())[0].status == "pending"
    finally:
        await reopened.close()


async def test_actual_channel_stream_keeps_progress_and_final_separate_with_durable_ids(tmp_path):
    from xdog.ai.types import AssistantMessage, TextContent

    from packages.claw.tests.test_orchestrator import _progress_message, _scripted_stream_fn

    path = tmp_path / "evidence.txt"
    path.write_text("actual tool evidence")

    async def before(index):
        pass

    stream = _scripted_stream_fn(
        [
            _progress_message("Checking.", path, "read-1"),
            AssistantMessage(content=(TextContent(text="Done."),)),
        ],
        before,
    )
    orch, channel, api = await application(tmp_path, stream, enabled_tools=("filesystem",))
    try:
        await channel._accept_inbound(text(1, "alice", "check file"))
        inputs = await orch.input_service()
        scope = InputScope("main", tmp_path)
        submitted = (await inputs.store.records(scope))[0]
        receipt = await wait(inputs.wait(scope, submitted.envelope.message.id))
        assert receipt.status == "succeeded" and receipt.response == "Checking.\n\nDone."
        first, final = await wait(api.sent.get()), await wait(api.sent.get())
        assert [sent_parts(first), sent_parts(final)] == [
            ("alice", "ctx:alice", "Checking."),
            ("alice", "ctx:alice", "Done."),
        ]
        rows = await (await orch.output_service()).store.records(scope)
        assert [(row.ordinal, row.text, row.status) for row in rows] == [
            (0, "Checking.", "delivered"),
            (1, "Done.", "delivered"),
        ]
        assert [row.id for row in rows] == [first.client_id, final.client_id]
        assert rows[0].id != rows[1].id and api.sent.empty()
    finally:
        await orch.stop()
