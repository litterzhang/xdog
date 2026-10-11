from __future__ import annotations

import datetime

import pytest
from xdog.claw.core.persistence.transcript_store import TranscriptStore
from xdog.claw.core.runtime.group import GroupRuntime
from xdog.claw.core.types import Group


def test_catalog_retains_archives_scopes_ownership_and_survives_reload(tmp_path):
    store = TranscriptStore(tmp_path, owner_group="one")
    first = store.create_session("one")
    store.append_turn(first.session_id, {"role": "user", "content": "first"})
    store.increment_turn(first.session_id)
    second = store.reset_session("one")
    foreign = store.create_session("two")
    reloaded = TranscriptStore(tmp_path, owner_group="one")
    assert {data["session_id"] for data in reloaded.list_sessions("one")} == {first.session_id, second.session_id}
    with pytest.raises(ValueError, match="another group"):
        reloaded.activate_session("one", foreign.session_id)
    reloaded.activate_session("one", first.session_id)
    assert reloaded.get_active_session("one").session_id == first.session_id
    assert reloaded.load_transcript(first.session_id)[0]["content"] == "first"


def test_owned_legacy_archives_recover_but_shared_store_does_not_guess(tmp_path):
    (tmp_path / "legacy-id.jsonl").write_text('{"role":"user","content":"legacy"}\n')
    shared = TranscriptStore(tmp_path)
    assert shared.list_sessions("main") == []
    owned = TranscriptStore(tmp_path, owner_group="main")
    assert owned.list_sessions("main")[0]["session_id"] == "legacy-id"
    assert owned.activate_session("main", "legacy-id").session_id == "legacy-id"
    with pytest.raises(ValueError):
        owned.load_transcript("../../other")


def test_explicit_resume_does_not_immediately_daily_reset_again(tmp_path):
    store = TranscriptStore(tmp_path)
    first = store.create_session("main")
    old = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=4)
    store._index["main"]["created_at"] = old.isoformat()
    store._save_index()
    assert store.needs_daily_reset("main")
    store.activate_session("main", first.session_id)
    assert not store.needs_daily_reset("main")
    store.increment_turn(first.session_id)
    assert not store.needs_daily_reset("main")


def test_group_branch_navigation_saves_return_head_and_rejects_invalid_before_switch(tmp_path):
    group = Group(id="main")
    runtime = GroupRuntime(group=group, data_dir=tmp_path, model="test/dummy")
    store = runtime.transcript_store
    meta = store.create_session("main")
    store.replace_transcript(meta.session_id, [
        {"role": "user", "content": "first"}, {"role": "assistant", "content": "answer"},
        {"role": "user", "content": "later"},
    ])
    runtime.navigate_session("goto", meta.session_id, "", 1, meta.session_id)
    assert len(store.load_transcript(meta.session_id)) == 2
    branches = store.branch_metadata(meta.session_id)
    backup = next(data for data in branches if data.get("label", "").startswith("Return"))
    runtime.navigate_session("branch", meta.session_id, backup["branch_id"], -1, meta.session_id)
    assert len(store.load_transcript(meta.session_id)) == 3
    assert store.active_branch(meta.session_id) == backup["branch_id"]
    with pytest.raises(ValueError, match="changed"):
        runtime.navigate_session("new", "", "", -1, "wrong-active")
    assert store.get_active_session("main").session_id == meta.session_id
    assert any(row["kind"] == "checkpoint" for row in runtime.navigation_entries(f"tree|{meta.session_id}|"))
