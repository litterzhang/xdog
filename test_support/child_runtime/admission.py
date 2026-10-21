"""UI-046: prospective history/feedback before visible receipts; crash is never replay."""

from __future__ import annotations

import asyncio
import json
import os
import select
import subprocess
import sys
from pathlib import Path

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.agent.children import ChildSpec
from xdog.ai.types import AssistantMessage
from xdog.ai.utils.event_stream import EventStream

from test_support.child_runtime.processes import write_host_script


class TestChildAdmission:
    @pytest.mark.skipif(sys.platform != "linux", reason="Owned current Linux SIGKILL/fsync boundary proof")
    @pytest.mark.parametrize("handled", [False, True])
    @pytest.mark.parametrize(
        "boundary",
        ["before-checkpoint", "before-replace", "after-replace", "after-checkpoint", "before-settle", "after-settle"],
    )
    def test_owned_sigkill_history_feedback_and_queue_boundary_never_replays(
        self, tmp_path, handled, boundary, *, runtime_factory, child_store_type, child_process_imports
    ):
        script = tmp_path / "owned_checkpoint.py"
        write_host_script(
            script,
            "admission_1.py.txt",
            child_process_imports,
        )
        root = tmp_path / "records"
        process = subprocess.Popen(
            [sys.executable, "-I", str(script), str(root), boundary, str(handled)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            ready, _, _ = select.select([process.stdout], [], [], 8)
            assert ready, "Owned boundary receipt absent before deadline"
            # Byte reads prevent a partially written line from becoming an unbounded readline.
            receipt = bytearray()
            import time

            deadline = time.monotonic() + 2
            while not receipt.endswith(b"\n"):
                assert time.monotonic() < deadline, "Partial boundary receipt"
                assert select.select([process.stdout], [], [], max(0, deadline - time.monotonic()))[0]
                byte = os.read(process.stdout.fileno(), 1)
                assert byte
                receipt.extend(byte)
            result = json.loads(receipt)
            assert result["boundary"] == boundary
            process.kill()
            assert process.wait(timeout=5) == -9
            records = child_store_type(root).load()
            record = records[0]
            assert record["status"] == "interrupted" and record["queue_uncertain"] is True
            committed = boundary not in {"before-checkpoint", "before-replace"}
            if handled:
                assert ("CHECKPOINT FEEDBACK" in record.get("input_feedback", [])) == committed
                assert "TRANSFORMED TARGET" not in str(record["messages"])
            else:
                admitted = any("TRANSFORMED TARGET" in json.dumps(row.get("content")) for row in record["messages"])
                assert admitted == committed
            pending = record["undelivered_inputs"]
            assert [row["content"] for row in pending] == ([] if boundary == "after-settle" else ["ORIGINAL TARGET"])
            if pending:
                assert pending[0]["images"][0]["mime_type"] == "image/png"
                assert pending[0]["images"][0]["data"].startswith("iVBORw0KGgo")
            if committed and not handled:
                assert any("iVBORw0KGgo" in json.dumps(row["content"]) for row in record["messages"])

            async def inspect():
                owner = runtime_factory(
                    lambda *args: pytest.fail("Crash recovery must not restart/model replay"), records=records
                )
                try:
                    assert not owner.active
                    queue = (await owner.control("queue", result["id"], '{"action":"list"}'))["queue"]
                    assert queue["stopped"] is True
                    assert all(row["uncertain"] for row in queue["inputs"])
                    assert (await owner.control("inspect", result["id"]))["messages"] == record["messages"]
                finally:
                    await owner.aclose()

            asyncio.run(inspect())
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            process.stdout.close()
            process.stderr.close()

    @pytest.mark.parametrize("status", ["completed", "interrupted", "canceled"])
    def test_terminal_record_supersedes_admission_even_when_cleanup_interrupted(
        self, tmp_path, monkeypatch, status, *, child_store_type
    ):
        store = child_store_type(tmp_path / "records")
        spec = ChildSpec("0123456789ab", "TASK")
        store.begin(spec)
        checkpoint = store.load()[0]
        checkpoint.update(messages=[{"role": "user", "content": "ADMITTED"}], settling=False)
        store.save_admission(checkpoint)
        original = Path.unlink

        def fail(path, *args, **kwargs):
            if ".admission." in path.name:
                raise OSError("Owned cleanup interruption")
            return original(path, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", fail)
        with pytest.raises(OSError, match="cleanup interruption"):
            store.save({**checkpoint, "status": status, "messages": [{"role": "user", "content": "FINAL"}]})
        assert store._path(spec.id, ".admission").exists()
        assert store.load()[0]["status"] == status
        assert store.load()[0]["messages"][0]["content"] == "FINAL"

    @pytest.mark.parametrize("fault", ["id", "task", "status", "settling", "messages"])
    def test_admission_checkpoint_rejects_foreign_or_invalid_saved_records(self, tmp_path, fault, *, child_store_type):
        store = child_store_type(tmp_path / "records")
        spec = ChildSpec("0123456789ab", "TASK")
        store.begin(spec)
        record = store.load()[0]
        record[fault] = {
            "id": "abcdef012345",
            "task": "FOREIGN",
            "status": "running",
            "settling": True,
            "messages": "invalid",
        }[fault]
        store._write(store._path(spec.id, ".admission"), record)
        with pytest.raises(ValueError):
            store.load()

    @pytest.mark.parametrize("action", ["cancel", "close"])
    @pytest.mark.parametrize("written", [False, True])
    async def test_cancel_close_reachable_while_checkpoint_writer_owns_storage(
        self, tmp_path, action, written, *, runtime_factory, child_store_type
    ):
        import threading

        entered, release = threading.Event(), threading.Event()
        store = child_store_type(tmp_path / "records")
        contexts = []

        def checkpoint(record):
            if written:
                store.save_admission(record)
            entered.set()
            assert release.wait(5)
            if not written:
                store.save_admission(record)

        owner = runtime_factory(
            lambda *args: Agent(
                lambda *args: contexts.append(args) or EventStream.empty(AssistantMessage()),
                config=AgentConfig(model="offline/model"),
            ),
            store.save,
            begin=store.begin,
            persist_inputs=store.save_inputs,
            persist_admission=checkpoint,
            forget=store.forget,
        )
        cancel = None
        try:
            child = await owner.start("INITIAL")
            assert await asyncio.to_thread(entered.wait, 3)
            cancel = asyncio.create_task(owner.control(action, child))
            await asyncio.sleep(0.05)
            assert not cancel.done() and owner._children[child].queue_work
            assert not contexts  # No provider entry before admission; never restart after cancel.
            release.set()
            result = await asyncio.wait_for(cancel, 3)
            assert result["status"] == "canceled"
            assert not owner.active and not contexts
            if action == "close":
                assert store.load() == []
            else:
                assert store.load()[0]["status"] == "canceled"
                assert store.load()[0]["queue_uncertain"] is True
                assert store.load()[0]["initial_state"] == "uncertain"
                queue = (await owner.control("queue", child, '{"action":"list"}'))["queue"]
                assert queue["inputs"][-1]["uncertain"] is True
            assert not store._path(child, ".admission").exists()
        finally:
            release.set()
            if cancel is not None:
                await asyncio.gather(cancel, return_exceptions=True)
            await owner.aclose()

    @pytest.mark.parametrize("failure", ["replace", "directory-fsync"])
    def test_failed_admission_replace_or_fsync_exposes_only_valid_old_or_new_checkpoint(
        self, tmp_path, monkeypatch, failure, *, child_store_type
    ):
        store = child_store_type(tmp_path / "records")
        spec = ChildSpec("0123456789ab", "TASK")
        store.begin(spec)
        record = store.load()[0]
        old = {**record, "messages": [{"role": "user", "content": "OLD"}]}
        store.save_admission(old)
        new = {**record, "messages": [{"role": "user", "content": "NEW"}]}

        def fail(*args):
            raise OSError("Owned checkpoint storage failure")

        if failure == "replace":
            monkeypatch.setattr(os, "replace", fail)
        else:
            monkeypatch.setattr(store, "_sync_directory", fail)
        with pytest.raises(OSError, match="storage failure"):
            store.save_admission(new)
        loaded = store.load()[0]
        assert loaded["messages"][0]["content"] == ("OLD" if failure == "replace" else "NEW")
        assert loaded["status"] == "interrupted"
        assert not list(store.root.glob(".child-*"))
