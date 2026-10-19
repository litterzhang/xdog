"""Real durable output and send-attempt facts, independent from Agent execution."""

from __future__ import annotations

import asyncio
import sqlite3
import sys

import pytest
from xdog.claw.core.inputs.models import InputScope
from xdog.claw.core.runtime.input_adapter import freeze_input
from xdog.claw.core.types import UserInput


def original(tmp_path):
    return freeze_input(
        UserInput(
            group_id="main",
            content="do work",
            sender="alice",
            channel="weixin",
            metadata={"account_id": "account", "weixin_user_id": "alice", "context_token": "original-context"},
        ),
        tmp_path,
        message_id="input-1",
    )


async def test_chunk_ids_are_stable_and_bind_original_attempt_destination_and_text(tmp_path):
    from xdog.claw.core.outputs.store import OutputStore

    path = tmp_path / "private" / "output.sqlite3"
    store = await OutputStore.open(path)
    envelope = original(tmp_path)
    try:
        first = await store.append(envelope, "execution-1", 0, "live-channel", "progress")
        assert first.status == "pending" and first.send_id == ""
        assert await store.append(envelope, "execution-1", 0, "live-channel", "progress") == first
        with pytest.raises(ValueError, match="conflict"):
            await store.append(envelope, "execution-1", 0, "live-channel", "changed")
        changed = freeze_input(
            UserInput(group_id="main", content="do work", sender="bob", channel="weixin"),
            tmp_path,
            message_id="input-1",
        )
        with pytest.raises(ValueError, match="conflict"):
            await store.append(changed, "execution-1", 0, "live-channel", "progress")
        next_chunk = await store.append(envelope, "execution-1", 1, "live-channel", "done")
        explicit_retry = await store.append(envelope, "execution-2", 0, "live-channel", "progress")
        assert len({first.id, next_chunk.id, explicit_retry.id}) == 3
        assert await store.records(InputScope("other-group", tmp_path)) == []
    finally:
        await store.close()
    store = await OutputStore.open(path)
    try:
        rows = await store.records(envelope.scope)
        assert [row.text for row in rows] == ["progress", "done", "progress"]
        assert rows[0].envelope == envelope and rows[0].id == first.id
        assert rows[0].envelope.context["metadata"]["context_token"] == "original-context"
    finally:
        await store.close()


async def test_known_not_sent_has_a_new_send_attempt_but_never_a_new_output_id(tmp_path):
    from xdog.claw.core.outputs.store import OutputStore

    store = await OutputStore.open(tmp_path / "private" / "output.sqlite3")
    try:
        row = await store.append(original(tmp_path), "execution-1", 0, "channel", "done")
        sending = await store.begin(row.id)
        with pytest.raises(ValueError):
            await store.begin(row.id)
        with pytest.raises(ValueError, match="attempt"):
            await store.settle(row.id, "wrong-send", "delivered")
        await store.settle(row.id, sending.send_id, "pending", "positively rejected")
        retry = await store.begin(row.id)
        assert retry.id == row.id and retry.send_id != sending.send_id
        with pytest.raises(ValueError, match="attempt"):
            await store.settle(row.id, sending.send_id, "delivered")
        await store.settle(row.id, retry.send_id, "delivered")
        with pytest.raises(ValueError):
            await store.begin(row.id)
        assert (await store.records())[0].status == "delivered"
    finally:
        await store.close()
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT status FROM send_attempts ORDER BY rowid").fetchall() == [
            ("not_sent",),
            ("delivered",),
        ]


async def test_pending_survives_but_sending_recovers_unknown_and_never_replays(tmp_path):
    from xdog.claw.core.outputs.store import OutputStore

    path = tmp_path / "private" / "output.sqlite3"
    store = await OutputStore.open(path)
    pending = await store.append(original(tmp_path), "execution-1", 0, "channel", "pending")
    sending = await store.append(original(tmp_path), "execution-1", 1, "channel", "uncertain")
    await store.begin(sending.id)
    await store.close()
    store = await OutputStore.open(path)
    try:
        rows = await store.records()
        assert [(row.id, row.status) for row in rows] == [(pending.id, "pending"), (sending.id, "unknown")]
        with pytest.raises(ValueError):
            await store.begin(sending.id)
        assert (await store.begin(pending.id)).status == "sending"
    finally:
        await store.close()


async def test_real_process_exit_during_send_and_live_lease_refusal(tmp_path):
    from xdog.claw.core.outputs.store import OutputStore

    path = tmp_path / "private" / "output.sqlite3"
    script = """
import asyncio, os, sys
from pathlib import Path
from xdog.claw.core.outputs.store import OutputStore
from xdog.claw.core.runtime.input_adapter import freeze_input
from xdog.claw.core.types import UserInput
async def main():
    store = await OutputStore.open(Path(sys.argv[1]))
    envelope = freeze_input(UserInput(group_id='main', content='original'), Path(sys.argv[2]), message_id='one')
    row = await store.append(envelope, 'execution', 0, 'channel', 'accepted by network or not known')
    await store.begin(row.id)
    os._exit(0)
asyncio.run(main())
"""
    child = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        script,
        str(path),
        str(tmp_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, error = await asyncio.wait_for(child.communicate(), 15)
    assert child.returncode == 0, error.decode()
    store = await OutputStore.open(path)
    try:
        assert (await store.records())[0].status == "unknown"
        with pytest.raises(BlockingIOError):
            await OutputStore.open(path)
    finally:
        await store.close()


@pytest.mark.parametrize("fault", ["format", "corrupt_state", "changed_text"])
async def test_unsupported_or_corrupt_output_journal_is_untouched(tmp_path, fault):
    from xdog.claw.core.outputs.store import OutputStore

    path = tmp_path / "private" / "output.sqlite3"
    store = await OutputStore.open(path)
    await store.append(original(tmp_path), "execution-1", 0, "channel", "done")
    await store.close()
    with sqlite3.connect(path) as db:
        if fault == "format":
            db.execute("UPDATE metadata SET format='old-output-v0'")
        elif fault == "corrupt_state":
            db.execute("UPDATE outputs SET status='sending'")
        else:
            db.execute("UPDATE outputs SET output_id='not-the-stable-identity'")
    before = path.read_bytes()
    with pytest.raises(ValueError):
        await OutputStore.open(path)
    assert path.read_bytes() == before


@pytest.mark.parametrize("ordinal", [True, -1, 1.2, "0"])
async def test_invalid_ordinals_cannot_create_unaddressable_output(tmp_path, ordinal):
    from xdog.claw.core.outputs.store import OutputStore

    store = await OutputStore.open(tmp_path / "private" / "output.sqlite3")
    try:
        with pytest.raises(ValueError):
            await store.append(original(tmp_path), "execution-1", ordinal, "channel", "done")
        assert await store.records() == []
    finally:
        await store.close()
