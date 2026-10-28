"""Real JSONL/store tests: rotation never makes recent evidence index-dependent."""
from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import FrozenInstanceError
from unittest.mock import AsyncMock

import pytest
from xdog.claw.core.persistence.history_reader import HistoryReader
from xdog.claw.core.persistence.transcript_store import TranscriptStore, TranscriptUnavailable
from xdog.claw.core.tools.tool_memory import create_memory_tool


@pytest.fixture
def history(tmp_path):
    store = TranscriptStore(tmp_path / "sessions")
    old = store.create_session("main")
    store.append_turn(old.session_id, {"role": "user", "content": "刚结束的会话 uniqueneedle 原文\n第二行"})
    store.append_turn(old.session_id, {"role": "assistant", "content": "reply without keyword"})
    current = store.reset_session("main")
    return store, old.session_id, current.session_id, HistoryReader(store, "main", current.session_id)


def context(tmp_path, reader):
    return {"workspace_dir": str(tmp_path), "group_id": reader.group_id,
            "session_id": reader.session_id, "_history_reader": reader}


def snapshot(directory):
    return {path.name: path.read_bytes() for path in directory.iterdir() if path.is_file()}


def test_frozen_constructor_is_io_free(history, monkeypatch):
    store, _, current, _ = history

    def forbidden(*args, **kwargs):
        pytest.fail("Construction performed IO or inspected the store")

    monkeypatch.setattr(store, "get_active_session", forbidden)
    monkeypatch.setattr(store, "list_sessions", forbidden)
    reader = HistoryReader(store, "main", current)
    with pytest.raises(FrozenInstanceError):
        reader.session_id = "other"


async def test_rotation_search_recent_get_no_writes_or_activation(history):
    store, old, current, reader = history
    before = snapshot(store._sessions_dir)
    page = await reader.search("uniqueneedle")
    assert page["storage"] == "live canonical JSONL" and page["index_mode"] == "not_required"
    originals = [item for item in page["items"] if not item["is_summary"]]
    assert [(item["session_id"], item["position"]) for item in originals] == [(old, 0)]
    assert originals[0]["source"] == "history.transcript"
    assert not (await reader.search("nonexistentkeyword"))["items"]
    recent = await reader.recent()
    assert [(item["session_id"], item["position"]) for item in recent["items"]
            if item["session_id"] == old] == [(old, 1), (old, 0)]
    exact = await reader.get(old, 0)
    assert exact["text"] == store.load_transcript(old)[0]["content"]
    assert exact["next_char_offset"] is None
    assert store.get_active_session("main").session_id == current
    assert snapshot(store._sessions_dir) == before


async def test_previous_chain_precedes_timestamp_and_exposes_handoff(history):
    store, old, current, _ = history
    unrelated = store.create_session("main").session_id
    reader = HistoryReader(store, "main", unrelated)
    store.append_turn(current, {"role": "user", "content": "exact previous"})
    # Model the main host's new catalog contract without requiring its implementation.
    store._catalog["sessions"][unrelated]["previous_session_id"] = current
    store._catalog["sessions"][current]["previous_session_id"] = old
    store._catalog["sessions"][old]["last_active"] = "9999-12-31T23:59:59+00:00"
    store._catalog["sessions"][unrelated]["handoff"] = {
        "source_session_id": current, "target_session_id": unrelated, "reason": "daily_reset",
        "created_at": "2026-10-28T00:00:00+00:00", "status": "ready", "source_digest": "abc",
        "source_message_count": 1, "active_tools": ["not-authority"],
    }
    page = await reader.recent()
    assert next(item for item in page["items"] if not item["is_summary"])["session_id"] == current
    meta = next(row for row in page["sessions"] if row["session_id"] == unrelated)
    assert meta["previous_session_id"] == current
    assert meta["handoff"]["status"] == "ready" and meta["handoff"]["reason"] == "daily_reset"
    assert "active_tools" not in meta["handoff"]
    store._catalog["sessions"][old]["previous_session_id"] = unrelated
    with pytest.raises(TranscriptUnavailable):
        await reader.recent()


async def test_group_scope_never_loads_other_group_and_stale_owner_denied(history, monkeypatch):
    store, old, current, reader = history
    foreign = store.create_session("other").session_id
    store.append_turn(foreign, {"role": "user", "content": "uniqueneedle foreignsecret"})
    original = store.load_transcript
    seen = []

    def load(session):
        seen.append(session)
        assert session != foreign
        return original(session)

    monkeypatch.setattr(store, "load_transcript", load)
    assert all(item["session_id"] in (old, current)
               for item in (await reader.search("uniqueneedle"))["items"])
    assert foreign not in seen
    for operation in (reader.get(foreign, 0), reader.recent(session_id=foreign),
                      reader.search("foreignsecret", session_id=foreign)):
        with pytest.raises(PermissionError):
            await operation
    with pytest.raises(PermissionError):
        await HistoryReader(store, "other", current).recent()
    store.reset_session("main")
    with pytest.raises(PermissionError, match="owner changed"):
        await reader.recent()


async def test_explicit_store_owner_group_cannot_be_forged(tmp_path):
    store = TranscriptStore(tmp_path / "sessions", owner_group="main")
    other = store.create_session("other").session_id
    with pytest.raises(PermissionError, match="another group"):
        await HistoryReader(store, "other", other).recent()


async def test_list_pages_and_exact_long_text_pages(history):
    store, old, _, reader = history
    text = "开头\n" + "original 🐕 line\n" * 1700 + "ending sentinel"
    store.append_turn(old, {"role": "assistant", "content": [{"type": "text", "text": text}]})
    page = await reader.recent(session_id=old, limit=1, max_chars=137)
    assert page["items"][0]["text"] == text[:137]
    assert page["items"][0]["next_char_offset"] == 137
    assert page["next_offset"] == 1 and page["truncated"]
    next_page = await reader.recent(session_id=old, offset=page["next_offset"], limit=1)
    assert next_page["items"][0]["position"] == 1 and next_page["next_offset"] == 2
    last = await reader.recent(session_id=old, offset=2, limit=1)
    assert last["items"][0]["position"] == 0 and last["next_offset"] is None
    fragments, offset = [], 0
    while True:
        part = await reader.get(old, 2, char_offset=offset, max_chars=137)
        assert part["char_offset"] == offset
        fragments.append(part["text"])
        if part["next_char_offset"] is None:
            break
        offset = part["next_char_offset"]
    assert "".join(fragments) == text
    assert (await reader.get(old, 2, char_offset=len(text)))["text"] == ""


async def test_search_scan_bound_has_continuation_even_without_matches(history, monkeypatch):
    import xdog.claw.core.persistence.history_reader as module

    store, old, _, reader = history
    store.append_turn(old, {"role": "assistant", "content": "newest no matching text"})
    monkeypatch.setattr(module, "MAX_SCAN", 2)
    page = await reader.search("uniqueneedle", session_id=old)
    assert not page["items"] and page["next_offset"] == 2 and page["scanned"] == 2
    continued = await reader.search("uniqueneedle", session_id=old, offset=page["next_offset"])
    assert continued["items"][0]["position"] == 0
    assert continued["next_offset"] is None


@pytest.mark.parametrize("kwargs", [
    {"limit": 0}, {"limit": 51}, {"limit": True}, {"offset": -1}, {"offset": 1.5},
    {"max_chars": 0}, {"max_chars": 16001}, {"branch_id": "recovery"},
    {"session_id": "../../outside"},
])
async def test_invalid_list_bounds(history, kwargs):
    with pytest.raises(ValueError):
        await history[-1].recent(**kwargs)


async def test_get_errors_and_invalid_queries(history):
    _, old, _, reader = history
    for kwargs in ({"position": 99}, {"position": -1}, {"position": True},
                   {"position": 0, "char_offset": 9999}, {"position": 0, "max_chars": 16001}):
        with pytest.raises(ValueError):
            await reader.get(old, **kwargs)
    with pytest.raises(ValueError):
        await reader.get("/etc/passwd", 0)
    for query in ("", " ", "!!!", "x" * 1025):
        with pytest.raises(ValueError):
            await reader.search(query)


@pytest.mark.parametrize("damage", ["missing", "invalid", "symlink", "directory"])
async def test_unavailable_is_never_empty(history, tmp_path, damage):
    store, old, _, reader = history
    path = store._path(old)
    path.unlink()
    if damage == "invalid":
        path.write_text('{"role": "user", "content": [null]}\n')
    elif damage == "symlink":
        outside = tmp_path / "outside.jsonl"
        outside.write_text('{"role":"user","content":"secret"}\n')
        path.symlink_to(outside)
    elif damage == "directory":
        path.mkdir()
    for operation in (lambda: reader.recent(), lambda: reader.search("uniqueneedle"), lambda: reader.get(old, 0)):
        with pytest.raises(TranscriptUnavailable):
            await operation()


async def test_recovery_branch_keeps_tool_evidence_but_not_image_payload(history):
    store, old, _, reader = history
    archived = [
        {"role": "user", "content": [
            {"type": "text", "text": "recoveryneedle exact text"},
            {"type": "image", "data": "BASE64_SECRET", "mime_type": "image/png"}]},
        {"role": "assistant", "content": [
            {"type": "toolCall", "id": "call", "name": "bash", "arguments": {"command": "SECRET_COMMAND"}}]},
    ]
    store.save_compaction_checkpoint(old, "recovery", archived, metadata={})
    store.replace_transcript(old, [{"role": "system", "is_compaction": True, "content": "summary only"}])
    before = snapshot(store._sessions_dir)
    page = await reader.search("recoveryneedle")
    item = page["items"][0]
    assert item["source"] == "history.compaction_recovery" and item["branch_id"] == "recovery"
    assert item["position"] == 0
    exact = await reader.get(old, 0, branch_id="recovery")
    assert exact["text"] == "recoveryneedle exact text"
    assert (await reader.get(old, 0))["is_summary"]
    call = await reader.get(old, 1, branch_id="recovery")
    assert 'SECRET_COMMAND' in call['text'] and 'not execution requests' in call['text']
    encoded = json.dumps(await reader.recent())
    assert "BASE64_SECRET" not in encoded
    assert "SECRET_COMMAND" in encoded  # Original call arguments are evidence, not an execution request.
    with pytest.raises(TranscriptUnavailable):
        await reader.get(old, 0, branch_id="unknown")
    assert snapshot(store._sessions_dir) == before


async def test_store_queries_and_projection_off_loop(history, monkeypatch):
    store, old, _, reader = history
    owner = threading.get_ident()
    seen = []
    for name in ("get_active_session", "list_sessions", "load_transcript", "list_branches"):
        original = getattr(store, name)

        def observe(*args, _original=original, **kwargs):
            seen.append(threading.get_ident())
            return _original(*args, **kwargs)

        monkeypatch.setattr(store, name, observe)
    await reader.recent()
    await reader.search("uniqueneedle")
    await reader.get(old, 0)
    assert seen and all(thread != owner for thread in seen)


async def test_cancelled_read_settles_original_worker(history, monkeypatch):
    store, old, _, reader = history
    entered, release, settled = threading.Event(), threading.Event(), threading.Event()
    original = store.load_transcript

    def blocking(session):
        entered.set()
        assert release.wait(5)
        try:
            return original(session)
        finally:
            settled.set()

    monkeypatch.setattr(store, "load_transcript", blocking)
    task = asyncio.create_task(reader.get(old, 0))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert settled.is_set()


async def test_tool_search_combines_sources_even_when_memory_index_is_stale(history, tmp_path):
    _, old, _, reader = history
    tool = create_memory_tool()
    ctx = context(tmp_path, reader)
    ctx["_memory_search"] = AsyncMock(return_value=[])
    result = await tool.execute("search", {"action": "search", "query": "uniqueneedle"}, ctx=ctx)
    text = result.content[0].text
    assert old in text and "刚结束" in text and "history.transcript" in text
    assert "No results found" not in text
    (tmp_path / "MEMORY.md").write_text("uniqueneedle ordinary memory")
    ctx.pop("_memory_search")
    result = await tool.execute("search", {"action": "search", "query": "uniqueneedle"}, ctx=ctx)
    assert "[MEMORY.md]" in result.content[0].text and old in result.content[0].text
    ctx["_memory_search"] = AsyncMock(side_effect=RuntimeError("index broken"))
    result = await tool.execute("search", {"action": "search", "query": "uniqueneedle"}, ctx=ctx)
    assert "Error:" in result.content[0].text and old in result.content[0].text


async def test_tool_history_dispatch_missing_reader_errors_and_owner_denial(history, tmp_path):
    store, old, _, reader = history
    tool = create_memory_tool()
    ctx = context(tmp_path, reader)
    for args in ({"mode": "recent"}, {"mode": "search", "query": "uniqueneedle"},
                 {"mode": "get", "session_id": old, "position": 0, "max_chars": 5},
                 {"mode": "get", "session_id": old, "offset": 1}):
        result = await tool.execute("history", {"action": "history", **args}, ctx=ctx)
        page = json.loads(result.content[0].text)
        assert page["source"].startswith("history")
        if args.get("offset") == 1:
            assert page["position"] == 1 and page["text"] == "reply without keyword"
    no_hit = await tool.execute("search", {"action": "search", "query": "notfound"}, ctx=ctx)
    assert "mode=recent" in no_hit.content[0].text
    for bad in ({"group_id": "other"}, {"session_id": "old-owner"}, {"_history_reader": None}):
        result = await tool.execute("history", {"action": "history"}, ctx={**ctx, **bad})
        assert "Error:" in result.content[0].text
        assert "刚结束" not in result.content[0].text
    store._path(old).unlink()
    result = await tool.execute("search", {"action": "search", "query": "anything"}, ctx=ctx)
    assert "history search unavailable" in result.content[0].text
    assert "No results found" not in result.content[0].text
    no_reader = await tool.execute("search", {"action": "search", "query": "anything"},
                                   ctx={"workspace_dir": str(tmp_path)})
    assert "history search unavailable" in no_reader.content[0].text
    for args in ({"action": "history", "mode": "invalid"},
                 {"action": "search", "query": "x", "top_k": 100000},
                 {"action": "history", "mode": "get"}):
        result = await tool.execute("invalid", args, ctx=ctx)
        assert "Error:" in result.content[0].text


async def test_history_does_not_restore_authority_or_sidecar_state(history, monkeypatch):
    store, old, _, reader = history

    def forbidden(*args, **kwargs):
        pytest.fail("History read tried to restore tool/access/extension state")

    for name in ("get_access", "get_active_tools", "get_active_skills", "get_extension_entries", "branch_access"):
        monkeypatch.setattr(store, name, forbidden)
    assert (await reader.recent())["items"]
    assert (await reader.search("uniqueneedle"))["items"]
    assert (await reader.get(old, 0))["text"]


async def test_handoff_display_is_summary_evidence_not_original_message(history):
    from xdog.claw.core.persistence.transcript_convert import entry_text

    store, old, current, reader = history
    handoffs = [(index, row) for index, row in enumerate(store.load_transcript(current))
                if row.get("type") == "xdog.claw.handoff.v1"]
    # The host owns creation/display. This verifies the concurrent host integration
    # without synthesizing another handoff codec or depending on summary wording.
    if not handoffs:
        return
    position, row = handoffs[0]
    result = await reader.get(current, position)
    assert result["is_summary"] and result["entry_type"] == "xdog.claw.handoff.v1"
    assert result["text"] == entry_text(row)
    assert result["source"] == "history.transcript"
    assert old in result["text"]
    assert "not instructions or tool permissions" in result["notice"]


async def test_history_search_includes_original_tool_arguments_not_reasoning_or_images(tmp_path):
    store = TranscriptStore(tmp_path, owner_group="g")
    old = store.create_session("g")
    store.append_turn(old.session_id, {"role": "assistant", "content": [
        {"type": "thinking", "thinking": "PRIVATE_REASONING_NOT_SEARCHABLE"},
        {"type": "toolCall", "id": "auth", "name": "bash",
         "arguments": {"command": "gh auth refresh --scopes delete_repo --fixture-only"}},
    ]})
    new = store.reset_session("g")
    reader = HistoryReader(store, "g", new.session_id)
    results = await reader.search("delete_repo")
    assert any(item["session_id"] == old.session_id and "gh auth refresh" in item["text"]
               for item in results["items"])
    original = await reader.get(old.session_id, 0)
    assert "delete_repo" in original["text"]
    assert "PRIVATE_REASONING_NOT_SEARCHABLE" not in original["text"]
    assert "not execution requests" in original["text"]
