"""Generic Core output controls never cancel actual execution or uncertain sends."""
from __future__ import annotations

import sqlite3

import pytest
from xdog.claw.core.inputs.models import InputScope
from xdog.claw.core.outputs.store import OutputStore

from packages.claw.tests.test_output_store import original


async def test_cancel_pending_is_scoped_idempotent_and_durable_without_a_send_attempt(tmp_path):
    path = tmp_path / "private" / "outputs.sqlite3"
    store = await OutputStore.open(path)
    envelope = original(tmp_path)
    try:
        row = await store.append(envelope, "attempt", 0, "original-channel", "unsent")
        assert await store.cancel_pending(InputScope("other", tmp_path), (envelope.message.id,)) == ()
        assert await store.cancel_pending(envelope.scope, (envelope.message.id,)) == (row.id,)
        assert await store.cancel_pending(envelope.scope, (envelope.message.id,)) == ()
        with pytest.raises(ValueError, match="sent|pending|send"):
            await store.begin(row.id)
    finally:
        await store.close()
    store = await OutputStore.open(path)
    try:
        result = (await store.records(envelope.scope))[0]
        assert result.status == "cancelled" and result.send_id == "" and result.text == "unsent"
        with sqlite3.connect(path) as db:
            assert db.execute("SELECT count(*) FROM send_attempts").fetchone()[0] == 0
    finally:
        await store.close()


async def test_cancelling_known_not_sent_keeps_the_real_attempt_receipt_unchanged(tmp_path):
    path = tmp_path / "private" / "outputs.sqlite3"
    store = await OutputStore.open(path)
    envelope = original(tmp_path)
    try:
        row = await store.append(envelope, "attempt", 0, "original-channel", "not delivered")
        started = await store.begin(row.id)
        await store.settle(row.id, started.send_id, "pending", "receiver positively rejected")
        assert await store.cancel_pending(envelope.scope, (envelope.message.id,)) == (row.id,)
    finally:
        await store.close()
    store = await OutputStore.open(path)
    try:
        result = (await store.records())[0]
        assert result.status == "cancelled" and result.send_id == started.send_id
        assert result.error == "receiver positively rejected"
        with sqlite3.connect(path) as db:
            assert db.execute("SELECT status,error FROM send_attempts").fetchall() == [
                ("not_sent", "receiver positively rejected")]
    finally:
        await store.close()


@pytest.mark.parametrize("outcome", ["sending", "delivered", "unknown"])
async def test_notification_cancellation_never_rewrites_an_entered_or_uncertain_send(tmp_path, outcome):
    store = await OutputStore.open(tmp_path / "private" / "outputs.sqlite3")
    envelope = original(tmp_path)
    try:
        row = await store.append(envelope, "attempt", 0, "original-channel", "possibly observed")
        started = await store.begin(row.id)
        if outcome != "sending":
            await store.settle(row.id, started.send_id, outcome)
        assert await store.cancel_pending(envelope.scope, (envelope.message.id,)) == ()
        result = (await store.records())[0]
        assert result.status == outcome and result.send_id == started.send_id
    finally:
        await store.close()


async def test_cancelled_outputs_never_resume_transport_or_get_a_new_output_identity(tmp_path):
    from xdog.claw.core.outputs.delivery import OutputDelivery

    store = await OutputStore.open(tmp_path / "private" / "outputs.sqlite3")
    received = []

    async def send(row):
        received.append(row.text)

    delivery = OutputDelivery(store, lambda row: send)
    envelope = original(tmp_path)
    try:
        row = await store.append(envelope, "attempt", 0, "original-channel", "never sent")
        await store.cancel_pending(envelope.scope, (envelope.message.id,))
        await delivery.resume()
        assert received == []
        same = await store.append(envelope, "attempt", 0, "original-channel", "never sent")
        assert same.id == row.id and same.status == "cancelled"
    finally:
        await delivery.close()


async def test_old_output_format_is_refused_before_recovery_with_bytes_preserved(tmp_path):
    path = tmp_path / "private" / "outputs.sqlite3"
    store = await OutputStore.open(path)
    row = await store.append(original(tmp_path), "attempt", 0, "original-channel", "uncertain")
    await store.begin(row.id)
    await store.close()
    with sqlite3.connect(path) as db:
        db.execute("UPDATE metadata SET format='xdog.claw.outputs.v1'")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="format|[Uu]nsupported"):
        await OutputStore.open(path)
    assert path.read_bytes() == before
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT status FROM outputs").fetchone()[0] == "sending"


def external(scope, *, message_id="callback", provenance=None, images=()):
    from xdog.agent.session_input import SessionInput
    from xdog.claw.core.runtime.input_adapter import freeze_input
    from xdog.claw.core.types import ExternalInput

    message = SessionInput(message_id, "acp", "not identity", "Inspect original output", (), provenance or {})
    return freeze_input(ExternalInput(group_id=scope.group_id, sender="acp", channel="acp", content="",
                                     metadata={"input_origin": "external"}, canonical=message, images=images),
                        scope.workspace, message_id=message_id)










async def test_missing_source_policy_keeps_original_output_pending_without_publishing_a_send(tmp_path):
    from xdog.claw.core.outputs.delivery import OutputDelivery

    store = await OutputStore.open(tmp_path / "private" / "outputs.sqlite3")
    received, state = [], ["pending"]

    async def send(row):
        received.append(row.text)

    async def gate(row):
        return state[0]

    delivery = OutputDelivery(store, lambda row: send, gate=gate)
    try:
        await delivery.emit(original(tmp_path), "attempt", "channel", ("first",))
        row = (await store.records())[0]
        assert row.status == "pending" and row.send_id == "" and not received
        state[0] = "ready"
        await delivery.resume()
        assert received == ["first"] and (await store.records())[0].status == "delivered"
    finally:
        await delivery.close()


async def test_notification_cancel_after_begin_but_before_sender_is_known_not_sent(tmp_path, monkeypatch):
    import asyncio

    from xdog.claw.core.outputs.delivery import OutputDelivery

    store = await OutputStore.open(tmp_path / "private" / "outputs.sqlite3")
    entered, release = asyncio.Event(), asyncio.Event()
    received, state = [], ["ready"]
    begin = store.begin

    async def paused(identifier):
        row = await begin(identifier)
        entered.set()
        await release.wait()
        return row

    async def send(row):
        received.append(row.text)

    async def gate(row):
        return state[0]

    monkeypatch.setattr(store, "begin", paused)
    delivery = OutputDelivery(store, lambda row: send, gate=gate)
    sending = asyncio.create_task(delivery.emit(original(tmp_path), "attempt", "channel", ("cancel before send",)))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        state[0] = "cancelled"
        release.set()
        await sending
        row = (await store.records())[0]
        assert row.status == "cancelled" and row.send_id and not received
        with sqlite3.connect(store.path) as db:
            assert db.execute("SELECT status FROM send_attempts").fetchall() == [("not_sent",)]
    finally:
        release.set()
        await sending
        await delivery.close()


async def test_explicit_pending_cancel_does_not_block_or_rewrite_an_already_entered_sender(tmp_path):
    import asyncio

    from xdog.claw.core.outputs.delivery import OutputDelivery

    store = await OutputStore.open(tmp_path / "private" / "outputs.sqlite3")
    envelope = original(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    received = []

    async def send(row):
        entered.set()
        await release.wait()
        received.append(row.text)

    delivery = OutputDelivery(store, lambda row: send)
    sending = asyncio.create_task(delivery.emit(envelope, "attempt", "channel", ("entered", "not entered")))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        cancelled = await asyncio.wait_for(delivery.cancel_pending(envelope.scope, (envelope.message.id,)), 1)
        assert len(cancelled) == 1
        release.set()
        await sending
        assert received == ["entered"]
        assert [row.status for row in await store.records()] == ["delivered", "cancelled"]
    finally:
        release.set()
        await sending
        await delivery.close()




async def test_policy_read_failure_after_begin_is_known_not_sent_not_unknown(tmp_path):
    from xdog.claw.core.outputs.delivery import OutputDelivery

    store = await OutputStore.open(tmp_path / "private" / "outputs.sqlite3")
    received, checked = [], []

    async def send(row):
        received.append(row.text)

    async def gate(row):
        checked.append(row.status)
        if row.status == "sending":
            raise RuntimeError("Policy service closed before sender entry")
        return "ready"

    delivery = OutputDelivery(store, lambda row: send, gate=gate)
    try:
        await delivery.emit(original(tmp_path), "attempt", "channel", ("not sent",))
        row = (await store.records())[0]
        assert checked == ["pending", "sending"] and not received
        assert row.status == "pending" and row.send_id
        with sqlite3.connect(store.path) as db:
            assert db.execute("SELECT status FROM send_attempts").fetchall() == [("not_sent",)]
    finally:
        await delivery.close()










async def test_retired_implicit_route_is_not_replayed_as_ordinary_output(tmp_path):
    from unittest.mock import AsyncMock

    from xdog.agent.session_input import SessionInput
    from xdog.claw.core.inputs.models import InputEnvelope, ReplyTarget
    from xdog.claw.core.outputs.delivery import OutputDelivery

    scope = InputScope('main', tmp_path)
    envelope = InputEnvelope(scope, SessionInput('old', 'acp', 'external', 'old work', (), {}),
        ReplyTarget('weixin', 'original-peer'), context={'kind': 'external', 'output': ['retired', 'binding']})
    store = await OutputStore.open(tmp_path / 'outputs.sqlite3')
    send = AsyncMock()
    delivery = OutputDelivery(store, lambda _: send)
    try:
        row = await store.append(envelope, 'original-attempt', 0, 'binding', 'old report')
        await delivery.resume()
        send.assert_not_called()
        saved, = await store.records()
        assert saved.id == row.id and saved.status == 'pending'
    finally:
        await delivery.close()
        await store.close()
