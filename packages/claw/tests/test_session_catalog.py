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
    assert reloaded.get_active_session("one").session_id == second.session_id
    assert reloaded.get_active_session("two").session_id == foreign.session_id
    assert not hasattr(reloaded, 'activate_session')
    assert reloaded.load_transcript(first.session_id)[0]["content"] == "first"


def test_owned_legacy_archives_recover_but_shared_store_does_not_guess(tmp_path):
    (tmp_path / "legacy-id.jsonl").write_text('{"role":"user","content":"legacy"}\n')
    shared = TranscriptStore(tmp_path)
    assert shared.list_sessions("main") == []
    owned = TranscriptStore(tmp_path, owner_group="main")
    assert owned.list_sessions("main")[0]["session_id"] == "legacy-id"
    assert owned.load_transcript("legacy-id")[0]['content'] == 'legacy'
    assert owned.get_active_session('main') is None
    with pytest.raises(ValueError):
        owned.load_transcript("../../other")


def test_new_context_resets_daily_clock_without_activating_old_history(tmp_path):
    store = TranscriptStore(tmp_path)
    first = store.create_session("main")
    old = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=4)
    store._index["main"]["created_at"] = old.isoformat()
    store._save_index()
    assert store.needs_daily_reset("main")
    fresh = store.reset_session("main")
    assert fresh.session_id != first.session_id
    assert not store.needs_daily_reset("main")
    store.increment_turn(fresh.session_id)
    assert not store.needs_daily_reset("main")


@pytest.mark.parametrize('action', ['resume', 'fork', 'branch', 'goto'])
def test_group_historical_routes_are_read_only_and_never_change_active_context(tmp_path, action):
    runtime = GroupRuntime(group=Group(id="main"), data_dir=tmp_path, model="test/dummy")
    store = runtime.transcript_store
    meta = store.create_session("main")
    transcript = [{"role": "user", "content": "first"}, {"role": "assistant", "content": "answer"}]
    store.replace_transcript(meta.session_id, transcript)
    # Historical fixture, not a current write/activate branch API.
    import json
    store._path(meta.session_id, 'legacy').write_text(
        ''.join(json.dumps(row) + '\n' for row in transcript))
    before = store.branch_metadata(meta.session_id)
    with pytest.raises(ValueError, match="read-only"):
        runtime.navigate_session(action, meta.session_id, "legacy", 1, meta.session_id)
    assert store.get_active_session("main").session_id == meta.session_id
    assert store.load_transcript(meta.session_id) == transcript
    assert store.branch_metadata(meta.session_id) == before
    assert runtime._active_session is None
    assert not hasattr(runtime, '_navigate_prepared')
    entries = runtime.navigation_entries(f"tree|{meta.session_id}|")
    assert any(row["kind"] == "checkpoint" for row in entries)
    assert all('Fork/navigate' not in row.get('detail', '') for row in entries)
    with pytest.raises(ValueError, match="changed"):
        runtime.navigate_session("new", "", "", -1, "wrong-active")
