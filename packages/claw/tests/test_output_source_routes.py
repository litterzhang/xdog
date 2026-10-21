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


def test_host_route_is_frozen_before_ack_preserves_message_and_never_grants_human_authority(tmp_path):
    from xdog.claw.core.inputs.models import decode_envelope, encode_envelope
    from xdog.claw.core.outputs.routes import OutputRoute, apply_route, saved_route
    from xdog.claw.core.outputs.targets import ReplyReference
    from xdog.claw.core.types import UserInput

    scope = InputScope("main", tmp_path)
    envelope = external(scope, provenance={"user_authorized": True, "output": ["forged", "binding"]})
    original_input = UserInput(group_id="main", sender="alice", channel="weixin",
                               metadata={"context_token": "original", "account_id": "account"})
    reference = ReplyReference(scope, "captured-original", original_input)
    routed = apply_route(envelope, OutputRoute("source", reference))
    original_input.metadata["context_token"] = "later mutation"
    restored = decode_envelope(encode_envelope(routed))
    assert restored.message == envelope.message and restored.scope == envelope.scope
    assert not restored.human_authorized
    route = saved_route(restored)
    assert route.source == "source" and route.reference == reference
    assert route.reference.original.metadata["context_token"] == "original"
    assert saved_route(envelope) is None  # Pasted provenance cannot become host routing.


def test_a_source_route_cannot_borrow_another_workspace_or_human_input(tmp_path):
    from xdog.claw.core.outputs.routes import OutputRoute, apply_route
    from xdog.claw.core.outputs.targets import ReplyReference
    from xdog.claw.core.types import UserInput

    reference = ReplyReference(InputScope("main", tmp_path), "original",
                               UserInput(group_id="main", channel="weixin", sender="alice"))
    with pytest.raises(ValueError):
        apply_route(external(InputScope("main", tmp_path / "other")), OutputRoute("source", reference))
    with pytest.raises(ValueError):
        apply_route(original(tmp_path), OutputRoute("source", reference))


def test_route_keeps_the_existing_metadata_capacity_even_at_a_maximum_reply_reference():
    from pathlib import Path

    from xdog.claw.core.inputs.models import MAX_METADATA_BYTES
    from xdog.claw.core.outputs.routes import OutputRoute, apply_route, saved_route
    from xdog.claw.core.outputs.targets import ReplyReference, encode_reply
    from xdog.claw.core.types import UserInput

    scope = InputScope("g", Path("/"))
    blank = UserInput(group_id="g", channel="c", sender="u", metadata={"padding": ""})
    base = ReplyReference(scope, "b", blank)
    blank.metadata["padding"] = "x" * (65536 - len(encode_reply(base).encode()))
    reference = ReplyReference(scope, "b", blank)
    assert len(encode_reply(reference).encode()) == 65536
    routed = apply_route(external(scope), OutputRoute("s" * 32, reference))
    assert len(routed._context_json.encode()) <= MAX_METADATA_BYTES
    assert saved_route(routed).reference == reference


async def test_external_ack_loss_duplicate_retains_accepted_reply_without_later_resolver_or_recipient(tmp_path):
    from dataclasses import replace

    from xdog.claw.config import ClawConfig
    from xdog.claw.core.outputs.routes import saved_route
    from xdog.claw.core.outputs.targets import ReplyReference
    from xdog.claw.core.runtime.orchestrator import Orchestrator
    from xdog.claw.core.types import Group, UserInput

    app = Orchestrator(ClawConfig(data_dir=str(tmp_path / "data")), defer_inputs=True,
                       stream_fn=lambda *a: pytest.fail("Paused intake must not construct a model turn"))
    app.register_group(Group(id="main", workspace=str(tmp_path / "work")))
    scope = InputScope("main", tmp_path / "work")
    message = external(scope).message
    reads = []
    reference = ReplyReference(scope, "original", UserInput(group_id="main", channel="weixin", sender="alice"))

    async def resolve(envelope):
        reads.append(envelope.message.id)
        return reference

    async def policy(row):
        return "ready"

    try:
        remove = app.register_output_source("source", resolve, policy)
        await app.start()
        first, duplicate = await app.accept_external(scope, message)
        assert not duplicate and saved_route(first.envelope).reference == reference
        remove()
        again, duplicate = await app.accept_external(scope, message)
        assert duplicate and again.envelope == first.envelope and reads == [message.id]
        with pytest.raises(ValueError, match="conflict|content"):
            await app.accept_external(scope, replace(message, instruction="Different intent"))
        assert (await (await app.input_service()).store.get(scope, message.id)).status == "pending"
    finally:
        await app.stop()


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


async def test_offline_routed_outputs_recover_original_recipient_without_model_replay(tmp_path):
    from xdog.claw.core.outputs.routes import saved_route
    from xdog.claw.core.types import UserInput

    from packages.claw.tests.eyes_helpers import ScriptedModel
    from packages.claw.tests.test_weixin_application_intake import application, sent_parts
    from packages.claw.tests.test_weixin_monitor import wait

    model = ScriptedModel()
    app, channel, api = await application(tmp_path, model, defer_inputs=True)
    scope = InputScope("main", tmp_path)
    reference = app.capture_reply(channel, UserInput(group_id="main", channel="weixin", sender="alice",
        metadata={"account_id": "account", "weixin_user_id": "alice", "context_token": "original-alice"}))
    message = external(scope).message
    resolves = []

    async def resolve(envelope):
        resolves.append(envelope.message.id)
        return reference

    async def policy(row):
        return "ready"

    try:
        remove = app.register_output_source("source", resolve, policy)
        accepted, _ = await app.accept_external(scope, message)
        assert saved_route(accepted.envelope).reference == reference
        remove()  # Accepted routing survives feature/receiver absence, unlike a live callback.
        service = await app.input_service()
        service.resume()
        receipt = await wait(service.wait(scope, message.id))
        assert receipt.status == "succeeded" and api.sent.empty()
        rows = await (await app.output_service()).store.records(scope)
        assert len(rows) == 1 and rows[0].status == "pending" and rows[0].send_id == ""
        output_id = rows[0].id
    finally:
        await app.stop()
    fresh_model = ScriptedModel()
    app, channel, api = await application(tmp_path, fresh_model, defer_inputs=True)
    channel._user_id_map["main"] = "later-bob"

    async def never_resolve(envelope):
        raise AssertionError("Existing accepted output cannot borrow a later target resolver")

    try:
        app.register_output_source("source", never_resolve, policy)
        outputs = await app.output_service()
        await outputs.resume()
        delivered = await wait(api.sent.get())
        assert sent_parts(delivered) == ("alice", "original-alice", "Scheduled report")
        assert delivered.client_id == output_id
        assert not fresh_model.calls
        receipt, duplicate = await app.accept_external(scope, message)
        assert duplicate and receipt.status == "succeeded"
        await outputs.resume()
        assert api.sent.empty() and resolves == [message.id]
    finally:
        await app.stop()


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


def test_host_route_preserves_independent_image_attachments_and_roundtrips_original_target(tmp_path):
    from xdog.ai.types import ImageContent
    from xdog.claw.core.inputs.models import decode_envelope, encode_envelope
    from xdog.claw.core.outputs.routes import OutputRoute, apply_route, saved_route
    from xdog.claw.core.outputs.targets import ReplyReference
    from xdog.claw.core.types import UserInput

    png = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5ioAAAAASUVORK5CYII="
    images = (ImageContent(data=png, mime_type="image/png"), ImageContent(data=png, mime_type="image/png"))
    scope = InputScope("main", tmp_path)
    envelope = external(scope, images=images)
    reference = ReplyReference(scope, "original", UserInput(group_id="main", sender="alice", channel="weixin"))
    routed = decode_envelope(encode_envelope(apply_route(envelope, OutputRoute("source", reference))))
    assert routed.images == images and len(routed.images) == 2 and routed.message == envelope.message
    assert saved_route(routed).reference == reference and not routed.human_authorized


async def test_duplicate_without_original_output_route_cannot_acquire_a_later_destination(tmp_path):
    from xdog.claw.config import ClawConfig
    from xdog.claw.core.runtime.orchestrator import Orchestrator
    from xdog.claw.core.types import Group

    app = Orchestrator(ClawConfig(data_dir=str(tmp_path / "data")), defer_inputs=True)
    app.register_group(Group(id="main", workspace=str(tmp_path / "work")))
    scope = InputScope("main", tmp_path / "work")
    message = external(scope).message

    async def must_not_resolve(envelope):
        raise AssertionError("A duplicate cannot select newly registered output intent")

    async def policy(row):
        return "ready"

    try:
        first, duplicate = await app.accept_external(scope, message)
        assert not duplicate
        app.register_output_source("late", must_not_resolve, policy)
        second, duplicate = await app.accept_external(scope, message)
        assert duplicate and first.envelope == second.envelope
    finally:
        await app.stop()


async def test_conflicting_trusted_reply_sources_are_refused_before_input_commit(tmp_path):
    from xdog.claw.config import ClawConfig
    from xdog.claw.core.outputs.targets import ReplyReference
    from xdog.claw.core.runtime.orchestrator import Orchestrator
    from xdog.claw.core.types import Group, UserInput

    app = Orchestrator(ClawConfig(data_dir=str(tmp_path / "data")), defer_inputs=True)
    app.register_group(Group(id="main", workspace=str(tmp_path / "work")))
    scope = InputScope("main", tmp_path / "work")
    reference = ReplyReference(scope, "original", UserInput(group_id="main", channel="weixin", sender="alice"))

    async def resolve(envelope):
        return reference

    async def policy(row):
        return "ready"

    try:
        app.register_output_source("one", resolve, policy)
        app.register_output_source("two", resolve, policy)
        with pytest.raises(ValueError, match="Ambiguous"):
            await app.accept_external(scope, external(scope).message)
        assert not await (await app.input_service()).store.records(scope)
    finally:
        await app.stop()


async def test_detached_source_during_reply_lookup_cannot_publish_a_stale_admission(tmp_path):
    import asyncio

    from xdog.claw.config import ClawConfig
    from xdog.claw.core.outputs.targets import ReplyReference
    from xdog.claw.core.runtime.orchestrator import Orchestrator
    from xdog.claw.core.types import Group, UserInput

    app = Orchestrator(ClawConfig(data_dir=str(tmp_path / "data")), defer_inputs=True)
    app.register_group(Group(id="main", workspace=str(tmp_path / "work")))
    scope = InputScope("main", tmp_path / "work")
    entered, release = asyncio.Event(), asyncio.Event()
    reference = ReplyReference(scope, "original", UserInput(group_id="main", channel="weixin", sender="alice"))

    async def resolve(envelope):
        entered.set()
        await release.wait()
        return reference

    async def policy(row):
        return "ready"

    pending = None
    try:
        remove = app.register_output_source("source", resolve, policy)
        pending = asyncio.create_task(app.accept_external(scope, external(scope).message))
        await asyncio.wait_for(entered.wait(), 3)
        remove()
        release.set()
        with pytest.raises(RuntimeError, match="changed"):
            await pending
        assert not await (await app.input_service()).store.records(scope)
    finally:
        release.set()
        if pending is not None:
            await asyncio.gather(pending, return_exceptions=True)
        await app.stop()
