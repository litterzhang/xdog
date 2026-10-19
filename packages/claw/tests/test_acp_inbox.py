"""Durable group intake is independent of a model, TUI and transport."""
from __future__ import annotations

import pytest
from xdog.agent.session_input import SessionInput
from xdog.claw.core.inputs.models import InputScope

from packages.claw.tests.core_inputs import external_envelope


def message(identifier="message-1", instruction="Inspect the change"):
    return SessionInput(identifier, "acp", "Eyes", instruction, ({"path": "example.py"},), {"route_id": "route"})


async def test_receipt_is_durable_idempotent_and_conflict_checked(tmp_path):
    from xdog.claw.core.inputs.store import InputStore

    store = await InputStore.open(tmp_path / "private" / "inbox.sqlite3")
    try:
        scope = await store.bind_scope(InputScope("main", tmp_path))
        row, duplicate = await store.enqueue(external_envelope("main", tmp_path, message()))
        assert not duplicate and row.status == "pending"
        again, duplicate = await store.enqueue(external_envelope("main", tmp_path, message()))
        assert duplicate and again == row
        with pytest.raises(ValueError, match="different"):
            await store.enqueue(external_envelope("main", tmp_path, message(instruction="Something else")))
    finally:
        await store.close()
    restored = await InputStore.open(tmp_path / "private" / "inbox.sqlite3")
    try:
        assert await restored.bind_scope(InputScope("main", tmp_path)) == scope
        rows = await restored.records(InputScope("main", tmp_path))
        assert len(rows) == 1 and rows[0].envelope.message == message()
        assert rows[0].status == "pending"
    finally:
        await restored.close()


async def test_restart_never_replays_running_input_but_keeps_pending(tmp_path):
    from xdog.claw.core.inputs.store import InputStore

    path = tmp_path / "private" / "inbox.sqlite3"
    store = await InputStore.open(path)
    await store.enqueue(external_envelope("main", tmp_path, message("one")))
    await store.enqueue(external_envelope("main", tmp_path, message("two")))
    claimed = await store.claim(InputScope("main", tmp_path))
    assert claimed.envelope.message.id == "one" and claimed.status == "claimed" and claimed.attempt_id
    claimed = await store.start(claimed)
    assert claimed.status == "running"
    await store.close()
    restored = await InputStore.open(path)
    try:
        rows = await restored.records(InputScope("main", tmp_path))
        assert [r.status for r in rows] == ["interrupted", "pending"]
        second = await restored.claim(InputScope("main", tmp_path))
        assert second.envelope.message.id == "two"
        second = await restored.start(second)
        await restored.finish(second, "succeeded", response="Handled")
        assert await restored.claim(InputScope("main", tmp_path)) is None
        assert (await restored.records(InputScope("main", tmp_path)))[1].response == "Handled"
    finally:
        await restored.close()


async def test_intake_rejects_overflow_without_dropping_and_has_one_owner(tmp_path):
    from xdog.claw.core.inputs.store import InputStore

    path = tmp_path / "private" / "inbox.sqlite3"
    store = await InputStore.open(path, max_pending=2)
    try:
        with pytest.raises(BlockingIOError):
            await InputStore.open(path)
        await store.enqueue(external_envelope("main", tmp_path, message("one")))
        await store.enqueue(external_envelope("main", tmp_path, message("two")))
        with pytest.raises(ValueError, match="capacity"):
            await store.enqueue(external_envelope("main", tmp_path, message("three")))
        _, duplicate = await store.enqueue(external_envelope("main", tmp_path, message("one")))
        assert duplicate
        assert [r.envelope.message.id for r in await store.records(InputScope("main", tmp_path))] == ["one", "two"]
        # A group may not silently retarget a persisted inbox to a new workspace.
        other = tmp_path / "other"
        other.mkdir()
        assert await store.bind_scope(InputScope("main", other)) != await store.bind_scope(InputScope("main", tmp_path))
    finally:
        await store.close()


async def test_private_store_refuses_shared_directory_and_symlink(tmp_path):
    from xdog.claw.core.inputs.store import InputStore

    shared = tmp_path / "shared"
    shared.mkdir(mode=0o755)
    with pytest.raises(ValueError, match="private"):
        await InputStore.open(shared / "inbox.sqlite3")
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    target = tmp_path / "target"
    target.write_text("do not touch")
    (private / "inbox.sqlite3").symlink_to(target)
    with pytest.raises(ValueError, match="symlink|regular"):
        await InputStore.open(private / "inbox.sqlite3")
    assert target.read_text() == "do not touch"


async def test_status_is_paged_and_selected_lookup_keeps_original_input(tmp_path):
    from xdog.claw.core.inputs.store import InputStore

    store = await InputStore.open(tmp_path / "private" / "inbox.sqlite3")
    try:
        await store.enqueue(external_envelope("main", tmp_path, message("one")))
        await store.enqueue(external_envelope("main", tmp_path, message("two")))
        page, cursor = await store.status(InputScope("main", tmp_path), limit=1)
        assert [row["message_id"] for row in page] == ["one"] and cursor == 1
        page, cursor = await store.status(InputScope("main", tmp_path), cursor=1, limit=1)
        assert [row["message_id"] for row in page] == ["two"] and cursor is None
        page, _ = await store.status(InputScope("main", tmp_path), selected="two")
        assert page[0]["status"] == "pending" and "payload" not in page[0]
        assert (await store.get(InputScope("main", tmp_path), "two")).envelope.message.id == "two"
        assert await store.get(InputScope("main", tmp_path), "missing") is None
    finally:
        await store.close()
