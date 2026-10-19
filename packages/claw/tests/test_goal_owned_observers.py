"""Goal-owned Eyes policy on the actual file -> Unix ACP -> group Agent path.

Bounds come from eyes_helpers.until and the fixture's awaited shutdown. Tests do
not launch a provider, daemon, goal engine or an alternative observation loop.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import sqlite3
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from xdog.agent.session_input import SessionInput
from xdog.claw.core.inputs.models import InputScope
from xdog.claw.core.inputs.store import InputStore
from xdog.claw.core.observers.journal import ObserverJournal
from xdog.claw.core.observers.manager import ManagedObservers
from xdog.claw.core.observers.models import ObserverStopped

from packages.claw.tests.core_inputs import external_envelope
from packages.claw.tests.eyes_helpers import at
from packages.claw.tests.eyes_helpers import managed as managed

OWNER = "g-owned"


def file_source():
    return {"type": "file", "root": ".", "include": ["task-report.json"], "poll_interval_ms": 10}


async def create_file(harness, *, name="owned-file", group="g1", **policy):
    root = harness.orch.get_group_workspace(group)
    root.mkdir(parents=True, exist_ok=True)
    path = root / "task-report.json"
    if not path.exists():
        path.write_text('{"revision":1}')
    created = await harness.manager.create(group, name, file_source(), "reconcile task", **policy)
    await watching(harness, created["id"], group)
    return created, path


async def watching(harness, identifier, group="g1"):
    async def check():
        status = await harness.manager.status(group, identifier)
        if status["state"] in {"failed", "needs_review"}:
            record = await harness.record(identifier, group)
            pending = record.state.get("pending")
            pytest.fail(
                f"Owned file could not resume: state={status['state']}, error={status['error']}, "
                f"observer_id={identifier}, pending_registration="
                f"{pending.get('registration_id') if isinstance(pending, dict) else None}", pytrace=False,
            )
        return status if status["state"] == "watching" else None
    return await harness.until(check)


async def settled(harness, identifier, count=1, group="g1"):
    async def check():
        status = await harness.manager.status(group, identifier)
        rows = status["occurrences"]
        if len(rows) != count or any(
            row["execution_status"] != "succeeded"
            or row["delivery_status"] not in {"delivered", "not-required"} for row in rows
        ):
            return None
        return status
    return await harness.until(check)


async def test_default_policy_is_unowned_broadcast_and_file_is_not_auto_restored(managed):
    created, _ = await create_file(managed)
    record = await managed.record(created["id"])
    assert record.owner == created["owner"] == ""
    assert record.notify_only is created["notify_only"] is False
    assert record.recovery == "ephemeral"
    columns, policy = await managed.manager.store._disk(lambda db: (
        {row[1]: row[2] for row in db.execute("PRAGMA table_info(observers)")},
        tuple(db.execute("SELECT owner,notify_only FROM observers WHERE observer_id=?",
                         (record.id,)).fetchone()),
    ))
    assert columns["owner"] == "TEXT" and columns["notify_only"] == "INTEGER"
    assert policy == ("", 0)
    await managed.restart()
    restored = await managed.record(record.id)
    assert restored == record
    assert record.id not in managed.manager._files
    assert (await managed.manager.status("g1", record.id))["state"] == "needs-start"
    with pytest.raises(ValueError, match="Unknown owned observer"):
        await managed.manager.resume_owned("g1", record.id, owner=OWNER)
    assert not managed.model.calls
    managed.channel.send_message.assert_not_awaited()


async def test_owned_file_explicit_restore_retains_identity_and_executes_without_broadcast(managed):
    created, path = await create_file(managed, owner=OWNER, notify_only=True)
    original = await managed.record(created["id"])
    await managed.restart()
    assert original.id not in managed.manager._files
    status = await managed.manager.resume_owned("g1", original.id, owner=OWNER)
    assert (status["id"], status["revision"], status["owner"], status["notify_only"]) == (
        original.id, original.revision, OWNER, True,
    )
    await watching(managed, original.id)
    service = managed.manager._files[original.id]
    await managed.manager.resume_owned("g1", original.id, owner=OWNER)
    assert managed.manager._files[original.id] is service
    assert await managed.record(original.id) == original
    path.write_text('{"revision":2}')
    status = await settled(managed, original.id)
    row, = status["occurrences"]
    receipt = await managed.server.backend.inputs.store.get(InputScope("g1", original.cwd), row["message_id"])
    assert receipt is not None and receipt.status == "succeeded" and receipt.response == "Scheduled report"
    assert row["started"] == 1 and row["receipt_status"] == "accepted"
    assert row["delivery_status"] == "not-required"
    assert managed.model.calls == ["reconcile task"]
    other_scope = InputScope("g2", managed.orch.get_group_workspace("g2"))
    assert await managed.server.backend.inputs.store.records(other_scope) == ()
    managed.channel.send_message.assert_not_awaited()


@pytest.mark.parametrize("terminal", ["cancelled", "needs_review"])
async def test_owned_terminal_files_cannot_be_reenabled_by_resume_or_duplicate_create(managed, terminal):
    created, path = await create_file(managed, owner=OWNER, notify_only=True)
    original = await managed.record(created["id"])
    if terminal == "cancelled":
        await managed.manager.store.cancel("g1", original.cwd, original.id)
    else:
        await managed.manager.store.fail(original, "inspect interrupted task effects", review=True)
    await managed.manager.close()
    await managed.restart()
    before = await managed.record(original.id)
    status = await managed.manager.resume_owned("g1", original.id, owner=OWNER)
    duplicate = await managed.manager.create("g1", "owned-file", file_source(), "reconcile task",
                                             owner=OWNER, notify_only=True)
    assert not status["enabled"] and status["state"] == terminal
    assert duplicate["id"] == original.id and not duplicate["enabled"]
    assert await managed.record(original.id) == before
    assert original.id not in managed.manager._files
    assert before.owner == OWNER and before.notify_only is True
    path.write_text('{"revision":3}')
    await managed.flush()
    assert not managed.model.calls
    managed.channel.send_message.assert_not_awaited()


async def test_cross_group_cannot_resume_or_cancel_another_owners_file(managed):
    created, _ = await create_file(managed, owner=OWNER, notify_only=True)
    original = await managed.record(created["id"])
    await managed.restart()
    with pytest.raises(ValueError, match="Unknown"):
        await managed.manager.resume_owned("g2", original.id, owner=OWNER)
    for action in (managed.manager.cancel, managed.manager.status):
        with pytest.raises(ValueError, match="Unknown"):
            await action("g2", original.id)
    assert await managed.manager.store.get("g2", original.cwd, original.id) is None
    assert await managed.manager.store.get("g1", managed.orch.get_group_workspace("g2"), original.id) is None
    assert await managed.manager.list("g2") == ()
    assert await managed.record(original.id) == original
    assert original.id not in managed.manager._files
    assert not managed.model.calls


@pytest.mark.parametrize("changed_policy", [{"owner": "different-goal"}, {"notify_only": False}])
async def test_existing_name_and_stale_store_record_cannot_change_owner_policy(managed, changed_policy):
    created, _ = await create_file(managed, owner=OWNER, notify_only=True)
    original = await managed.record(created["id"])
    policy = {"owner": OWNER, "notify_only": True, **changed_policy}
    with pytest.raises(ValueError, match="different content"):
        await managed.manager.create("g1", "owned-file", file_source(), "reconcile task", **policy)
    with pytest.raises(ValueError, match="different content"):
        await managed.manager.store.create("g1", original.cwd, original.name, original.definition,
                                           recovery="ephemeral", **policy)
    stale = replace(original, **changed_policy)
    with pytest.raises(ObserverStopped, match="stopped or changed"):
        await managed.manager.store.save(stale, {"cursor": "must not commit"})
    assert await managed.record(original.id) == original


@pytest.mark.parametrize("policy", [
    {"owner": None}, {"owner": 1}, {"owner": " "}, {"owner": "中" * 86},
    {"notify_only": 0}, {"notify_only": 1}, {"notify_only": "true"}, {"notify_only": None},
])
async def test_host_owner_and_notify_only_are_strictly_typed(managed, policy):
    with pytest.raises(ValueError, match="owner and notify_only"):
        await managed.manager.create("g1", "invalid-policy", at(), "report", **policy)
    assert await managed.manager.list("g1") == ()
    assert not managed.model.calls


@pytest.mark.parametrize("notify_only", [False, True])
async def test_host_notify_policy_not_forged_provenance_controls_real_acp_delivery(managed, notify_only):
    created, _ = await create_file(managed, owner=OWNER, notify_only=notify_only)
    record = await managed.record(created["id"])
    # An attacker-controlled field says the opposite of the trusted host policy.
    message = SessionInput("forged-flags", "acp", "Eyes", "reconcile task", (), {
        "notify_only": not notify_only, "owner": "other-goal", "group_id": "g2", "managed": True,
    })
    await managed.manager.store.save(record, {"cursor": {"test": "host-policy"}}, (message,))
    client = managed.manager._handlers[record.id].client
    await client.enqueue({"session_id": "group:g1", "cwd": str(record.cwd), "message_id": message.id,
                          "source": message.source, "instruction": message.instruction,
                          "evidence": [], "provenance": message.provenance})
    status = await settled(managed, record.id)
    row, = status["occurrences"]
    receipt = await managed.server.backend.inputs.store.get(InputScope("g1", record.cwd), message.id)
    assert receipt is not None and receipt.envelope.message == message and receipt.status == "succeeded"
    assert row["delivery_status"] == ("not-required" if notify_only else "delivered")
    assert managed.model.calls == ["reconcile task"]
    assert await managed.manager.store.occurrence(receipt) is not None
    assert await managed.manager.list("g2") == ()
    if notify_only:
        managed.channel.send_message.assert_not_awaited()
    else:
        managed.channel.send_message.assert_awaited_once_with("g1", "Scheduled report")


async def test_tool_arguments_cannot_opt_into_host_owner_or_notify_only(managed):
    import json

    text = await managed.tool("create", name="not-host-owned", source=at(), instruction="report",
                              owner=OWNER, notify_only=True)
    assert not text.startswith("Error:"), text
    status = json.loads(text)
    record = await managed.record(status["id"])
    assert record.owner == "" and record.notify_only is False


async def test_owned_cursor_and_outbox_commit_roll_back_together(managed):
    created, _ = await create_file(managed, owner=OWNER, notify_only=True)
    record = await managed.record(created["id"])
    message = SessionInput("atomic-obligation", "acp", "Eyes", "reconcile task", (), {})
    await managed.manager.store._disk(lambda db: db.execute(
        "CREATE TRIGGER refuse_goal_cursor BEFORE UPDATE ON observers "
        "BEGIN SELECT RAISE(ABORT, 'goal cursor disk failure'); END"))
    try:
        with pytest.raises(sqlite3.IntegrityError, match="goal cursor disk failure"):
            await managed.manager.store.save(record, {"cursor": "new", "pending": {"id": message.id}}, (message,))
    finally:
        await managed.manager.store._disk(lambda db: db.execute("DROP TRIGGER refuse_goal_cursor"))
    assert await managed.record(record.id) == record
    assert await managed.manager.store.message(record, message.id) is None
    assert (await managed.manager.status("g1", record.id))["occurrences"] == ()
    assert not managed.model.calls


async def saved_pending_before_intake(harness, monkeypatch, *, notify_only=True):
    """Crash checkpoint after the real file handler's atomic outbox save."""
    created, path = await create_file(harness, owner=OWNER, notify_only=notify_only)
    reached = asyncio.Event()

    async def before_intake(record, definition, delivery):
        reached.set()
        await asyncio.Future()

    with monkeypatch.context() as patch:
        patch.setattr(harness.manager, "_deliver", before_intake)
        path.write_text('{"revision":2}')
        await asyncio.wait_for(reached.wait(), 3)
        before = await harness.record(created["id"])
        pending = before.state["pending"]
        assert pending is not None
        canonical = await harness.manager.store.message(before, pending["id"])
        assert canonical is not None
        assert await harness.server.backend.inputs.store.get(InputScope("g1", before.cwd), pending["id"]) is None
        assert not harness.model.calls
        await harness.restart()
    assert before.id not in harness.manager._files
    assert (await harness.record(before.id)).state == before.state
    return before, path, pending, canonical


async def pending_cleared(harness, identifier):
    async def check():
        record = await harness.record(identifier)
        return record if record.state.get("pending") is None else None
    return await harness.until(check)


async def test_owned_file_pending_before_intake_survives_explicit_restore_and_next_change(managed, monkeypatch):
    before, path, pending, canonical = await saved_pending_before_intake(managed, monkeypatch)
    identifier, message_id = before.id, pending["id"]
    await managed.manager.resume_owned("g1", identifier, owner=OWNER)
    await watching(managed, identifier)

    async def delivered_original():
        receipt = await managed.server.backend.inputs.store.get(InputScope("g1", before.cwd), message_id)
        return receipt if receipt is not None and receipt.status == "succeeded" else None

    try:
        receipt = await managed.until(delivered_original)
    except TimeoutError:
        current = await managed.record(identifier)
        receipt = await managed.server.backend.inputs.store.get(InputScope("g1", before.cwd), message_id)
        pytest.fail(
            f"Saved obligation was not recovered: message_id={message_id}, "
            f"pending_preserved={current.state.get('pending') == pending}, "
            f"inbox_receipt={receipt}, model_calls={managed.model.calls}", pytrace=False,
        )
    assert receipt.envelope.message == canonical
    assert managed.model.calls == ["reconcile task"]
    status = await settled(managed, identifier)
    assert [row["message_id"] for row in status["occurrences"]] == [message_id]
    recovered = await pending_cleared(managed, identifier)
    assert (recovered.id, recovered.owner, recovered.revision, recovered.inbox_id) == (
        before.id, OWNER, before.revision, before.inbox_id,
    )
    # A recovered obligation must not leave an unfinished occurrence fencing
    # all subsequent file saves. A genuinely new change is new work, not replay.
    path.write_text('{"revision":3}')
    second = await settled(managed, identifier, count=2)
    identities = {row["message_id"] for row in second["occurrences"]}
    assert len(identities) == 2 and message_id in identities
    assert all(row["delivery_status"] == "not-required" for row in second["occurrences"])
    assert managed.model.calls == ["reconcile task", "reconcile task"]
    managed.channel.send_message.assert_not_awaited()


async def test_owned_file_explicit_restore_fresh_baselines_then_accepts_live_change(managed):
    # Owned-file restoration is not historical file replay. The Goal host must
    # reread reports at startup; this observer starts a fresh collection baseline.
    created, path = await create_file(managed, owner=OWNER, notify_only=True)
    await managed.manager.close()
    path.write_text('{"revision":2}')
    await managed.restart()
    await managed.manager.resume_owned("g1", created["id"], owner=OWNER)
    await watching(managed, created["id"])
    assert not managed.model.calls
    assert (await managed.manager.status("g1", created["id"]))["occurrences"] == ()
    path.write_text('{"revision":3}')
    status = await settled(managed, created["id"])
    row, = status["occurrences"]
    record = await managed.record(created["id"])
    message = await managed.manager.store.message(record, row["message_id"])
    evidence, = message.evidence[0]["evidence"]
    assert evidence["before"]["digest"] == hashlib.sha256(b'{"revision":2}').hexdigest()
    assert evidence["after"]["digest"] == hashlib.sha256(b'{"revision":3}').hexdigest()
    assert managed.model.calls == ["reconcile task"]
    managed.channel.send_message.assert_not_awaited()


@pytest.fixture
async def owned_store(tmp_path):
    """Separate real leases for input facts and capability state."""
    core = await InputStore.open(tmp_path / 'owned-core' / 'inputs.sqlite3')
    await core.bind_scope(InputScope('g1', tmp_path))
    journal = await ObserverJournal.open(tmp_path / 'owned-observers' / 'state.sqlite3', core)
    try:
        yield journal, core, tmp_path
    finally:
        await journal.close()
        await core.close()


@pytest.mark.parametrize("policy", [
    {"owner": None}, {"owner": 1}, {"owner": " "}, {"owner": "中" * 86},
    {"notify_only": 0}, {"notify_only": 1}, {"notify_only": "true"}, {"notify_only": None},
])
async def test_store_owner_and_notify_only_are_strictly_typed(owned_store, policy):
    store, _, cwd = owned_store
    with pytest.raises(ValueError, match="owner and notify_only"):
        await store.create("g1", cwd, "invalid-policy", {}, **policy)
    assert await store.list() == ()


async def test_store_refuses_missing_policy_schema_without_migration(owned_store):
    store, core, cwd = owned_store
    await store.create('g1', cwd, 'ordinary-file', {}, recovery='ephemeral')
    await store._disk(lambda db: (db.execute('ALTER TABLE observers DROP COLUMN owner'),
                                 db.execute('ALTER TABLE observers DROP COLUMN notify_only')))
    await store.close()
    original = store.path.read_bytes()
    with pytest.raises(ValueError, match='Unsupported'):
        await ObserverJournal.open(store.path, core)
    assert store.path.read_bytes() == original


async def test_store_owned_policy_and_cursor_pending_survive_receiver_reopen(owned_store):
    store, inbox, cwd = owned_store
    original = await store.create("g1", cwd, "goal-file", {}, owner=OWNER, notify_only=True,
                                   recovery="ephemeral")
    message = SessionInput("saved-obligation", "acp", "Eyes", "reconcile task", (), {})
    checkpoint = {"cursor": {"revision": 7}, "pending": {"id": message.id, "data": "immutable evidence"}}
    await store.save(original, checkpoint, (message,))
    with pytest.raises(ValueError, match="unfinished"):
        await store.save(original, {"cursor": {"revision": 7}, "pending": None})
    assert await inbox.get(InputScope("g1", cwd), message.id) is None
    await store.close()
    await inbox.close()
    reopened = await InputStore.open(inbox.path)
    try:
        restored_store = await ObserverJournal.open(store.path, reopened)
        restored = await restored_store.get("g1", cwd, original.id)
        assert restored == replace(original, state=checkpoint)
        assert restored.owner == OWNER and restored.notify_only is True
        assert await restored_store.message(restored, message.id) == message
        assert await restored_store.get("g2", cwd, original.id) is None
        assert await restored_store.get("g1", cwd / "different", original.id) is None
        assert await reopened.get(InputScope("g1", cwd), message.id) is None
    finally:
        await restored_store.close()
        await reopened.close()


@pytest.mark.parametrize("forged", [
    {"owner": "wrong-goal"}, {"notify_only": False}, {"revision": 2},
])
async def test_store_fences_stale_owner_policy_and_revision_before_outbox_commit(owned_store, forged):
    store, _, cwd = owned_store
    original = await store.create("g1", cwd, "goal-file", {}, owner=OWNER, notify_only=True,
                                   recovery="ephemeral")
    message = SessionInput("do-not-commit", "acp", "Eyes", "reconcile task", (), {})
    with pytest.raises(ObserverStopped, match="stopped or changed"):
        await store.save(replace(original, **forged), {"pending": {"id": message.id}}, (message,))
    assert await store.get("g1", cwd, original.id) == original
    assert await store.message(original, message.id) is None


@pytest.mark.parametrize("started", [False, True])
async def test_store_recovery_requeues_only_unstarted_owned_execution(owned_store, started):
    store, inbox, cwd = owned_store
    observer = await store.create("g1", cwd, "goal-file", {}, owner=OWNER, notify_only=True,
                                   recovery="ephemeral")
    message = SessionInput("owned-execution", "acp", "Eyes", "reconcile task", (), {})
    checkpoint = {"cursor": {"revision": 3}, "pending": {"id": message.id}}
    await store.save(observer, checkpoint, (message,))
    await inbox.enqueue(external_envelope("g1", cwd, message))
    claimed = await inbox.claim(InputScope("g1", cwd))
    assert claimed is not None
    if started:
        await inbox.start(claimed)
    await store.sync(observer)
    await store.close()
    await inbox.close()
    reopened = await InputStore.open(inbox.path)
    try:
        restored_store = await ObserverJournal.open(store.path, reopened)
        await restored_store.sync(observer)
        receipt = await reopened.get(InputScope("g1", cwd), message.id)
        assert receipt.status == ("interrupted" if started else "pending")
        restored = await restored_store.get("g1", cwd, observer.id)
        assert restored.owner == OWNER and restored.notify_only is True
        assert restored.state == checkpoint
        assert restored.enabled is (not started)
        assert restored.recovery == ("needs_review" if started else "ephemeral")
        if started:
            assert await reopened.claim(InputScope("g1", cwd)) is None
        else:
            retried = await reopened.claim(InputScope("g1", cwd))
            assert retried.envelope.message == message and retried.attempt_id != claimed.attempt_id
    finally:
        await restored_store.close()
        await reopened.close()


async def test_owned_file_execution_commit_before_silent_delivery_never_replays_model(managed, monkeypatch):
    reached = asyncio.Event()

    async def before_report(record):
        reached.set()
        await asyncio.Future()

    created, path = await create_file(managed, owner=OWNER, notify_only=True)
    with monkeypatch.context() as patch:
        patch.setattr(managed.manager, "_report", before_report)
        path.write_text('{"revision":2}')
        await asyncio.wait_for(reached.wait(), 3)
        record = await managed.record(created["id"])
        message_id = record.state["pending"]["id"]
        receipt = await managed.server.backend.inputs.store.get(InputScope("g1", record.cwd), message_id)
        assert receipt.status == "succeeded"
        row, = (await managed.manager.status("g1", record.id))["occurrences"]
        assert row["started"] == 1 and row["delivery_status"] == "pending"
        await managed.restart()
    status = await settled(managed, record.id)
    row, = status["occurrences"]
    assert row["message_id"] == message_id and row["delivery_status"] == "not-required"
    await managed.manager.resume_owned("g1", record.id, owner=OWNER)
    await watching(managed, record.id)
    await managed.flush()
    assert managed.model.calls == ["reconcile task"]
    assert len(await managed.server.backend.inputs.store.records(InputScope("g1", record.cwd))) == 1
    managed.channel.send_message.assert_not_awaited()


async def test_owned_file_interrupted_model_stays_needs_review_after_explicit_resume(managed):
    entered, release = asyncio.Event(), asyncio.Event()
    managed.model.gates["reconcile task"] = entered, release
    created, path = await create_file(managed, owner=OWNER, notify_only=True)
    path.write_text('{"revision":2}')
    await asyncio.wait_for(entered.wait(), 3)
    record = await managed.record(created["id"])
    message_id = record.state["pending"]["id"]
    await managed.restart(application=True)
    status = await managed.manager.resume_owned("g1", record.id, owner=OWNER)
    assert status["owner"] == OWNER and status["notify_only"] is True
    assert not status["enabled"] and status["state"] == "needs_review"
    row, = status["occurrences"]
    assert row["message_id"] == message_id and row["execution_status"] == "interrupted"
    assert record.id not in managed.manager._files
    release.set()
    await managed.flush()
    assert managed.model.calls == ["reconcile task"]
    managed.channel.send_message.assert_not_awaited()


@pytest.mark.parametrize("notify_only", [False, True])
async def test_owned_scheduler_policy_uses_real_inbox_but_cannot_resume_as_file(managed, notify_only):
    created = await managed.manager.create("g1", "owned-timer", at(), "reconcile task", owner=OWNER,
                                           notify_only=notify_only)
    before = await managed.record(created["id"])
    with pytest.raises(ValueError, match="Only explicitly owned file"):
        await managed.manager.resume_owned("g1", before.id, owner=OWNER)
    managed.clock.advance(240)
    status = await managed.completed(before.id)
    row, = status["occurrences"]
    receipt = await managed.server.backend.inputs.store.get(InputScope("g1", before.cwd), row["message_id"])
    assert receipt is not None and receipt.status == "succeeded"
    assert row["delivery_status"] == ("not-required" if notify_only else "delivered")
    assert status["owner"] == OWNER and status["notify_only"] is notify_only
    assert managed.model.calls == ["reconcile task"]
    if notify_only:
        managed.channel.send_message.assert_not_awaited()
    else:
        managed.channel.send_message.assert_awaited_once_with("g1", "Scheduled report")


@pytest.mark.parametrize("wrong_owner", ["g-other", ""])
async def test_same_group_wrong_owner_cannot_claim_file_or_change_saved_policy(managed, wrong_owner):
    created, _ = await create_file(managed, owner=OWNER, notify_only=True)
    before = await managed.record(created["id"])
    await managed.restart()
    with pytest.raises(ValueError, match="Unknown owned observer"):
        await managed.manager.resume_owned("g1", before.id, owner=wrong_owner)
    assert await managed.record(before.id) == before
    assert before.id not in managed.manager._files and before.id not in managed.manager._tasks
    assert not managed.model.calls
    managed.channel.send_message.assert_not_awaited()


async def test_resume_requires_explicit_keyword_owner_not_just_group_and_id(managed):
    created, _ = await create_file(managed, owner=OWNER, notify_only=True)
    await managed.restart()
    with pytest.raises(TypeError):
        await managed.manager.resume_owned("g1", created["id"])
    with pytest.raises(TypeError):
        await managed.manager.resume_owned("g1", created["id"], OWNER)
    assert created["id"] not in managed.manager._files
    assert not managed.model.calls


async def review_required(harness, identifier):
    async def check():
        record = await harness.record(identifier)
        return record if not record.enabled and record.recovery == "needs_review" else None
    return await harness.until(check)


async def test_owned_pending_namespace_change_never_enqueues_to_new_inbox(managed, monkeypatch):
    before, _, pending, canonical = await saved_pending_before_intake(managed, monkeypatch)
    await managed.server.backend.inputs.store._disk(lambda db: db.execute(
        "UPDATE scopes SET namespace='ffffffffffffffffffffffffffffffff' WHERE group_id=? AND workspace=?",
        ("g1", str(before.cwd)),
    ))
    enqueue = AsyncMock(wraps=managed.orch.accept_external)
    monkeypatch.setattr(managed.orch, "accept_external", enqueue)
    await managed.manager.resume_owned("g1", before.id, owner=OWNER)
    current = await review_required(managed, before.id)
    enqueue.assert_not_awaited()
    assert current.state["pending"] == pending
    assert await managed.manager.store.message(before, pending["id"]) == canonical
    assert await managed.server.backend.inputs.store.get(InputScope("g1", before.cwd), pending["id"]) is None
    assert before.id not in managed.manager._files
    assert not managed.model.calls
    managed.channel.send_message.assert_not_awaited()


@pytest.mark.parametrize("corruption", ["registration", "message_id", "evidence"])
async def test_owned_pending_must_match_immutable_canonical_outbox(managed, monkeypatch, corruption):
    before, _, pending, canonical = await saved_pending_before_intake(managed, monkeypatch)
    corrupt = copy.deepcopy(pending)
    if corruption == "registration":
        corrupt["registration_id"] = "unrelated-registration"
    elif corruption == "message_id":
        corrupt["id"] = "unrelated-message"
    else:
        corrupt["events"][0]["evidence"][0]["after"]["digest"] = "f" * 64
    await managed.manager.store.save(before, {"pending": corrupt})
    enqueue = AsyncMock(wraps=managed.orch.accept_external)
    monkeypatch.setattr(managed.orch, "accept_external", enqueue)
    await managed.manager.resume_owned("g1", before.id, owner=OWNER)
    try:
        current = await review_required(managed, before.id)
    except TimeoutError:
        status = await managed.manager.status("g1", before.id)
        pytest.fail(
            f"Corrupt pending ({corruption}) was not rejected: status={status['state']}, "
            f"occurrences={status['occurrences']}, enqueue_calls={enqueue.await_count}, "
            f"model_calls={managed.model.calls}", pytrace=False,
        )
    enqueue.assert_not_awaited()
    assert current.state["pending"] == corrupt
    assert await managed.manager.store.message(before, canonical.id) == canonical
    assert await managed.server.backend.inputs.store.get(InputScope("g1", before.cwd), canonical.id) is None
    assert before.id not in managed.manager._files
    assert not managed.model.calls


async def test_owned_running_receipt_is_waited_not_reenqueued_or_reexecuted(managed, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    managed.model.gates["reconcile task"] = entered, release
    created, path = await create_file(managed, owner=OWNER, notify_only=True)
    path.write_text('{"revision":2}')
    await asyncio.wait_for(entered.wait(), 3)
    before = await managed.record(created["id"])
    message_id = before.state["pending"]["id"]
    receipt = await managed.server.backend.inputs.store.get(InputScope("g1", before.cwd), message_id)
    assert receipt.status == "running"
    # Stop collection only: the original admitted Agent turn remains alive.
    # Exercise manager recovery without rebinding an active Agent's tool context.
    await managed.manager.close()
    managed.manager = ManagedObservers(managed.orch, managed.endpoint, clocks=managed.clock.clocks())
    await managed.manager.start()
    enqueue = AsyncMock(wraps=managed.orch.accept_external)
    monkeypatch.setattr(managed.orch, "accept_external", enqueue)
    await managed.manager.resume_owned("g1", before.id, owner=OWNER)
    await managed.flush()
    enqueue.assert_not_awaited()
    assert managed.model.calls == ["reconcile task"] and managed.model.active == 1
    assert before.id not in managed.manager._files
    release.set()
    await settled(managed, before.id)
    await watching(managed, before.id)
    await pending_cleared(managed, before.id)
    enqueue.assert_not_awaited()
    assert managed.model.calls == ["reconcile task"]
    managed.channel.send_message.assert_not_awaited()


async def test_owned_unknown_channel_outcome_is_not_replayed_by_resume(managed, monkeypatch):
    created, path = await create_file(managed, owner=OWNER, notify_only=False)
    managed.channel.send_message.side_effect = TimeoutError("unknown send outcome")
    path.write_text('{"revision":2}')
    before = await review_required(managed, created["id"])
    await managed.restart()
    enqueue = AsyncMock(wraps=managed.orch.accept_external)
    monkeypatch.setattr(managed.orch, "accept_external", enqueue)
    status = await managed.manager.resume_owned("g1", before.id, owner=OWNER)
    assert not status["enabled"] and status["state"] == "needs_review"
    row, = status["occurrences"]
    assert row["execution_status"] == "succeeded" and row["delivery_status"] == "unknown"
    assert (await managed.record(before.id)).state == before.state
    enqueue.assert_not_awaited()
    assert before.id not in managed.manager._files
    assert managed.model.calls == ["reconcile task"]
    assert managed.channel.send_message.await_count == 1


async def test_cancelling_saved_unreceived_owned_pending_never_admits_it_on_resume(managed, monkeypatch):
    before, _, pending, canonical = await saved_pending_before_intake(managed, monkeypatch)
    result = await managed.manager.cancel("g1", before.id)
    assert result["not_started"] == 1
    status = await managed.manager.resume_owned("g1", before.id, owner=OWNER)
    assert not status["enabled"] and status["state"] == "cancelled"
    row, = status["occurrences"]
    assert row["message_id"] == canonical.id and row["cancelled"] == 1
    assert row["delivery_status"] == "not-required"
    assert (await managed.record(before.id)).state["pending"] == pending
    assert await managed.server.backend.inputs.store.get(InputScope("g1", before.cwd), canonical.id) is None
    assert before.id not in managed.manager._files
    assert not managed.model.calls
