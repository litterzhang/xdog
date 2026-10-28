"""Rotation keeps explicit choices and evidence, never old execution authority."""
from __future__ import annotations

import json

import pytest
from xdog.agent.access import AccessState
from xdog.claw.core.persistence.session_preparation import check, reset
from xdog.claw.core.persistence.transcript_store import TranscriptStore


@pytest.mark.parametrize("mode", ["explicit", "automatic", "direct"])
@pytest.mark.parametrize("tools", [(), ("filesystem",)])
def test_context_rotation_inherits_choices_but_not_context_state(tmp_path, mode, tools):
    store = TranscriptStore(tmp_path)
    first = store.create_session("main")
    identifier = first.session_id
    store.save_model(identifier, "test/chosen")
    store.save_reasoning(identifier, "test/chosen", "high")
    store.save_active_tools(identifier, tools)
    store.save_active_skills(identifier, ("goal",))
    store.save_extension_entries(identifier, {"opaque": [{"old_cursor": 42}]})
    store.save_access(identifier, AccessState(False, 7).to_dict())
    store.append_turn(identifier, {"role": "user", "content": "old conversation"})
    store.increment_turn(identifier)
    store._index["main"]["created_at"] = "2020-01-01T00:00:00+00:00"
    store._save_index()
    previous = store.get_active_session("main")
    if mode == "automatic":
        assert check(store, "main", 4, 0, allow_reset=True).reset_due
    new = reset(store, "main", previous) if mode != "direct" else store.reset_session("main")
    reopened = TranscriptStore(tmp_path)
    assert new.session_id != identifier and new.turn_count == 0 and new.label == ""
    assert reopened.get_active_session("main") == new
    handoff = reopened.load_transcript(new.session_id)[0]
    assert handoff['type'] == 'xdog.claw.handoff.v1'
    assert handoff['data']['source_session_id'] == identifier
    assert 'old conversation' in str(handoff['data']['recent'])
    assert reopened.get_model(new.session_id) == "test/chosen"
    assert reopened.get_reasoning(new.session_id, "test/chosen") == "high"
    assert reopened.get_reasoning(new.session_id, "test/other") is None
    assert reopened.get_active_tools(new.session_id) == tools
    assert reopened.get_active_skills(new.session_id) == ("goal",)
    assert reopened.get_extension_entries(new.session_id) == {}
    assert reopened.get_access(new.session_id) == {"read_only": False, "revision": 7}
    assert reopened.load_transcript(identifier)[0]["content"] == "old conversation"


@pytest.mark.parametrize("suffix,invalid", [
    ("model", {"model": ""}),
    ("thinking", {"model": "test/chosen", "thinking": "invalid"}),
    ("tools", {"active": 42}),
    ("skills", {"active": ["../bad"]}),
])
def test_invalid_preference_cannot_be_hidden_by_context_rotation(tmp_path, suffix, invalid):
    store = TranscriptStore(tmp_path)
    first = store.create_session("main")
    (tmp_path / f"{first.session_id}.{suffix}.json").write_text(json.dumps(invalid))
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    with pytest.raises(ValueError):
        store.reset_session("main")
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()} == before
    assert store.get_active_session("main") == first


@pytest.mark.parametrize("suffix", ["model", "thinking", "tools", "skills", "catalog", "sessions"])
def test_rotation_preference_failure_rolls_back_all_files_and_pointer(tmp_path, monkeypatch, suffix):
    store = TranscriptStore(tmp_path)
    first = store.create_session("main")
    store.save_model(first.session_id, "test/chosen")
    store.save_reasoning(first.session_id, "test/chosen", "high")
    store.save_active_tools(first.session_id, ())
    store.save_active_skills(first.session_id, ("goal",))
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    actual = store._atomic

    def fail(path, value, **kwargs):
        actual(path, value, **kwargs)
        if path.name.endswith(f".{suffix}.json") or path.name == f"{suffix}.json":
            raise OSError("injected rotation commit failure")

    monkeypatch.setattr(store, "_atomic", fail)
    with pytest.raises(OSError, match="injected"):
        store.reset_session("main")
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()} == before
    reopened = TranscriptStore(tmp_path)
    assert reopened.get_active_session("main") == first
    assert reopened.get_model(first.session_id) == "test/chosen"


@pytest.mark.parametrize("rotation", ["daily", "idle", "explicit"])
async def test_application_rotation_uses_selected_model_reasoning_skills_and_tools(tmp_path, rotation):
    from xdog.ai.types import AssistantMessage, Model, TextContent
    from xdog.ai.utils.event_stream import EventStream
    from xdog.claw.config import ClawConfig
    from xdog.claw.core.runtime.orchestrator import Orchestrator
    from xdog.claw.core.types import Group, UserInput

    calls = []

    class Stream:
        def resolve_model(self, name):
            assert name in {"test/default", "test/chosen"}
            return Model(id=name, context_window=100_000 if name == "test/default" else 64_000,
                         reasoning=True, supported_efforts=("low", "high"))

        def models(self):
            return [self.resolve_model("test/default"), self.resolve_model("test/chosen")]

        def __call__(self, model, context, options):
            calls.append((model, context, options))
            return EventStream.empty(AssistantMessage(content=(TextContent(text="offline reply"),)))

    app = Orchestrator(ClawConfig(data_dir=str(tmp_path / "data")), model="test/default", stream_fn=Stream())
    app.register_group(Group(id="main", workspace=str(tmp_path / "workspace"), enabled_tools=("current_time",)))
    runtime = app._runtimes["main"]
    skill = runtime.workspace_dir / "skills" / "focus"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: focus\ndescription: local context test\n---\nKeep choices.")
    try:
        await app.start()
        await app.route_message(UserInput(group_id="main", content="old context"))
        original = runtime.get_new_session_id()
        await app.extension_models("main", original, "test/chosen")
        await app.reasoning("main", original, "high")
        await app.skills_control("main", original, "load", "focus")
        session = runtime._active_session
        await session.select_active_tools((), expected_revision=session.agent.tool_selection_revision)
        if rotation == "explicit":
            await app.navigate_session("main", "new", "", "", -1, original)
        else:
            runtime.transcript_store._index["main"]["created_at"] = "2020-01-01T00:00:00+00:00"
            if rotation == "idle":
                from dataclasses import replace
                runtime.group = replace(runtime.group, config=replace(runtime.group.config, idle_reset_seconds=1))
                runtime.transcript_store._index["main"]["created_at"] = session.meta.created_at
                runtime.transcript_store._index["main"]["last_active"] = "2020-01-01T00:00:00+00:00"
            runtime.transcript_store._save_index()
            # Automatic rotation is the normal prepare route; no control path.
        fresh = await runtime.prepare_session()
        await fresh.initialize_extensions()
        assert fresh.meta.session_id != original
        assert fresh.agent.state.model == "test/chosen" and fresh.agent.options.thinking == "high"
        assert fresh.agent.context_window == runtime.context_window == 64_000
        assert fresh.agent.active_tool_selection == () and fresh.agent.active_skills == ("focus",)
        handoff = runtime.transcript_store.load_transcript(fresh.meta.session_id)[0]
        assert handoff['type'] == 'xdog.claw.handoff.v1'
        assert handoff['data']['source_session_id'] == original
        assert len(calls) == 2  # Original turn + one tool-free handoff summary; no task replay.
        assert calls[-1][0] == 'test/chosen' and calls[-1][2].thinking == 'high'
        assert not calls[-1][1].tools
    finally:
        await app.stop()


PREFERENCE_CRASH = r'''
import os, sys
from pathlib import Path
from xdog.claw.core.persistence.transcript_store import TranscriptStore
import xdog.claw.core.persistence.navigation_journal as journal
root, stage = Path(sys.argv[1]), sys.argv[2]
store = TranscriptStore(root, owner_group='main')
atomic, manifest = store._atomic, journal.write_manifest
count = 0
if stage == 'committed':
    def die(directory, value):
        manifest(directory, value)
        if value['phase'] == 'committed':
            os._exit(72)
    journal.write_manifest = die
else:
    def die(path, value, **kwargs):
        global count
        atomic(path, value, **kwargs)
        count += 1
        if count == int(stage):
            os._exit(71)
    store._atomic = die
store.reset_session('main')
'''


@pytest.mark.parametrize("stage", ["2", "5", "7", "committed"])
def test_process_death_cannot_publish_new_pointer_without_inherited_preferences(tmp_path, stage):
    import subprocess
    import sys

    store = TranscriptStore(tmp_path, owner_group="main")
    original = store.create_session("main")
    store.save_model(original.session_id, "test/chosen")
    store.save_reasoning(original.session_id, "test/chosen", "high")
    store.save_active_tools(original.session_id, ())
    store.save_active_skills(original.session_id, ("goal",))
    store.save_access(original.session_id, AccessState(True, 9).to_dict())
    child = subprocess.run([sys.executable, "-c", PREFERENCE_CRASH, str(tmp_path), stage],
                           capture_output=True, text=True, timeout=15)
    assert child.returncode == (72 if stage == "committed" else 71), child.stderr
    reopened = TranscriptStore(tmp_path, owner_group="main")
    actual = reopened.get_active_session("main")
    assert (actual.session_id != original.session_id) is (stage == "committed")
    assert reopened.get_model(actual.session_id) == "test/chosen"
    assert reopened.get_reasoning(actual.session_id, "test/chosen") == "high"
    assert reopened.get_active_tools(actual.session_id) == ()
    assert reopened.get_active_skills(actual.session_id) == ("goal",)
    assert reopened.get_access(actual.session_id) == {"read_only": True, "revision": 9}
