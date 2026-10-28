"""Tests for transcript store (formerly SessionManager)."""
import pytest
from xdog.claw.core.persistence.transcript_store import TranscriptStore


@pytest.fixture
def store(tmp_path):
    return TranscriptStore(tmp_path / "sessions")

def test_append_turn_and_load(store):
    meta = store.create_session("g1")
    store.append_turn(meta.session_id, {"role": "user", "content": "hello"})
    store.append_turn(meta.session_id, {"role": "assistant", "content": "hi"})
    turns = store.load_transcript(meta.session_id)
    assert len(turns) == 2
    assert turns[0]["content"] == "hello"

def test_reset_session_creates_new(store):
    old = store.create_session("g1")
    store.append_turn(old.session_id, {"role": "user", "content": "hello"})
    new = store.reset_session("g1")
    assert new.session_id != old.session_id
    handoff = store.load_transcript(new.session_id)[0]
    assert handoff['type'] == 'xdog.claw.handoff.v1'
    assert handoff['data']['source_session_id'] == old.session_id
    assert handoff['data']['recent'][0]['text'] == 'hello'
    assert handoff['data']['status'] == 'recent_only'
    assert store.load_transcript(old.session_id) == [{'role': 'user', 'content': 'hello'}]

# Backward compatibility alias
