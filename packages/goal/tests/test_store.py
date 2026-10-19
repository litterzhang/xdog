"""Goal owns real durable snapshots/history/outbox, independently from a host."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import replace

import pytest


def original():
    from xdog.goal.models import Goal

    return Goal("goal-one", "Outcome", "Produce reviewed work", ("Result is correct",))


async def test_atomic_snapshot_history_notices_and_detached_reads(tmp_path):
    from xdog.goal.models import Notice, Owner
    from xdog.goal.store import GoalStore

    owner = Owner("opaque-host-owner", tmp_path)
    store = await GoalStore.open(tmp_path / "private" / "goals.sqlite3", owner)
    try:
        first = original()
        notice = Notice("notice-one", first.id, "Inspect worker result")
        await store.put(
            first, expected_version=0, notices=(notice,), artifacts=(("raw-report", {"evidence": ["file"]}),)
        )
        await store.put(replace(first, version=2, summary="Progress"), expected_version=1)
        assert (await store.get(first.id)).summary == "Progress"
        assert [item.version for item in await store.history(first.id)] == [1, 2]
        assert len(await store.list()) == 1 and (await store.notices())[0].notice == notice
        artifact = await store.artifact(first.id, "raw-report")
        artifact["evidence"].append("caller mutation")
        assert await store.artifact(first.id, "raw-report") == {"evidence": ["file"]}
        with pytest.raises(ValueError, match="version"):
            await store.put(replace(first, version=2, summary="stale"), expected_version=1)
        assert (await store.get(first.id)).summary == "Progress"
    finally:
        await store.close()
    reopened = await GoalStore.open(store.path, owner)
    try:
        assert (await reopened.get("goal-one")).version == 2
        assert len(await reopened.history("goal-one")) == 2
    finally:
        await reopened.close()


async def test_notice_conflict_rolls_back_the_entire_new_goal_revision(tmp_path):
    from xdog.goal.models import Notice, Owner
    from xdog.goal.store import GoalStore

    store = await GoalStore.open(tmp_path / "private" / "goals.sqlite3", Owner("owner", tmp_path))
    try:
        goal = original()
        await store.put(goal, expected_version=0, notices=(Notice("same-notice", goal.id, "original"),))
        with pytest.raises(ValueError, match="conflict"):
            await store.put(
                replace(goal, version=2, summary="must roll back"),
                expected_version=1,
                notices=(Notice("same-notice", goal.id, "different"),),
            )
        assert (await store.get(goal.id)).version == 1 and len(await store.history(goal.id)) == 1
        assert (await store.notices())[0].notice.instruction == "original"
    finally:
        await store.close()


async def test_owner_mismatch_and_live_second_writer_are_refused(tmp_path):
    from xdog.goal.models import Owner
    from xdog.goal.store import GoalStore

    path = tmp_path / "private" / "goals.sqlite3"
    store = await GoalStore.open(path, Owner("host-one:namespace", tmp_path))
    try:
        await store.put(original(), expected_version=0)
        with pytest.raises(BlockingIOError):
            await GoalStore.open(path, Owner("host-one:namespace", tmp_path))
    finally:
        await store.close()
    before = path.read_bytes()
    with pytest.raises(ValueError, match="owner"):
        await GoalStore.open(path, Owner("other-host", tmp_path))
    assert path.read_bytes() == before
    with pytest.raises(ValueError, match="owner"):
        await GoalStore.open(path, Owner("host-one:namespace", tmp_path / "other-workspace"))
    assert path.read_bytes() == before


@pytest.mark.parametrize("damage", ["version", "hash", "state"])
async def test_unsupported_or_corrupt_goal_state_is_byte_preserving(tmp_path, damage):
    from xdog.goal.models import Owner
    from xdog.goal.store import GoalStore

    path = tmp_path / "private" / "goals.sqlite3"
    owner = Owner("owner", tmp_path)
    store = await GoalStore.open(path, owner)
    await store.put(original(), expected_version=0)
    await store.close()
    with sqlite3.connect(path) as db:
        if damage == "version":
            db.execute("UPDATE metadata SET format='legacy-goals-json-v0'")
        elif damage == "hash":
            db.execute("UPDATE goals SET digest='corrupt'")
        else:
            row = db.execute("SELECT payload FROM goals").fetchone()
            data = json.loads(row[0])
            data["status"] = "invented-state"
            db.execute("UPDATE goals SET payload=?", (json.dumps(data),))
    before = path.read_bytes()
    with pytest.raises(ValueError):
        await GoalStore.open(path, owner)
    assert path.read_bytes() == before


async def test_recovery_interrupts_work_and_uncertain_notice_without_replaying(tmp_path):
    from xdog.goal.models import Attempt, Goal, Notice, Owner, Review, Task, WorkerRef
    from xdog.goal.store import GoalStore

    owner = Owner("owner", tmp_path)
    store = await GoalStore.open(tmp_path / "private" / "goals.sqlite3", owner)
    attempt = Attempt("attempt-one", state="running", assignment="work", worker=WorkerRef("child", "execution"))
    working = replace(original(), tasks=(Task("task-one", "Implement", state="running", attempts=(attempt,)),))
    reviewing = Goal(
        "goal-review",
        "Review",
        "Inspect output",
        ("Inspect actual work",),
        status="reviewing",
        review=Review(
            "review-one", state="running", fingerprint="snapshot", worker=WorkerRef("reviewer", "review-exec")
        ),
    )
    await store.put(working, expected_version=0, notices=(Notice("notice-one", working.id, "report"),))
    await store.put(reviewing, expected_version=0)
    await store.notice_started("notice-one")
    await store.close()
    store = await GoalStore.open(store.path, owner)
    try:
        saved = await store.get(working.id)
        assert saved.tasks[0].state == "interrupted" and saved.tasks[0].attempts[0].state == "interrupted"
        assert saved.tasks[0].attempts[0].worker.id == "child"
        assert (await store.get(reviewing.id)).review.state == "interrupted"
        assert (await store.get(reviewing.id)).status != "completed"
        assert (await store.notices())[0].status == "unknown"
        assert len(await store.history(working.id)) == 2
    finally:
        await store.close()


@pytest.mark.parametrize("bad", [True, 0, -1, 1.2, "1"])
def test_goal_versions_do_not_coerce_bool_or_invalid_numbers(bad):
    from xdog.goal.models import Goal

    with pytest.raises(ValueError):
        Goal("goal", "Title", "Outcome", ("Check it",), version=bad)


def test_frozen_domain_requires_real_dependencies_and_report_identity():
    from xdog.goal.models import Goal, Report, Task

    with pytest.raises(ValueError):
        Goal("goal", "Title", "Outcome", ("Check",), tasks=(Task("task", "Task", depends_on=("unknown",)),))
    with pytest.raises(ValueError):
        Goal(
            "goal",
            "Title",
            "Outcome",
            ("Check",),
            tasks=(Task("one", "First", depends_on=("two",)), Task("two", "Second", depends_on=("one",))),
        )
    with pytest.raises(ValueError):
        Report("task", "attempt", True, "submitted", "summary", ("evidence",), ())


def test_goal_codec_rejects_unknown_fields_and_mutable_tuple_coercion():
    from xdog.goal.codec import decode_goal, encode_goal

    data = json.loads(encode_goal(original()))
    data["human_authorized"] = True
    with pytest.raises(ValueError):
        decode_goal(json.dumps(data))
    from xdog.goal.models import Goal

    with pytest.raises(ValueError):
        Goal("goal", "Title", "Outcome", ["mutable criteria"])


async def test_actual_process_exit_preserves_unknown_start_and_notification_facts(tmp_path):
    import sys

    from xdog.goal.models import Owner
    from xdog.goal.store import GoalStore

    path = tmp_path / "private" / "goals.sqlite3"
    program = """
import asyncio,os,sys
from pathlib import Path
from xdog.goal.models import Owner,Goal,Task,Attempt,Notice,WorkerRef
from xdog.goal.store import GoalStore
async def main():
 s=await GoalStore.open(Path(sys.argv[1]),Owner('process-owner',Path(sys.argv[2])))
 task=Task('task','Work',state='running',attempts=(Attempt('attempt',state='running',assignment='work',
   worker=WorkerRef('actual-child','execution-one')),))
 goal=Goal('goal','Outcome','Do work',('Inspect evidence',),tasks=(task,))
 await s.put(goal,expected_version=0,notices=(Notice('notice','goal','Report arrived'),))
 await s.notice_started('notice')
 os._exit(0)
asyncio.run(main())
"""
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        program,
        str(path),
        str(tmp_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, error = await asyncio.wait_for(process.communicate(), 15)
    assert process.returncode == 0, error.decode()
    store = await GoalStore.open(path, Owner("process-owner", tmp_path))
    try:
        goal = await store.get("goal")
        assert goal.tasks[0].state == "interrupted" and goal.tasks[0].attempts[0].worker.id == "actual-child"
        assert (await store.notices())[0].status == "unknown"
        assert len(await store.history("goal")) == 2
    finally:
        await store.close()


async def test_cancelled_admitted_commit_keeps_lease_until_actual_disk_settlement(tmp_path, monkeypatch):
    import threading

    from xdog.goal.models import Notice, Owner
    from xdog.goal.store import GoalStore

    owner = Owner("owner", tmp_path)
    store = await GoalStore.open(tmp_path / "private" / "goals.sqlite3", owner)
    committed, release = threading.Event(), threading.Event()
    transaction = store._database.transaction

    def held(operation):
        result = transaction(operation)
        committed.set()
        assert release.wait(5)
        return result

    monkeypatch.setattr(store._database, "transaction", held)
    writing = asyncio.create_task(
        store.put(original(), expected_version=0, notices=(Notice("notice", "goal-one", "same atomic commit"),))
    )
    closing = None
    try:
        assert await asyncio.to_thread(committed.wait, 3)
        writing.cancel()
        closing = asyncio.create_task(store.close())
        with pytest.raises(BlockingIOError):
            await GoalStore.open(store.path, owner)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await writing
        await asyncio.wait_for(closing, 3)
    finally:
        release.set()
        await asyncio.gather(writing, *(tuple([closing]) if closing else ()), return_exceptions=True)
        await store.close()
    store = await GoalStore.open(store.path, owner)
    try:
        assert (await store.get("goal-one")).version == 1
        assert (await store.notices())[0].notice.instruction == "same atomic commit"
    finally:
        await store.close()


async def test_old_notice_ack_cannot_settle_a_new_send_attempt(tmp_path):
    from xdog.goal.models import Notice, Owner
    from xdog.goal.store import GoalStore

    store = await GoalStore.open(tmp_path / "private" / "goals.sqlite3", Owner("owner", tmp_path))
    try:
        await store.put(original(), expected_version=0, notices=(Notice("notice", "goal-one", "report"),))
        first = await store.notice_started("notice")
        await store.notice_settled("notice", "pending", "proven not accepted", send_id=first.send_id)
        second = await store.notice_started("notice")
        assert first.send_id != second.send_id
        with pytest.raises(ValueError, match="attempt"):
            await store.notice_settled("notice", "accepted", send_id=first.send_id)
        await store.notice_settled("notice", "accepted", send_id=second.send_id)
        with pytest.raises(ValueError):
            await store.notice_started("notice")
    finally:
        await store.close()


@pytest.mark.parametrize("damage", ["extra_schema", "historical_active_send"])
async def test_unknown_schema_or_inconsistent_old_send_is_not_repaired_on_open(tmp_path, damage):
    from xdog.goal.models import Notice, Owner
    from xdog.goal.store import GoalStore

    owner = Owner("owner", tmp_path)
    path = tmp_path / "private" / "goals.sqlite3"
    store = await GoalStore.open(path, owner)
    await store.put(original(), expected_version=0, notices=(Notice("notice", "goal-one", "report"),))
    first = await store.notice_started("notice")
    await store.notice_settled("notice", "pending", send_id=first.send_id)
    second = await store.notice_started("notice")
    await store.notice_settled("notice", "accepted", send_id=second.send_id)
    await store.close()
    with sqlite3.connect(path) as db:
        if damage == "extra_schema":
            db.execute("ALTER TABLE goals ADD COLUMN unsupported_policy TEXT DEFAULT ''")
        else:
            db.execute("UPDATE notice_attempts SET status='sending' WHERE send_id=?", (first.send_id,))
    before = path.read_bytes()
    with pytest.raises(ValueError):
        await GoalStore.open(path, owner)
    assert path.read_bytes() == before
