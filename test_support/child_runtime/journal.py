"""Write-ahead accepted child inputs survive an owned SIGKILL without implicit replay."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import subprocess
import sys
import threading

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.ai.types import AssistantMessage, ImageContent, StartEvent
from xdog.ai.utils.event_stream import EventStream

from test_support.child_runtime.processes import write_host_script

PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5ioAAAAASUVORK5CYII="


async def running(tmp_path, *, runtime_factory, child_store_type):
    entered = threading.Event()

    def stream(*args):
        async def events():
            entered.set()
            yield StartEvent(partial=AssistantMessage())
            await asyncio.sleep(60)

        return EventStream.from_async_generator(events())

    store = child_store_type(tmp_path / "records")
    owner = runtime_factory(
        lambda *args: Agent(stream, config=AgentConfig(model="offline/model")),
        store.save,
        begin=store.begin,
        persist_inputs=store.save_inputs,
    )
    child = await owner.start("INITIAL")
    assert await asyncio.to_thread(entered.wait, 3)
    return owner, store, child


class TestChildJournal:
    @pytest.mark.skipif(os.name != "posix", reason="Owned SIGKILL and directory fsync evidence")
    @pytest.mark.parametrize("mode", ["original", "edited", "image-return"])
    def test_actual_killed_child_owner_restores_uncertain_snapshot_without_restarting(
        self, tmp_path, mode, *, runtime_factory, child_store_type, child_process_imports
    ):
        script = tmp_path / "owned.py"
        write_host_script(
            script,
            "journal_1.py.txt",
            child_process_imports,
        )
        root = tmp_path / "records"
        body = base64.b64decode(PNG) + b"\0" * (2 * 1024 * 1024)
        encoded = base64.b64encode(body).decode()
        # Supply image through owned file, not multi-MiB argv/credentials/environment.
        image = tmp_path / "input.json"
        image.write_text(json.dumps(encoded))
        script.write_text(
            script.read_text().replace("data=sys.argv[2]", "data=json.loads(Path(sys.argv[2]).read_text())")
        )
        child = subprocess.Popen(
            [sys.executable, "-I", str(script), str(root), str(image), mode],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            import select

            ready, _, _ = select.select([child.stdout], [], [], 8)
            assert ready, "Owned child input acknowledgment never arrived"
            result = json.loads(child.stdout.readline())
            assert result["ack"] is True
            child.kill()
            assert child.wait(timeout=5) == -9
            store = child_store_type(root)
            records = store.load()
            assert records[0]["queue_uncertain"] is True and records[0]["status"] == "interrupted"
            if mode == "image-return":
                assert records[0]["undelivered_inputs"] == []
                assert records[0]["returned_backups"][0]["content"] == "ACKNOWLEDGED IMAGE INPUT"
                assert base64.b64decode(records[0]["returned_backups"][0]["images"][0]["data"]) == body

                async def recover_return():
                    owner = runtime_factory(
                        lambda *args: pytest.fail("Never restart returned task"), store.save, records=records
                    )
                    try:
                        actual = (await owner.control("queue", result["id"], '{"action":"list"}'))["queue"]
                        row = next(row for row in actual["inputs"] if row.get("source") == "durable-return")
                        assert row["uncertain"] is True
                        copied = await owner.control(
                            "queue",
                            result["id"],
                            json.dumps({"action": "recover_images", "id": row["id"], "revision": actual["revision"]}),
                        )
                        assert copied["image_recovery"]["images"][0]["data"] == encoded
                        assert store.load() == records and not owner.active
                    finally:
                        await owner.aclose()

                asyncio.run(recover_return())
            else:
                assert len(records[0]["undelivered_inputs"]) == 1
                assert records[0]["undelivered_inputs"][0]["content"] == (
                    "ACKNOWLEDGED EDITED IMAGE" if mode == "edited" else "ACKNOWLEDGED IMAGE INPUT"
                )
                assert "EXPLICITLY REMOVED INPUT" not in str(records)
                assert base64.b64decode(records[0]["undelivered_inputs"][0]["images"][0]["data"]) == body

                async def recover():
                    owner = runtime_factory(
                        lambda *args: pytest.fail("Never restart recovered agent"), store.save, records=records
                    )
                    try:
                        actual = (await owner.control("queue", result["id"], '{"action":"list"}'))["queue"]
                        row = next(row for row in actual["inputs"] if row.get("source") == "crash-snapshot")
                        assert row["uncertain"] is True
                        copied = await owner.control(
                            "queue",
                            result["id"],
                            json.dumps({"action": "recover_images", "id": row["id"], "revision": actual["revision"]}),
                        )
                        assert copied["image_recovery"]["images"][0]["data"] == encoded
                        assert store.load() == records and not owner.active
                    finally:
                        await owner.aclose()

                asyncio.run(recover())
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)
            child.stdout.close()
            child.stderr.close()

    async def test_failed_journal_refuses_live_acceptance_and_keeps_prior_snapshot(
        self, tmp_path, *, runtime_factory, child_store_type
    ):
        owner, store, child = await running(
            tmp_path, runtime_factory=runtime_factory, child_store_type=child_store_type
        )
        try:
            await owner.control("steer", child, "ACK FIRST")
            saved = store._path(child, ".inputs").read_bytes()

            def fail(*args):
                raise OSError("Owned journal failure")

            owner.persist_inputs = fail
            with pytest.raises(OSError, match="journal failure"):
                await owner.control("follow-up", child, "NEVER ACK")
            assert [row.content for row in owner._children[child].agent.pending_inputs()] == ["ACK FIRST"]
            assert store._path(child, ".inputs").read_bytes() == saved
            assert not owner._children[child].queue_work
        finally:
            await owner.aclose()

    async def test_cancelled_journal_waits_for_real_writer_without_enqueuing_or_stranding_child(
        self, tmp_path, *, runtime_factory, child_store_type
    ):
        owner, store, child = await running(
            tmp_path, runtime_factory=runtime_factory, child_store_type=child_store_type
        )
        entered, release = threading.Event(), threading.Event()
        original = owner.persist_inputs

        def held(*args):
            original(*args)
            entered.set()
            assert release.wait(5)

        owner.persist_inputs = held
        work = None
        try:
            work = asyncio.create_task(owner.control("steer", child, "CANCELLED WRITE"))
            assert await asyncio.to_thread(entered.wait, 3)
            work.cancel()
            await asyncio.sleep(0.02)
            assert not work.done() and owner._children[child].queue_work
            with pytest.raises(RuntimeError, match="storage still owns"):
                await owner.control("follow-up", child, "BLOCKED SECOND")
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await work
            assert not owner._children[child].agent.pending_inputs()
            await owner.control("cancel", child)
            assert not store._path(child, ".inputs").exists()
            assert not store.load()[0].get("undelivered_inputs")
        finally:
            release.set()
            if work is not None:
                await asyncio.gather(work, return_exceptions=True)
            await owner.aclose()

    def test_terminal_record_wins_over_orphan_journal_after_cleanup_interruption(self, tmp_path, *, child_store_type):
        store = child_store_type(tmp_path / "records")
        from xdog.agent.input_queue import QueuedInput

        store.save({"id": "0123456789ab", "task": "done", "access": "read-only", "status": "completed", "messages": []})
        store.save_inputs("0123456789ab", (QueuedInput("old", "ALREADY CONSUMED", "steer"),))
        loaded = store.load()[0]
        assert loaded["status"] == "completed" and not loaded.get("undelivered_inputs")
        assert not loaded.get("queue_uncertain")

    async def test_acknowledged_queue_edits_moves_and_removals_write_the_current_snapshot(
        self, tmp_path, *, runtime_factory, child_store_type
    ):
        owner, store, child = await running(
            tmp_path, runtime_factory=runtime_factory, child_store_type=child_store_type
        )

        async def change(action, identifier, **fields):
            captured = (await owner.control("queue", child, '{"action":"list"}'))["queue"]
            return await owner.control(
                "queue",
                child,
                json.dumps({"action": action, "id": identifier, "revision": captured["revision"], **fields}),
            )

        def saved():
            return json.loads(store._path(child, ".inputs").read_text())["inputs"]

        try:
            await owner.control("steer", child, "FIRST", images=(ImageContent(data=PNG, mime_type="image/png"),))
            await owner.control("steer", child, "SECOND")
            rows = owner._children[child].agent.pending_inputs()
            first, second = rows[0].id, rows[1].id
            await change("edit", first, content="EDITED PHOTO")
            assert saved()[0]["content"] == "EDITED PHOTO" and saved()[0]["images"][0]["data"] == PNG
            await change("move", second, direction=-1)
            assert [row["id"] for row in saved()] == [second, first]
            returned = await change("return", second)
            assert returned["queue"]["returned"]["content"] == "SECOND"
            assert [row["id"] for row in saved()] == [first]
            await change("remove", first)
            assert saved() == [] and not owner._children[child].agent.pending_inputs()
        finally:
            await owner.aclose()

    @pytest.mark.parametrize("action", ["edit", "remove", "move", "return"])
    async def test_failed_queue_change_journal_does_not_mutate_live_input_or_prior_disk_state(
        self, tmp_path, action, *, runtime_factory, child_store_type
    ):
        owner, store, child = await running(
            tmp_path, runtime_factory=runtime_factory, child_store_type=child_store_type
        )
        try:
            await owner.control("steer", child, "KEEP FIRST")
            await owner.control("steer", child, "KEEP SECOND")
            captured = (await owner.control("queue", child, '{"action":"list"}'))["queue"]
            before = store._path(child, ".inputs").read_bytes()

            def fail(*args):
                raise OSError("Owned prospective write failed")

            owner.persist_inputs = fail
            fields = {"content": "replacement"} if action == "edit" else {"direction": 1} if action == "move" else {}
            with pytest.raises(OSError, match="prospective write"):
                await owner.control(
                    "queue",
                    child,
                    json.dumps(
                        {
                            "action": action,
                            "id": captured["inputs"][0]["id"],
                            "revision": captured["revision"],
                            **fields,
                        }
                    ),
                )
            assert (await owner.control("queue", child, '{"action":"list"}'))["queue"] == captured
            assert store._path(child, ".inputs").read_bytes() == before
        finally:
            await owner.aclose()

    async def test_consumption_during_queue_change_write_refuses_stale_live_mutation(
        self, tmp_path, *, runtime_factory, child_store_type
    ):
        owner, store, child = await running(
            tmp_path, runtime_factory=runtime_factory, child_store_type=child_store_type
        )
        entered, release = threading.Event(), threading.Event()
        original = owner.persist_inputs

        def held(*args):
            original(*args)
            entered.set()
            assert release.wait(5)

        task = None
        try:
            await owner.control("steer", child, "CONSUMPTION RACE")
            captured = (await owner.control("queue", child, '{"action":"list"}'))["queue"]
            owner.persist_inputs = held
            task = asyncio.create_task(
                owner.control(
                    "queue",
                    child,
                    json.dumps(
                        {
                            "action": "edit",
                            "id": captured["inputs"][0]["id"],
                            "revision": captured["revision"],
                            "content": "NEVER ADMITTED EDIT",
                        }
                    ),
                )
            )
            assert await asyncio.to_thread(entered.wait, 3)

            async def select():
                owner._children[child].agent._dequeue_steering()

            await owner._bridge(select())
            release.set()
            with pytest.raises(ValueError, match="changed"):
                await task
            row = owner._children[child].agent.pending_inputs()[0]
            assert row.selected and row.content == "CONSUMPTION RACE"
        finally:
            release.set()
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)
            await owner.aclose()

    async def test_cancelled_prospective_edit_settles_writer_and_never_acknowledges_live_change(
        self, tmp_path, *, runtime_factory, child_store_type
    ):
        owner, store, child = await running(
            tmp_path, runtime_factory=runtime_factory, child_store_type=child_store_type
        )
        entered, release = threading.Event(), threading.Event()
        work = None
        try:
            await owner.control("steer", child, "OLD LIVE TEXT")
            captured = (await owner.control("queue", child, '{"action":"list"}'))["queue"]
            original = owner.persist_inputs

            def held(*args):
                original(*args)
                entered.set()
                assert release.wait(5)

            owner.persist_inputs = held
            work = asyncio.create_task(
                owner.control(
                    "queue",
                    child,
                    json.dumps(
                        {
                            "action": "edit",
                            "id": captured["inputs"][0]["id"],
                            "revision": captured["revision"],
                            "content": "PROSPECTIVE UNCERTAIN TEXT",
                        }
                    ),
                )
            )
            assert await asyncio.to_thread(entered.wait, 3)
            work.cancel()
            await asyncio.sleep(0.02)
            work.cancel()
            await asyncio.sleep(0.02)
            assert not work.done() and owner._children[child].queue_work
            assert owner._children[child].agent.pending_inputs()[0].content == "OLD LIVE TEXT"
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await work
            assert owner._children[child].agent.pending_inputs()[0].content == "OLD LIVE TEXT"
            assert not owner._children[child].queue_work
            await owner.control("cancel", child)
            # Terminal record supersedes the prospective journal with actual final state.
            assert store.load()[0]["undelivered_inputs"][0]["content"] == "OLD LIVE TEXT"
            assert not store._path(child, ".inputs").exists()
        finally:
            release.set()
            if work is not None:
                await asyncio.gather(work, return_exceptions=True)
            await owner.aclose()

    @pytest.mark.parametrize("fault", ["none", "write", "cancel", "selected", "revoked"])
    async def test_live_image_return_commit_journals_before_removal_and_rechecks_lease(
        self, tmp_path, fault, *, runtime_factory, child_store_type
    ):
        owner, store, child = await running(
            tmp_path, runtime_factory=runtime_factory, child_store_type=child_store_type
        )
        entered, release = threading.Event(), threading.Event()
        work = None
        alive = True

        async def control(command):
            return await owner.control(
                "queue", child, json.dumps(command), return_owner="owned-return", return_alive=lambda: alive
            )

        try:
            await owner.control("steer", child, "RETURN PHOTO", images=(ImageContent(data=PNG, mime_type="image/png"),))
            before = (await owner.control("queue", child, '{"action":"list"}'))["queue"]
            row = before["inputs"][0]
            prepared = await control({"action": "return_prepare", "id": row["id"], "revision": before["revision"]})
            command = {
                "action": "return_commit",
                "id": row["id"],
                "revision": before["revision"],
                "token": prepared["queue"]["return_token"],
                "receipt": prepared["queue"]["image_receipt"],
            }
            original = owner.persist_inputs
            prior = store._path(child, ".inputs").read_bytes()
            writes = []

            def persist(*args):
                writes.append(args)
                if fault == "write":
                    raise OSError("Owned return journal failure")
                original(*args)
                if fault in {"cancel", "selected", "revoked"}:
                    entered.set()
                    assert release.wait(5)

            owner.persist_inputs = persist
            work = asyncio.create_task(control(command))
            if fault in {"cancel", "selected", "revoked"}:
                assert await asyncio.to_thread(entered.wait, 3)
                assert owner._children[child].queue_work
                assert len(owner._children[child].agent.pending_inputs()) == 1
                if fault == "cancel":
                    work.cancel()
                    await asyncio.sleep(0.02)
                    assert not work.done()
                elif fault == "selected":

                    async def select():
                        owner._children[child].agent._dequeue_steering()

                    await owner._bridge(select())
                else:
                    alive = False
                release.set()
            if fault == "none":
                committed = await work
                assert committed["queue"]["returned"] == row
                assert json.loads(store._path(child, ".inputs").read_text())["inputs"] == []
                assert not owner._children[child].agent.pending_inputs()
                assert await control(command) == committed
                assert len(writes) == 1  # Exact retry does not rewrite the durable snapshot.
            else:
                expected = OSError if fault == "write" else asyncio.CancelledError if fault == "cancel" else ValueError
                with pytest.raises(expected):
                    await work
                assert owner._children[child].agent.pending_inputs()[0].id == row["id"]
                if fault == "write":
                    assert store._path(child, ".inputs").read_bytes() == prior
            assert not owner._children[child].queue_work
        finally:
            release.set()
            if work is not None:
                await asyncio.gather(work, return_exceptions=True)
            await owner.aclose()

    @pytest.mark.parametrize("fails", ["none", "backup", "queue"])
    async def test_durable_return_handoff_failure_and_explicit_discard_preserve_exact_bytes(
        self, tmp_path, fails, *, runtime_factory, child_store_type
    ):
        owner, store, child = await running(
            tmp_path, runtime_factory=runtime_factory, child_store_type=child_store_type
        )
        owner.persist_return = store.save_return
        try:
            await owner.control(
                "steer", child, "DURABLE RETURN COPY", images=(ImageContent(data=PNG, mime_type="image/png"),)
            )
            snapshot = (await owner.control("queue", child, '{"action":"list"}'))["queue"]
            row = snapshot["inputs"][0]
            prepared = await owner.control(
                "queue",
                child,
                json.dumps({"action": "return_prepare", "id": row["id"], "revision": snapshot["revision"]}),
                return_owner="handoff",
            )
            if fails == "backup":

                def fail(*args):
                    raise OSError("Owned backup failure")

                owner.persist_return = fail
            elif fails == "queue":

                def fail(*args):
                    raise OSError("Owned queue write failure after backup")

                owner.persist_inputs = fail
            command = {
                "action": "return_commit",
                "id": row["id"],
                "revision": snapshot["revision"],
                "token": prepared["queue"]["return_token"],
                "receipt": prepared["queue"]["image_receipt"],
            }
            if fails != "none":
                with pytest.raises(OSError):
                    await owner.control("queue", child, json.dumps(command), return_owner="handoff")
                assert len(owner._children[child].agent.pending_inputs()) == 1
            else:
                await owner.control("queue", child, json.dumps(command), return_owner="handoff")
                assert not owner._children[child].agent.pending_inputs()
            await owner.control("cancel", child)
            saved = store.load()[0]
            assert saved["returned_backups"][0]["content"] == "DURABLE RETURN COPY"
            assert saved["returned_backups"][0]["images"][0]["data"] == PNG
            assert not store._path(child, ".returns").exists()
            actual = (await owner.control("queue", child, '{"action":"list"}'))["queue"]
            backup = next(row for row in actual["inputs"] if row.get("source") == "durable-return")
            assert backup["uncertain"] is True
            before = store.load()[0]
            copied = await owner.control(
                "queue",
                child,
                json.dumps({"action": "recover_images", "id": backup["id"], "revision": actual["revision"]}),
            )
            assert copied["image_recovery"]["images"][0]["data"] == PNG and store.load()[0] == before
            removed = await owner.control(
                "queue", child, json.dumps({"action": "remove", "id": backup["id"], "revision": actual["revision"]})
            )
            assert not any(row.get("source") == "durable-return" for row in removed["queue"]["inputs"])
            assert store.load()[0]["returned_backups"] == []
        finally:
            await owner.aclose()

    @pytest.mark.parametrize("first_failure", ["backup", "queue"])
    async def test_return_retry_after_partial_write_reuses_exact_backup_without_duplicate_rows(
        self, tmp_path, first_failure, *, runtime_factory, child_store_type
    ):
        owner, store, child = await running(
            tmp_path, runtime_factory=runtime_factory, child_store_type=child_store_type
        )
        original_inputs = owner.persist_inputs
        attempts = []

        def backup(identifier, row):
            attempts.append(("backup", row.id))
            store.save_return(identifier, row)
            if first_failure == "backup" and len(attempts) == 1:
                raise OSError("Owned failure after actual backup replacement")

        def inputs(identifier, rows):
            attempts.append(("queue", identifier))
            if first_failure == "queue" and len(attempts) == 2:
                raise OSError("Owned queue failure after backup")
            original_inputs(identifier, rows)

        try:
            await owner.control(
                "steer", child, "RETRY EXACT IMAGE", images=(ImageContent(data=PNG, mime_type="image/png"),)
            )
            owner.persist_return = backup
            owner.persist_inputs = inputs
            before = (await owner.control("queue", child, '{"action":"list"}'))["queue"]
            row = before["inputs"][0]
            prepared = await owner.control(
                "queue",
                child,
                json.dumps({"action": "return_prepare", "id": row["id"], "revision": before["revision"]}),
                return_owner="retry-owner",
            )
            command = {
                "action": "return_commit",
                "id": row["id"],
                "revision": before["revision"],
                "token": prepared["queue"]["return_token"],
                "receipt": prepared["queue"]["image_receipt"],
            }
            with pytest.raises(OSError):
                await owner.control("queue", child, json.dumps(command), return_owner="retry-owner")
            assert len(owner._children[child].agent.pending_inputs()) == 1
            assert len(owner._children[child].returned_backups) == 1
            committed = await owner.control("queue", child, json.dumps(command), return_owner="retry-owner")
            assert committed["queue"]["returned"] == row and not committed["queue"]["inputs"]
            assert len(owner._children[child].returned_backups) == 1
            disk = json.loads(store._path(child, ".returns").read_text())
            assert len(disk["inputs"]) == 1 and disk["inputs"][0]["images"][0]["data"] == PNG
            writes_before = list(attempts)
            assert await owner.control("queue", child, json.dumps(command), return_owner="retry-owner") == committed
            assert attempts == writes_before
            await owner.control("cancel", child)
            assert len(store.load()[0]["returned_backups"]) == 1
        finally:
            await owner.aclose()

    async def test_child_consumption_flush_blocks_next_request_and_removes_consumed_snapshot(
        self, tmp_path, *, runtime_factory, child_store_type
    ):
        first_entered, release_first = threading.Event(), threading.Event()
        flush_entered, release_flush = threading.Event(), threading.Event()
        calls = []

        def stream(model, context, options):
            calls.append(context)
            from xdog.ai.types import TextContent

            message = AssistantMessage(content=(TextContent(text="DONE"),))
            result = asyncio.get_running_loop().create_future()
            result.set_result(message)

            async def events():
                if len(calls) == 1:
                    first_entered.set()
                    while not release_first.is_set():
                        await asyncio.sleep(0.003)
                from xdog.ai.types import TextContent

                message = AssistantMessage(content=(TextContent(text="DONE"),))
                from xdog.ai.types import DoneEvent

                yield DoneEvent(message=message)

            return EventStream.from_async_generator(events(), result_future=result)

        store = child_store_type(tmp_path / "records")
        original = store.save_inputs

        def persist(identifier, rows):
            if not rows:
                flush_entered.set()
                assert release_flush.wait(5)
            original(identifier, rows)

        owner = runtime_factory(
            lambda *args: Agent(stream, config=AgentConfig(model="offline/model")),
            store.save,
            begin=store.begin,
            persist_inputs=persist,
        )
        try:
            child = await owner.start("INITIAL")
            assert await asyncio.to_thread(first_entered.wait, 3)
            await owner.control("follow-up", child, "CONSUMED INPUT")
            release_first.set()
            assert await asyncio.to_thread(flush_entered.wait, 3)
            assert len(calls) == 1 and owner._children[child].queue_work
            assert not owner._children[child].agent.pending_inputs()
            assert json.loads(store._path(child, ".inputs").read_text())["inputs"][0]["content"] == "CONSUMED INPUT"
            release_flush.set()
            result = await owner.control("wait", child)
            assert result["status"] == "completed" and len(calls) == 2
            assert not store.load()[0].get("undelivered_inputs")
        finally:
            release_first.set()
            release_flush.set()
            await owner.aclose()

    @pytest.mark.skipif(os.name != "posix", reason="Owned SIGKILL after awaited consumption flush")
    def test_kill_after_next_model_entry_does_not_restore_consumed_queue_input(
        self, tmp_path, *, child_store_type, child_process_imports
    ):
        script = tmp_path / "consumed.py"
        write_host_script(
            script,
            "journal_2.py.txt",
            child_process_imports,
        )
        root = tmp_path / "records"
        child = subprocess.Popen(
            [sys.executable, "-I", str(script), str(root)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        try:
            import select

            ready, _, _ = select.select([child.stdout], [], [], 8)
            assert ready, "Next model boundary was not reached after admission flush"
            result = json.loads(child.stdout.readline())
            assert result["consumed"] is True
            journal = json.loads(child_store_type(root)._path(result["id"], ".inputs").read_text())
            assert journal["inputs"] == []
            child.kill()
            assert child.wait(timeout=5) == -9
            record = child_store_type(root).load()[0]
            assert record["status"] == "interrupted" and record["undelivered_inputs"] == []
            assert "CONSUMED BEFORE CRASH" not in str(record)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)
            child.stdout.close()
            child.stderr.close()
