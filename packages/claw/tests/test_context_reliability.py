"""Context availability is not an empty history or permission to replace its owner."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from xdog.claw.core.persistence.session_preparation import check, history, reset
from xdog.claw.core.persistence.transcript_store import TranscriptStore


@pytest.mark.parametrize("archived", [False, True])
def test_missing_known_transcript_is_unavailable_and_preserves_current_pointer(tmp_path, archived):
    store = TranscriptStore(tmp_path, owner_group="main")
    original = store.create_session("main")
    store.append_turn(original.session_id, {"role": "user", "content": "remember this"})
    if archived:
        store.create_session("main")
    (tmp_path / f"{original.session_id}.jsonl").unlink()
    before = (tmp_path / "sessions.json").read_bytes()
    reopened = TranscriptStore(tmp_path, owner_group="main")
    with pytest.raises(ValueError, match="unavailable"):
        history(reopened, original.session_id)
    assert (tmp_path / "sessions.json").read_bytes() == before
    assert not (tmp_path / f"{original.session_id}.jsonl").exists()


def test_unknown_transcript_is_not_an_empty_context(tmp_path):
    store = TranscriptStore(tmp_path)
    with pytest.raises(ValueError, match="unavailable"):
        store.load_transcript("unknown")


def test_existing_empty_context_remains_valid(tmp_path):
    store = TranscriptStore(tmp_path)
    meta = store.create_session("main")
    reopened = TranscriptStore(tmp_path)
    assert reopened.load_transcript(meta.session_id) == []
    assert history(reopened, meta.session_id).messages == ()
    assert reopened.get_active_session("main") == meta


@pytest.mark.parametrize("damage", ["directory", "fifo", "json", "row", "utf8", "permission"])
def test_unreadable_or_malformed_context_is_explicitly_unavailable(tmp_path, monkeypatch, damage):
    store = TranscriptStore(tmp_path)
    meta = store.create_session("main")
    path = tmp_path / f"{meta.session_id}.jsonl"
    if damage in ("directory", "fifo"):
        path.unlink()
        path.mkdir() if damage == "directory" else os.mkfifo(path)
    elif damage == "json":
        path.write_text('{"role":')
    elif damage == "row":
        path.write_text('[]\n')
    elif damage == "utf8":
        path.write_bytes(b"\xff\n")
    else:
        # chmod cannot model denial when tests run as root. Inject only the OS
        # failure; keep the actual reader/error translation under test.
        actual = os.open

        def denied(file, *args, **kwargs):
            if Path(file) == path:
                raise PermissionError("storage denied")
            return actual(file, *args, **kwargs)

        monkeypatch.setattr(os, "open", denied)
    with pytest.raises(ValueError, match="unavailable"):
        history(store, meta.session_id)
    assert store.get_active_session("main") == meta


@pytest.mark.parametrize("action", ["automatic", "explicit", "direct"])
def test_missing_current_context_cannot_be_hidden_by_rotation(tmp_path, action):
    store = TranscriptStore(tmp_path)
    meta = store.create_session("main")
    store._index["main"]["created_at"] = "2020-01-01T00:00:00+00:00"
    store._save_index()
    meta = store.get_active_session("main")
    path = tmp_path / f"{meta.session_id}.jsonl"
    path.unlink()
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    with pytest.raises(ValueError, match="unavailable"):
        if action == "automatic":
            check(store, "main", 4, 0, allow_reset=True)
        elif action == "explicit":
            reset(store, "main", meta)
        else:
            store.reset_session("main")
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()} == before
    assert not path.exists()


def test_known_missing_checkpoint_is_unavailable_but_unknown_branch_remains_absent(tmp_path):
    store = TranscriptStore(tmp_path)
    meta = store.create_session("main")
    rows = [{"role": "user", "content": "original"}]
    store.save_compaction_checkpoint(meta.session_id, "checkpoint", rows, metadata={})
    (tmp_path / f"{meta.session_id}_branch_checkpoint.jsonl").unlink()
    with pytest.raises(ValueError, match="unavailable"):
        store.load_branch(meta.session_id, "checkpoint")
    assert store.load_branch(meta.session_id, "unknown") is None


@pytest.mark.parametrize("field", ["created_at", "last_active", "resumed_at"])
def test_naive_index_timestamps_are_rejected_without_rewriting_original_data(tmp_path, field):
    store = TranscriptStore(tmp_path)
    store.create_session("main")
    path = tmp_path / "sessions.json"
    value = json.loads(path.read_text())
    value["main"][field] = "2026-10-20T10:00:00"
    path.write_text(json.dumps(value))
    before = path.read_bytes()
    with pytest.raises(ValueError, match="index"):
        TranscriptStore(tmp_path)
    assert path.read_bytes() == before


@pytest.mark.parametrize("timestamp", ["bad", 123, ""])
def test_present_invalid_resume_timestamp_is_rejected(tmp_path, timestamp):
    store = TranscriptStore(tmp_path)
    store.create_session("main")
    path = tmp_path / "sessions.json"
    value = json.loads(path.read_text())
    value["main"]["resumed_at"] = timestamp
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="index"):
        TranscriptStore(tmp_path)


@pytest.mark.parametrize("offset", ["+00:00", "+08:00", "-05:30", "Z"])
def test_zoned_index_timestamps_remain_usable_by_reset_predicates(tmp_path, offset):
    store = TranscriptStore(tmp_path)
    store.create_session("main")
    path = tmp_path / "sessions.json"
    value = json.loads(path.read_text())
    for key in ("created_at", "last_active", "resumed_at"):
        value["main"][key] = "2020-01-01T00:00:00" + offset
    path.write_text(json.dumps(value))
    before = path.read_bytes()
    reopened = TranscriptStore(tmp_path)
    assert reopened.needs_daily_reset("main") is True
    assert reopened.needs_idle_reset("main", 60) is True
    assert path.read_bytes() == before


@pytest.mark.parametrize("allow_reset", [False, True])
async def test_cached_runtime_cannot_admit_after_its_transcript_disappears(tmp_path, allow_reset):
    from xdog.ai.types import AssistantMessage
    from xdog.ai.utils.event_stream import EventStream
    from xdog.claw.core.runtime.group import GroupRuntime
    from xdog.claw.core.types import Group

    runtime = GroupRuntime(group=Group(id="main"), data_dir=tmp_path,
                           model="test/dummy", stream_fn=lambda *args: EventStream.empty(AssistantMessage()))
    session = await runtime.prepare_session(allow_reset=False)
    (runtime.transcript_store._sessions_dir / f"{session.meta.session_id}.jsonl").unlink()
    try:
        with pytest.raises(ValueError, match="unavailable"):
            await runtime.prepare_session(allow_reset=allow_reset)
        assert runtime._active_session is session
    finally:
        session.dispose()
        await session.children.runtime.aclose()
        await session.extensions.aclose()
        await runtime.mcp.aclose()


async def test_failed_history_restore_cannot_make_the_next_attempt_silently_empty(tmp_path):
    from xdog.ai.types import UserMessage

    from packages.claw.tests.test_active_tool_persistence import close, create

    runtime, calls = create(tmp_path)
    session = runtime.get_or_create_session(allow_reset=False)
    path = runtime.transcript_store._path(session.meta.session_id)
    path.unlink()
    try:
        for _ in range(2):
            with pytest.raises(ValueError, match="unavailable"):
                session._restore_messages()
        assert not calls and session.agent.messages == ()
        # A deliberate external repair of this temporary fixture permits a real
        # restore; the reader never creates a replacement file on its own.
        path.write_text('{"role":"user","content":"original remembered input"}\n')
        session._restore_messages()
        assert session.agent.messages == (UserMessage(content="original remembered input"),)
    finally:
        await close(runtime)


@pytest.mark.parametrize("row", [
    {"role": "user", "content": 42},
    {"role": "user", "content": [42]},
    {"role": "user", "content": [{"type": "text", "text": 42}]},
    {"role": "assistant", "content": [{"type": "toolCall", "arguments": 42}]},
    {"role": "user", "content": [{"type": "unknown", "text": "cannot drop this"}]},
    {},
])
@pytest.mark.parametrize("action", ["read", "automatic", "explicit"])
def test_unusable_message_structure_cannot_be_hidden_by_reset(tmp_path, row, action):
    store = TranscriptStore(tmp_path, owner_group="main")
    meta = store.create_session("main")
    store._index["main"]["created_at"] = "2020-01-01T00:00:00+00:00"
    store._save_index()
    store._path(meta.session_id).write_text(json.dumps(row) + "\n")
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    with pytest.raises(ValueError, match="unavailable"):
        if action == "read":
            history(store, meta.session_id)
        elif action == "automatic":
            check(store, "main", 4, 0, allow_reset=True)
        else:
            store.reset_session("main")
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()} == before


def test_arbitrary_existing_file_is_not_a_registered_context(tmp_path):
    store = TranscriptStore(tmp_path)
    path = tmp_path / "unknown.jsonl"
    path.write_text("")
    with pytest.raises(ValueError, match="unavailable"):
        store.load_transcript("unknown")
    assert path.read_bytes() == b"" and store.list_sessions("main") == []


async def test_missing_known_checkpoint_remains_discoverable_and_timeline_reports_unavailable(tmp_path):
    import threading

    from xdog.claw.core.runtime.transcript_source import source_records

    from packages.claw.tests.test_active_tool_persistence import close, create

    runtime, calls = create(tmp_path)
    store = runtime.transcript_store
    session = runtime.get_or_create_session(allow_reset=False)
    identifier = session.meta.session_id
    store.save_compaction_checkpoint(identifier, "recovery", [{"role": "user", "content": "before"}], metadata={})
    store._path(identifier, "recovery").unlink()
    try:
        assert store.list_branches(identifier) == ["recovery"]
        assert store.branch_metadata(identifier)[0]["branch_id"] == "recovery"
        capture = runtime.capture_saved_source(identifier, identifier, "@timeline")
        with pytest.raises(ValueError, match="unavailable"):
            source_records(capture, threading.Event())
        assert not calls and runtime.get_new_session_id() == identifier
    finally:
        await close(runtime)
