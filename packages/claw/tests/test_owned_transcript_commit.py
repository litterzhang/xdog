"""Ordinary execution receipts follow actual history-data durability, not metadata alone."""
from __future__ import annotations

import json
import os
import stat
import threading
from pathlib import Path

import pytest
from xdog.ai.types import AssistantMessage, TextContent
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.config import ClawConfig
from xdog.claw.core.inputs.models import InputScope
from xdog.claw.core.runtime.orchestrator import Orchestrator
from xdog.claw.core.types import Group, UserInput


async def test_succeeded_turn_fsyncs_actual_user_and_assistant_history_before_receipt(tmp_path, monkeypatch):
    owner = threading.get_ident()
    actual_fsync = os.fsync
    observed, durable_histories, calls = [], [], []

    def fsync(descriptor):
        actual_fsync(descriptor)
        path = Path(os.readlink(f"/proc/self/fd/{descriptor}"))
        if not path.is_relative_to(tmp_path):
            return
        observed.append((path, threading.get_ident()))
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            return
        # Inspect the actual fsynced inode, including a temporary pre-rename
        # history image. A catalog/commit marker containing a summary is not it.
        raw = Path(f"/proc/self/fd/{descriptor}").read_bytes()
        try:
            rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
        except (ValueError, UnicodeError):
            return
        if (rows and all(isinstance(row, dict) for row in rows)
                and {"user", "assistant"} <= {row.get("role") for row in rows}
                and b"durability user sentinel" in raw and b"durability assistant sentinel" in raw):
            durable_histories.append(rows)

    def model(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage(content=(TextContent(text="durability assistant sentinel"),)))

    app = Orchestrator(ClawConfig(data_dir=str(tmp_path / "data")), model="test/dummy", stream_fn=model)
    app.register_group(Group(id="main", workspace=str(tmp_path / "workspace"), enabled_tools=()))
    try:
        await app.start()
        monkeypatch.setattr(os, "fsync", fsync)
        result = await app.route_message(UserInput(group_id="main", channel="tui", content="durability user sentinel"),
                                         message_id="durable-body")
        service = await app.input_service()
        scope = InputScope("main", app.get_group_workspace("main"))
        record = await service.store.get(scope, "durable-body")
        assert result is not None and result.error is None and record.status == "succeeded" and len(calls) == 1
        assert observed, "Removing fsync is not a durability implementation"
        assert durable_histories, "Succeeded receipt was published without fsync of the actual JSONL message data"
        assert all(thread != owner for _path, thread in observed), "Final-turn IO still runs on the Agent owner"
    finally:
        await app.stop()


async def test_metadata_failure_rolls_back_history_and_cache_without_replaying_actual_tool(tmp_path, monkeypatch):
    import copy
    import shlex

    from xdog.ai.types import ToolCall

    calls = []
    effect = tmp_path / "workspace" / "effect.log"

    def model(*args):
        calls.append(args)
        parts = ((ToolCall(id="effect", name="bash", arguments={
            "command": f"printf 'effect\\n' >> {shlex.quote(str(effect))}"}),) if len(calls) == 1 else
                 (TextContent(text="actual effect finished"),))
        return EventStream.empty(AssistantMessage(content=parts, stop_reason="toolUse" if len(calls) == 1 else "stop"))

    app = Orchestrator(ClawConfig(data_dir=str(tmp_path / "data")), model="test/dummy", stream_fn=model)
    app.register_group(Group(id="main", workspace=str(tmp_path / "workspace"), enabled_tools=("bash",)))
    runtime = app._runtimes["main"]
    original = runtime.transcript_store._atomic
    failed = False
    history_writes = []

    def actual_write_then_fail(path, value, **kwargs):
        nonlocal failed
        result = original(path, value, **kwargs)
        if calls and kwargs.get("jsonl"):
            history_writes.append(path)
        if calls and path == runtime.transcript_store._catalog_file and not failed:
            failed = True
            raise OSError("actual metadata commit failed after replacement")
        return result

    try:
        await app.start()
        session = await runtime.prepare_session(allow_reset=False)
        store = runtime.transcript_store
        paths = (store._path(session.meta.session_id), store._catalog_file, store._index_file)
        before = {path.name: path.read_bytes() for path in paths}
        catalog, index = copy.deepcopy(store._catalog), copy.deepcopy(store._index)
        monkeypatch.setattr(store, "_atomic", actual_write_then_fail)
        result = await app.route_message(UserInput(group_id="main", content="actual one-time effect"),
                                         message_id="effect")
        assert result is not None and result.error and "metadata commit failed" in result.error
        assert effect.read_text() == "effect\n" and len(calls) == 2
        assert {path.name: path.read_bytes() for path in paths} == before
        assert store._catalog == catalog and store._index == index
        assert session._persisted_count == 0 and session.meta.turn_count == 0
        assert len(history_writes) == 1, "Failure fallback must not retry/append a partially written batch"
        import datetime
        old = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=4)
        store._index["main"]["created_at"] = old.isoformat()
        store._save_index()
        later = await app.route_message(UserInput(group_id="main", content="must not run with uncertain history"),
                                        message_id="later")
        assert later is not None and later.error and len(calls) == 2 and effect.read_text() == "effect\n"
    finally:
        await app.stop()


async def test_cancel_during_real_commit_keeps_original_owner_and_publishes_committed_cursor_once(
    tmp_path, monkeypatch,
):
    import asyncio

    from xdog.claw.core.persistence.transcript_store import TranscriptStore

    entered, release = threading.Event(), threading.Event()
    calls, executions, producers = [], [], []

    def model(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage(content=(TextContent(text="actual committed reply"),)))

    def execution(binding):
        executions.append(binding)
        producers.append(asyncio.current_task())

    app = Orchestrator(ClawConfig(data_dir=str(tmp_path / "data")), model="test/dummy", stream_fn=model)
    app.register_group(Group(id="main", workspace=str(tmp_path / "workspace"), enabled_tools=()))
    runtime = app._runtimes["main"]
    original = runtime.transcript_store._atomic
    held, writes = False, []

    def actual_write_then_hold(path, value, **kwargs):
        nonlocal held
        result = original(path, value, **kwargs)
        if calls and kwargs.get("jsonl"):
            writes.append(path)
        if calls and path == runtime.transcript_store._index_file and not held:
            held = True
            entered.set()
            assert release.wait(5)
        return result

    work = reading = None
    try:
        await app.start()
        monkeypatch.setattr(runtime.transcript_store, "_atomic", actual_write_then_hold)
        work = asyncio.create_task(app.route_message(UserInput(group_id="main", channel="tui", content="actual input"),
                                                     message_id="original", on_execution_started=execution))
        assert await asyncio.to_thread(entered.wait, 3)
        session = runtime._active_session
        scope = InputScope("main", runtime.workspace_dir)
        service = await app.input_service()
        assert (await service.store.get(scope, "original")).status == "running"
        assert session._persisted_count == 0 and session.meta.turn_count == 0
        assert runtime.transcript_store.get_active_session("main").turn_count == 0
        read_started, read_finished = threading.Event(), threading.Event()

        def actual_read():
            read_started.set()
            try:
                return runtime.transcript_store.load_transcript(session.meta.session_id)
            finally:
                read_finished.set()

        reading = asyncio.create_task(asyncio.to_thread(actual_read))
        assert await asyncio.to_thread(read_started.wait, 3)
        assert not await asyncio.to_thread(read_finished.wait, 0.05), "Reader exposed pre-marker uncommitted history"
        for _ in range(3):
            producers[0].cancel()  # The actual original owner, not a detached result waiter.
            await asyncio.sleep(0)
        assert app.is_group_running("main") and not producers[0].done()
        assert len(calls) == 1 and len(writes) == 1
        release.set()
        await asyncio.gather(work, return_exceptions=True)
        record = await asyncio.wait_for(service.wait(scope, "original"), 3)
        assert record.status == "interrupted" and record.attempt_id == executions[0].attempt_id
        assert session._persisted_count == len(session.agent.messages) == 2 and session.meta.turn_count == 1
        assert len(writes) == 1 and len(calls) == 1
        raw = runtime.transcript_store.load_transcript(session.meta.session_id)
        assert await reading == raw
        assert len(raw) == 2 and raw[0]["channel"] == "tui" and "usage" in raw[1]
        reopened = TranscriptStore(runtime.transcript_store._sessions_dir, owner_group="main")
        assert reopened.get_active_session("main").turn_count == 1
        assert reopened.load_transcript(session.meta.session_id) == raw
    finally:
        release.set()
        if work is not None:
            await asyncio.gather(work, return_exceptions=True)
        if reading is not None:
            await asyncio.gather(reading, return_exceptions=True)
        await app.stop()


def test_commit_preserves_hidden_metadata_and_refuses_same_length_prefix_conflict(tmp_path):
    import dataclasses

    from xdog.agent.messages import messages_to_dicts
    from xdog.ai.types import UserMessage
    from xdog.claw.core.persistence.transcript_commit import HistoryCommit, commit
    from xdog.claw.core.persistence.transcript_convert import transcript_to_messages
    from xdog.claw.core.persistence.transcript_store import TranscriptStore

    store = TranscriptStore(tmp_path, owner_group="main")
    meta = store.create_session("main")
    prefix = [{"role": "system", "content": "original summary", "is_compaction": True,
               "compaction": {"source": "builtin", "first_kept_index": 1, "original_count": 3, "details": {}},
               "channel": "summary", "private_metadata": {"keep": "exact"}},
              {"role": "custom", "type": "audit.fixture", "data": {"kind": "keep"}, "channel": "internal"}]
    store.replace_transcript(meta.session_id, prefix)
    messages = (*transcript_to_messages(prefix), UserMessage(content="new user"),
                AssistantMessage(content=(TextContent(text="new answer"),)))
    batch = HistoryCommit(meta, tmp_path, "", json.dumps(messages_to_dicts(list(messages))), 2, (),
                          channel="tui", usage=(("input", 3), ("output", 2)), increment_turn=True)
    wrong = dataclasses.replace(batch, messages_json=batch.messages_json.replace("original summary", "forged summary"))
    with pytest.raises(ValueError, match="prefix changed"):
        commit(store, wrong)
    assert store.load_transcript(meta.session_id) == prefix
    assert commit(store, batch).turn_count == 1
    saved = store.load_transcript(meta.session_id)
    assert saved[:2] == prefix and saved[2]["channel"] == "tui" and saved[3]["usage"] == {"input": 3, "output": 2}
    with pytest.raises(ValueError, match="prefix changed"):
        commit(store, batch)  # Repeated same prefix is not a second append.
    assert store.load_transcript(meta.session_id) == saved


async def test_failed_rollback_fences_original_store_and_real_reopen_recovers_without_model_replay(
    tmp_path, monkeypatch,
):
    from xdog.claw.core.persistence.navigation_journal import JOURNAL, NavigationRecoveryError
    from xdog.claw.core.persistence.transcript_store import TranscriptStore

    calls = []

    def model(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage(content=(TextContent(text="original real response"),)))

    def application(stream):
        app = Orchestrator(ClawConfig(data_dir=str(tmp_path / "data")), model="test/dummy", stream_fn=stream)
        app.register_group(Group(id="main", workspace=str(tmp_path / "workspace"), enabled_tools=()))
        return app

    app = application(model)
    runtime = app._runtimes["main"]
    store = runtime.transcript_store
    original_atomic, original_replace = store._atomic, os.replace
    failed = False

    def actual_write_then_fail(path, value, **kwargs):
        nonlocal failed
        result = original_atomic(path, value, **kwargs)
        if calls and path == store._catalog_file and not failed:
            failed = True
            raise OSError("metadata failure after actual replacement")
        return result

    def fail_restore(source, target):
        if Path(source).name.startswith(".navigation-restore-"):
            raise OSError("actual recovery storage unavailable")
        return original_replace(source, target)

    try:
        await app.start()
        session = await runtime.prepare_session(allow_reset=False)
        paths = (store._path(session.meta.session_id), store._catalog_file, store._index_file)
        before = {path.name: path.read_bytes() for path in paths}
        service = await app.input_service()
        scope = InputScope("main", runtime.workspace_dir)
        namespace = await service.store.namespace(scope)
        with monkeypatch.context() as faults:
            faults.setattr(store, "_atomic", actual_write_then_fail)
            faults.setattr(os, "replace", fail_restore)
            result = await app.route_message(UserInput(group_id="main", content="real failed commit"),
                                             message_id="failed")
            assert result is not None and result.error and "recovery storage" in result.error
            assert len(calls) == 1 and (store._sessions_dir / JOURNAL).is_file()
            assert list(store._sessions_dir.glob(".navigation-backup-*.bak"))
            with pytest.raises(NavigationRecoveryError):
                store.get_active_session("main")
            with pytest.raises(NavigationRecoveryError):
                store.load_transcript(session.meta.session_id)
            assert session._persisted_count == 0
            record = await service.store.get(scope, "failed")
            assert record.status == "failed" and record.attempt_id
    finally:
        await app.stop()

    reopened = TranscriptStore(store._sessions_dir, owner_group="main")
    assert {path.name: path.read_bytes() for path in paths} == before
    assert reopened.get_active_session("main").turn_count == 0
    assert not (store._sessions_dir / JOURNAL).exists()

    def no_replay(*args):
        raise AssertionError("A failed/interrupted execution must never automatically rerun its model")

    restored = application(no_replay)
    try:
        await restored.start()
        service = await restored.input_service()
        assert await service.store.namespace(scope) == namespace
        saved = await service.store.get(scope, "failed")
        assert saved.status == "failed" and saved.attempt_id == record.attempt_id
        assert restored._runtimes["main"]._active_session is None and len(calls) == 1
    finally:
        await restored.stop()


async def test_committed_cleanup_failure_does_not_poison_next_batch_or_duplicate_history(tmp_path, monkeypatch):
    from xdog.claw.core.persistence.navigation_journal import JOURNAL

    calls = []

    def model(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage(content=(TextContent(text=f"actual reply {len(calls)}"),)))

    app = Orchestrator(ClawConfig(data_dir=str(tmp_path / "data")), model="test/dummy", stream_fn=model)
    app.register_group(Group(id="main", workspace=str(tmp_path / "workspace"), enabled_tools=()))
    runtime = app._runtimes["main"]
    unlink = os.unlink
    failures = []

    def actual_unlink_boundary(path, *args, **kwargs):
        if Path(path).name == JOURNAL and calls and not failures:
            failures.append(path)
            raise OSError("actual committed backup cleanup unavailable")
        return unlink(path, *args, **kwargs)

    try:
        await app.start()
        with monkeypatch.context() as faults:
            faults.setattr(os, "unlink", actual_unlink_boundary)
            first = await app.route_message(UserInput(group_id="main", content="first actual input"), message_id="one")
            assert first is not None and first.error is None and failures
            assert json.loads((runtime.transcript_store._sessions_dir / JOURNAL).read_text())["phase"] == "committed"
        second = await app.route_message(UserInput(group_id="main", content="second actual input"), message_id="two")
        assert second is not None and second.error is None and len(calls) == 2
        session = runtime._active_session
        assert session.meta.turn_count == 2 and session._persisted_count == 4
        rows = runtime.transcript_store.load_transcript(session.meta.session_id)
        assert len(rows) == 4 and not (runtime.transcript_store._sessions_dir / JOURNAL).exists()
    finally:
        await app.stop()


CRASH_PROGRAM = r'''
import asyncio,base64,json,os,shlex,sys
from pathlib import Path
from xdog.ai.types import AssistantMessage,TextContent,ToolCall
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.config import ClawConfig
from xdog.claw.core.runtime.orchestrator import Orchestrator
from xdog.claw.core.types import Group,UserInput
from xdog.claw.core.persistence import navigation_journal
root,point=Path(sys.argv[1]),sys.argv[2]
calls=[]
def model(*args):
 calls.append(1)
 with (root/'model.log').open('a') as f:f.write('model\n');f.flush();os.fsync(f.fileno())
 parts=((ToolCall(id='effect',name='bash',arguments={'command':
    "printf 'effect\\n' >> "+shlex.quote(str(root/'workspace'/'effect.log'))}),) if len(calls)==1 else
    (TextContent(text='actual process history'),))
 return EventStream.empty(AssistantMessage(content=parts,stop_reason='toolUse' if len(calls)==1 else 'stop'))
async def main():
 app=Orchestrator(ClawConfig(data_dir=str(root/'data')),model='test/process',stream_fn=model)
 app.register_group(Group(id='main',workspace=str(root/'workspace'),enabled_tools=('bash',)))
 await app.start()
 runtime=app._runtimes['main'];session=await runtime.prepare_session(allow_reset=False);store=runtime.transcript_store
 paths=(store._path(session.meta.session_id),store._catalog_file,store._index_file)
 (root/'before.json').write_text(json.dumps({p.name:base64.b64encode(p.read_bytes()).decode() for p in paths}))
 (root/'session').write_text(session.meta.session_id)
 original=store._atomic
 def atomic(path,value,**kw):
  original(path,value,**kw)
  if calls and ((point=='history' and kw.get('jsonl')) or (point=='catalog' and path==store._catalog_file)
                or (point=='index' and path==store._index_file)):
   os._exit(71)
 store._atomic=atomic
 marker=navigation_journal.write_manifest
 def write_marker(directory,value):
  if calls and value['phase']=='committed' and point=='marker-before':os._exit(71)
  marker(directory,value)
  if calls and value['phase']=='committed' and point=='marker-after':os._exit(72)
 navigation_journal.write_manifest=write_marker
 await app.route_message(UserInput(group_id='main',channel='tui',content='actual process input'),message_id='process')
 raise AssertionError('Crash point was not reached')
asyncio.run(main())
'''


@pytest.mark.parametrize("point", ["history", "catalog", "index", "marker-before", "marker-after"])
async def test_actual_process_death_recovers_history_without_replaying_executed_tool(tmp_path, point):
    import asyncio
    import base64
    import subprocess
    import sys

    from xdog.claw.core.persistence.transcript_store import TranscriptStore

    child = await asyncio.to_thread(subprocess.run, [sys.executable, "-c", CRASH_PROGRAM, str(tmp_path), point],
                                    capture_output=True, text=True, timeout=20)
    assert child.returncode == (72 if point == "marker-after" else 71), (child.stdout, child.stderr)
    assert (tmp_path / "model.log").read_text().splitlines() == ["model", "model"]
    assert (tmp_path / "workspace" / "effect.log").read_text() == "effect\n"
    root = tmp_path / "data" / "groups" / "main" / "sessions"
    identifier = (tmp_path / "session").read_text()
    store = TranscriptStore(root, owner_group="main")
    encoded = json.loads((tmp_path / "before.json").read_text())
    before = {name: base64.b64decode(value) for name, value in encoded.items()}
    if point == "marker-after":
        assert store.get_active_session("main").turn_count == 1
        assert len(store.load_transcript(identifier)) == 4
    else:
        assert {name: (root / name).read_bytes() for name in before} == before
        assert store.get_active_session("main").turn_count == 0

    def no_model(*args):
        raise AssertionError("Process recovery automatically reran an already executed model/tool")

    app = Orchestrator(ClawConfig(data_dir=str(tmp_path / "data")), model="test/process", stream_fn=no_model)
    app.register_group(Group(id="main", workspace=str(tmp_path / "workspace"), enabled_tools=("bash",)))
    try:
        await app.start()
        scope = InputScope("main", tmp_path / "workspace")
        record = await (await app.input_service()).store.get(scope, "process")
        assert record.status == "interrupted" and record.attempt_id
        assert app._runtimes["main"]._active_session is None
        assert (tmp_path / "model.log").read_text().splitlines() == ["model", "model"]
        assert (tmp_path / "workspace" / "effect.log").read_text() == "effect\n"
    finally:
        await app.stop()
