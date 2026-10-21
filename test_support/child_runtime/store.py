from __future__ import annotations

import asyncio
import os
import threading

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.agent.supervision import ChildSpec
from xdog.ai.types import AssistantMessage
from xdog.ai.utils.event_stream import EventStream


def paired_history(rounds, text="small"):
    from xdog.agent.messages import messages_to_dicts
    from xdog.ai.types import TextContent, ToolCall, ToolResultMessage, UserMessage

    messages = [UserMessage(content="task")]
    for index in range(rounds):
        messages.extend(
            [
                AssistantMessage(content=(ToolCall(id=f"call-{index}", name="read", arguments={}),)),
                ToolResultMessage(tool_call_id=f"call-{index}", tool_name="read", content=(TextContent(text=text),)),
            ]
        )
    messages.append(AssistantMessage(content=(TextContent(text="done"),)))
    return messages_to_dicts(messages)


class TestChildStore:
    async def test_interrupted_catalog_never_restarts_and_closed_results_remain_archived(
        self, tmp_path, *, runtime_factory, child_store_type
    ):
        store = child_store_type.scoped(tmp_path, "session", "head")
        spec = ChildSpec("0123456789ab", "task", "read-only")
        store.begin(spec)

        def factory(spec, publish):
            raise AssertionError("Archived work must never restart")

        owner = runtime_factory(factory, records=store.load(), forget=store.forget)
        try:
            assert not owner.active
            record = await owner.control("inspect", spec.id)
            assert record["status"] == "interrupted" and "effects may have occurred" in record["error"]
            await owner.control("close", spec.id)
            assert not store.load() and (store.root / f"{spec.id}.json").exists()
        finally:
            await owner.aclose()

    @pytest.mark.parametrize("scope", [("../session", "head"), ("session", "../../outside"), ("session", "")])
    def test_scope_cannot_escape_parent_session_directory(self, tmp_path, scope, *, child_store_type):
        with pytest.raises(ValueError, match="scope"):
            child_store_type.scoped(tmp_path, *scope)

    def test_records_reject_links_and_entry_state_is_isolated(self, tmp_path, *, child_store_type):
        store = child_store_type(tmp_path / "records")
        one, two = ChildSpec("0123456789ab", "one"), ChildSpec("abcdef012345", "two")
        store.begin(one)
        store.begin(two)
        store.save_entries(one.id, {"plugin": [{"id": "entry", "kind": "note", "data": "one"}]})
        assert store.entries(two.id) == {}
        path = store.root / f"{one.id}.json"
        path.unlink()
        path.symlink_to(tmp_path / "outside")
        with pytest.raises(ValueError, match="symbolic"):
            store.begin(one)
        with pytest.raises(ValueError):
            store.load()

    async def test_async_factory_has_no_absolute_deadline_and_cancel_settles_before_persistence(
        self, *, runtime_factory
    ):
        finalized, records = [], []
        entered = threading.Event()

        async def factory(spec, publish):
            entered.set()
            await asyncio.Event().wait()
            return Agent(lambda *args: EventStream.empty(AssistantMessage()), config=AgentConfig(model="test/dummy"))

        async def finalize(spec):
            await asyncio.sleep(0)
            finalized.append(spec.id)

        def persist(record):
            assert finalized == [record["id"]]
            records.append(record)

        owner = runtime_factory(factory, persist, finalize=finalize)
        try:
            identifier = await owner.start("task")
            assert await asyncio.to_thread(entered.wait, 5)
            record = await owner.control("cancel", identifier)
            assert record["status"] == "canceled" and record["calls"] == 0 and len(records) == 1
        finally:
            await owner.aclose()

    async def test_cancel_during_begin_cannot_overwrite_final_record_later(self, *, runtime_factory):
        entered, release = threading.Event(), threading.Event()
        writes = []

        def begin(spec):
            entered.set()
            assert release.wait(5)
            writes.append("begin")

        def persist(record):
            writes.append(record["status"])

        def factory(spec, publish):
            return Agent(lambda *args: EventStream.empty(AssistantMessage()), config=AgentConfig(model="test/dummy"))

        owner = runtime_factory(factory, persist, begin=begin)
        try:
            identifier = await owner.start("task")
            async with asyncio.timeout(3):
                while not entered.is_set():
                    await asyncio.sleep(0.003)
            canceling = asyncio.create_task(owner.control("cancel", identifier))
            await asyncio.sleep(0.02)
            assert not canceling.done()
            release.set()
            record = await canceling
            assert record["status"] == "canceled" and writes == ["begin", "canceled"]
        finally:
            release.set()
            await owner.aclose()

    async def test_idle_scope_freezes_admission_until_the_entire_host_change_finishes(self, *, runtime_factory):
        def factory(spec, publish):
            return Agent(lambda *args: EventStream.empty(AssistantMessage()), config=AgentConfig(model="test/dummy"))

        owner = runtime_factory(factory)
        try:
            with owner.idle_scope():
                with pytest.raises(RuntimeError, match="context/policy is changing"):
                    await owner.start("race")
                assert not owner.active and not owner.snapshot()["children"]
            identifier = await owner.start("later")
            await owner.control("wait", identifier)
        finally:
            await owner.aclose()

    async def test_cancel_during_close_archive_settles_metadata_before_navigation_can_admit(self, *, runtime_factory):
        entered, release = threading.Event(), threading.Event()
        writes = []

        def factory(spec, publish):
            return Agent(lambda *args: EventStream.empty(AssistantMessage()), config=AgentConfig(model="test/dummy"))

        def forget(identifier):
            entered.set()
            assert release.wait(5)
            writes.append(identifier)

        owner = runtime_factory(factory, forget=forget)
        try:
            identifier = await owner.start("task")
            await owner.control("wait", identifier)
            closing = asyncio.create_task(owner.control("close", identifier))
            async with asyncio.timeout(3):
                while not entered.is_set():
                    await asyncio.sleep(0.002)
            closing.cancel()
            await asyncio.sleep(0.02)
            assert owner.active and not closing.done()
            with pytest.raises(RuntimeError, match="Child work"):
                with owner.idle_scope():
                    pass
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await closing
            assert writes == [identifier] and not owner.active and not owner.snapshot()["children"]
        finally:
            release.set()
            await owner.aclose()

    @pytest.mark.skipif(os.name != "posix", reason="POSIX directory-fd durability primitive")
    def test_child_record_flushes_file_then_rename_then_directory_and_new_parent_entries(
        self, tmp_path, monkeypatch, *, child_store_type, child_store_module
    ):
        import os
        import stat

        module = child_store_module
        store = child_store_type(tmp_path / "new-parent" / "records")
        calls = []
        fsync, replace = module.os.fsync, module.os.replace

        def sync(fd):
            calls.append("directory" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file")
            fsync(fd)

        def rename(source, target):
            calls.append("replace")
            replace(source, target)

        monkeypatch.setattr(module.os, "fsync", sync)
        monkeypatch.setattr(module.os, "replace", rename)
        store.begin(ChildSpec("0123456789ab", "retained initial task"))
        assert calls == ["directory", "directory", "file", "replace", "directory"]
        assert stat.S_IMODE(store.root.stat().st_mode) == 0o700
        calls.clear()
        record = store.load()[0]
        record["error"] = "second actual record"
        store.save(record)
        assert calls == ["file", "replace", "directory"]
        assert store.load()[0]["error"] == "second actual record"
        assert not list(store.root.glob(".child-*"))

    def test_directory_flush_failure_reports_uncertain_replacement_without_rollback(
        self, tmp_path, monkeypatch, *, child_store_type
    ):
        store = child_store_type(tmp_path / "records")
        store.begin(ChildSpec("0123456789ab", "task"))
        record = store.load()[0]
        record["error"] = "replacement actually happened"

        def fail(directory):
            raise OSError("Owned directory flush failure")

        monkeypatch.setattr(store, "_sync_directory", fail)
        with pytest.raises(OSError, match="flush failure"):
            store.save(record)
        # The failure is after rename, not proof the old state survived. No automatic
        # rollback or second write: callers must inspect actual persisted state.
        assert store.load()[0]["error"] == "replacement actually happened"
        assert not list(store.root.glob(".child-*"))

    @pytest.mark.skipif(os.name != "posix", reason="POSIX directory-fd durability primitive")
    def test_directory_flush_failure_closes_its_owned_fd(
        self, tmp_path, monkeypatch, *, child_store_type, child_store_module
    ):
        import os

        module = child_store_module
        directory = tmp_path / "owned"
        directory.mkdir()
        original = module.os.open
        fds = []

        def opened(*args, **kwargs):
            fd = original(*args, **kwargs)
            fds.append(fd)
            return fd

        def fail(fd):
            raise OSError("Owned fsync failure")

        monkeypatch.setattr(module.os, "open", opened)
        monkeypatch.setattr(module.os, "fsync", fail)
        with pytest.raises(OSError, match="fsync failure"):
            child_store_type._sync_directory(directory)
        assert len(fds) == 1
        with pytest.raises(OSError):
            os.fstat(fds[0])

    def test_segmented_history_crosses_2000_messages_and_record_file_capacity_without_losing_pairs(
        self, tmp_path, monkeypatch, *, child_store_type, child_store_module
    ):
        module = child_store_module
        from xdog.agent.history import validate_checkpoint
        from xdog.agent.messages import dicts_to_messages

        monkeypatch.setattr(module, "MAX_RECORD_BYTES", 700 * 1024)
        store = child_store_type(tmp_path)
        spec = ChildSpec("0123456789ab", "task")
        store.begin(spec)
        record = store.load()[0]
        record["messages"] = paired_history(1005, "x" * 1200)
        store.save(record)
        files = list(tmp_path.glob("*.json"))
        assert len(files) > 3 and all(path.stat().st_size <= module.MAX_RECORD_BYTES for path in files)
        loaded = store.load()[0]
        assert loaded["messages"] == record["messages"] and len(loaded["messages"]) > 2000
        typed = dicts_to_messages(loaded["messages"])
        validate_checkpoint(typed, len(typed) - 1)
        store.forget(spec.id)
        assert store.load() == []  # Close archives the entire history; it does not delete the chunks.
        assert store._read(store._path(spec.id))["messages"] == record["messages"]

    async def test_new_child_rejects_even_valid_large_parent_history_without_copy_or_replay(
        self, tmp_path, *, runtime_factory, child_store_type
    ):
        from xdog.agent.messages import dicts_to_messages

        store = child_store_type(tmp_path)
        history = paired_history(1005)

        def factory(spec, publish):
            agent = Agent(lambda *args: EventStream.empty(AssistantMessage()), config=AgentConfig(model="test/dummy"))
            agent.replace_messages(dicts_to_messages(history))
            return agent

        owner = runtime_factory(factory, store.save, begin=store.begin, persist_admission=store.save_admission)
        try:
            identifier = await owner.start("continue")
            result = await owner.control("wait", identifier)
            assert result["status"] == "failed" and result["calls"] == 0
            assert "empty history" in result["error"]
            assert result["messages"] == []
            assert store.load()[0]["messages"] == result["messages"]
        finally:
            await owner.aclose()

    @pytest.mark.parametrize("damage", ["missing", "corrupt", "symlink"])
    def test_segmented_history_is_not_silently_truncated_on_missing_corrupt_or_linked_chunk(
        self, tmp_path, damage, *, child_store_type
    ):
        store = child_store_type(tmp_path)
        spec = ChildSpec("0123456789ab", "task")
        store.begin(spec)
        record = store.load()[0]
        record["messages"] = paired_history(4, "x" * 150000)
        store.save(record)
        chunk = next(tmp_path.glob("*.history-*.json"))
        content = chunk.read_bytes()
        chunk.unlink()
        if damage == "corrupt":
            chunk.write_text('{"previous": null, "items": [{}]}')
        elif damage == "symlink":
            target = tmp_path / "outside"
            target.write_bytes(content)
            chunk.symlink_to(target)
        with pytest.raises((ValueError, OSError)):
            store.load()

    def test_failed_segmented_manifest_commit_preserves_prior_checkpoint_and_never_replays(
        self, tmp_path, monkeypatch, *, child_store_type, child_store_module
    ):
        module = child_store_module
        store = child_store_type(tmp_path)
        spec = ChildSpec("0123456789ab", "task")
        store.begin(spec)
        original = store.load()[0]
        record = {**original, "messages": paired_history(4, "x" * 150000)}
        replace = module.os.replace

        def fail_manifest(source, destination):
            if destination == store._path(spec.id):
                raise OSError("simulated crash before manifest commit")
            replace(source, destination)

        monkeypatch.setattr(module.os, "replace", fail_manifest)
        with pytest.raises(OSError, match="simulated crash"):
            store.save(record)
        assert list(tmp_path.glob("*.history-*.json"))
        assert store.load() == [original]
        monkeypatch.setattr(module.os, "replace", replace)
        store.save(record)
        assert store.load()[0]["messages"] == record["messages"]

    async def test_historical_budget_record_loads_without_reapplying_or_restarting(
        self, tmp_path, *, runtime_factory, child_store_type
    ):
        store = child_store_type(tmp_path)
        spec = ChildSpec("0123456789ab", "task")
        store.begin(spec)
        record = store.load()[0]
        record["config"].update(timeout=1, max_model_calls=1, max_tokens=1)
        record.update(calls=50, tokens=500000)
        store.save(record)
        for records in ([record], store.load()):
            owner = runtime_factory(lambda *args: pytest.fail("Never replay"), records=records)
            try:
                inspected = await owner.control("inspect", spec.id)
                assert inspected["calls"] == 50 and inspected["tokens"] == 500000
                assert not {"timeout", "max_model_calls", "max_tokens"} & inspected["config"].keys()
                assert not owner.active
            finally:
                await owner.aclose()

    def test_persisted_catalog_has_no_old_16_record_limit(self, tmp_path, *, child_store_type):
        store = child_store_type(tmp_path)
        for index in range(20):
            store.begin(ChildSpec(f"{index:012x}", f"task-{index}"))
        assert len(store.load()) == 20

    def test_per_file_record_capacity_is_still_enforced(
        self, tmp_path, monkeypatch, *, child_store_type, child_store_module
    ):
        module = child_store_module
        monkeypatch.setattr(module, "MAX_RECORD_BYTES", 1000)
        store = child_store_type(tmp_path)
        spec = ChildSpec("0123456789ab", "task")
        store.begin(spec)
        original = store.load()[0]
        with pytest.raises(ValueError, match="persistence budget"):
            store.save({**original, "error": "x" * 1001})
        assert store.load() == [original]
        path = store._path(spec.id)
        path.write_text(" " * 1001)
        with pytest.raises(ValueError, match="record file"):
            store.load()

    def test_single_message_capacity_remains_enforced_for_persisted_history(self, tmp_path, *, child_store_type):
        from xdog.agent.supervision import MAX_TRANSCRIPT_BYTES

        store = child_store_type(tmp_path)
        spec = ChildSpec("0123456789ab", "task")
        store.begin(spec)
        original = store.load()[0]
        store.save({**original, "messages": paired_history(1, "x" * MAX_TRANSCRIPT_BYTES)})
        with pytest.raises(ValueError, match="oversized child text message"):
            store.load()

    def test_compaction_archives_preserve_full_segmented_pairs_and_are_immutable(self, tmp_path, *, child_store_type):
        from xdog.agent.messages import dicts_to_messages

        store = child_store_type(tmp_path)
        spec = ChildSpec("0123456789ab", "task")
        history = tuple(dicts_to_messages(paired_history(1005, "x" * 1000)))
        store.archive_compaction(spec, 1, history)
        store.archive_compaction(spec, 1, history)  # ACK-loss retry is exact and idempotent, not model replay.
        assert store.compaction_archives(spec.id)[0]["messages"] == paired_history(1005, "x" * 1000)
        with pytest.raises(ValueError, match="identity mismatch"):
            store.archive_compaction(spec, 1, tuple(dicts_to_messages(paired_history(1))))
        assert store.compaction_archives(spec.id)[0]["messages"] == paired_history(1005, "x" * 1000)

    @pytest.mark.parametrize("damage", ["mixed", "count", "traversal", "identity"])
    def test_history_manifest_rejects_ambiguous_count_mismatched_and_escaping_references(
        self, tmp_path, damage, *, child_store_type
    ):
        import json

        store = child_store_type(tmp_path)
        spec = ChildSpec("0123456789ab", "task")
        store.begin(spec)
        record = store.load()[0]
        record["messages"] = paired_history(4, "x" * 150000)
        store.save(record)
        path = store._path(spec.id)
        manifest = json.loads(path.read_text())
        if damage == "mixed":
            manifest["messages"] = []
        elif damage == "count":
            manifest["history"]["count"] += 1
        elif damage == "identity":
            del manifest["id"]
        else:
            manifest["history"]["tail"] = "../outside"
        path.write_text(json.dumps(manifest))
        with pytest.raises(ValueError):
            store.load()

    def test_read_file_rejects_final_symlinks_and_nonregular_inodes_without_blocking(
        self, tmp_path, *, child_store_type
    ):
        store = child_store_type(tmp_path)
        target = tmp_path / "target"
        target.write_text("{}")
        linked = tmp_path / "linked"
        linked.symlink_to(target)
        with pytest.raises((OSError, ValueError)):
            store._read_file(linked)
        fifo = tmp_path / "fifo"
        os.mkfifo(fifo)
        with pytest.raises(ValueError, match="record file"):
            store._read_file(fifo)

    def test_archive_directory_sync_failure_does_not_acknowledge_success_or_remove_original(
        self, tmp_path, monkeypatch, *, child_store_type
    ):
        from xdog.agent.messages import dicts_to_messages

        store = child_store_type(tmp_path)
        spec = ChildSpec("0123456789ab", "task")
        store.begin(spec)
        original = store.load()[0]

        def failed_sync(directory):
            raise OSError("directory durability unknown")

        monkeypatch.setattr(store, "_sync_directory", failed_sync)
        with pytest.raises(OSError, match="durability unknown"):
            store.archive_compaction(spec, 1, tuple(dicts_to_messages(paired_history(2))))
        assert store.load() == [original]
        # Rename may have committed; retain that export conservatively, never
        # roll back its bytes or automatically rerun the child to recreate it.
        assert store.compaction_archives(spec.id)[0]["messages"] == paired_history(2)

    def test_exact_archive_retry_reestablishes_directory_durability_after_unknown_commit(
        self, tmp_path, monkeypatch, *, child_store_type
    ):
        from xdog.agent.messages import dicts_to_messages

        store = child_store_type(tmp_path)
        spec = ChildSpec("0123456789ab", "task")
        store.begin(spec)
        messages = tuple(dicts_to_messages(paired_history(2)))
        sync = store._sync_directory

        def failed_sync(directory):
            raise OSError("unknown archive commit")

        monkeypatch.setattr(store, "_sync_directory", failed_sync)
        with pytest.raises(OSError):
            store.archive_compaction(spec, 1, messages)
        flushed = []

        def acknowledged_sync(directory):
            sync(directory)
            flushed.append(directory)

        monkeypatch.setattr(store, "_sync_directory", acknowledged_sync)
        store.archive_compaction(spec, 1, messages)
        assert flushed == [store.root]
        assert store.compaction_archives(spec.id)[0]["messages"] == paired_history(2)
