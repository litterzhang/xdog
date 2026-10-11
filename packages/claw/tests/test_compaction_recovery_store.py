"""Durable recovery indexing cannot overwrite earlier context or omit an unsaved live tail."""

import pytest
from xdog.ai.types import AssistantMessage, TextContent, ToolCall, ToolResultMessage, UserMessage
from xdog.claw.core.persistence.transcript_convert import (
    live_transcript_snapshot,
    messages_to_transcript,
    transcript_to_messages,
)
from xdog.claw.core.persistence.transcript_store import TranscriptStore


def test_snapshot_keeps_stored_metadata_and_complete_unsaved_tail():
    original = (UserMessage(content='stored user'), AssistantMessage(content=(TextContent(text='stored answer'),)))
    stored = messages_to_transcript(original)
    stored[0]['channel'] = 'original-channel'
    stored[1]['usage']['input'] = 123  # A persisted fallback measurement, not an actual model measurement.
    tail = (UserMessage(content='unsaved user'),
            AssistantMessage(content=(ToolCall(id='new', name='read', arguments={'path': 'owned.py'}),)),
            ToolResultMessage(tool_call_id='new', tool_name='read', content=(TextContent(text='live result'),)),
            AssistantMessage(content=(TextContent(text='unsaved answer'),)))
    captured = live_transcript_snapshot((*original, *tail), stored)
    assert captured[:2] == stored and len(captured) == 6
    assert 'unsaved user' in str(captured) and 'live result' in str(captured)
    assert len(transcript_to_messages(captured)) == 6


def test_changed_live_row_never_reuses_stale_stored_payload():
    old = messages_to_transcript([UserMessage(content='old')])
    actual = live_transcript_snapshot((UserMessage(content='current live body'),), old)
    assert 'current live body' in str(actual) and actual != old


def test_checkpoint_is_indexed_private_complete_and_create_only(tmp_path):
    store = TranscriptStore(tmp_path, owner_group='main')
    meta = store.create_session('main')
    original = messages_to_transcript([UserMessage(content='all original data'), AssistantMessage()])
    store.replace_transcript(meta.session_id, original)
    metadata = {'label': 'Before compaction #1', 'recovery_kind': 'compaction', 'branch_point': 1}
    store.save_compaction_checkpoint(meta.session_id, 'compaction-unique', original, metadata=metadata)
    reopened = TranscriptStore(tmp_path, owner_group='main')
    assert reopened.load_branch(meta.session_id, 'compaction-unique') == original
    assert reopened.branch_metadata(meta.session_id)[0]['recovery_kind'] == 'compaction'
    assert reopened.load_transcript(meta.session_id) == original
    with pytest.raises(ValueError, match='no overwrite'):
        reopened.save_compaction_checkpoint(meta.session_id, 'compaction-unique', [], metadata=metadata)
    assert reopened.load_branch(meta.session_id, 'compaction-unique') == original


def test_checkpoint_failure_cannot_publish_success_or_replace_current_context(tmp_path, monkeypatch):
    store = TranscriptStore(tmp_path, owner_group='main')
    meta = store.create_session('main')
    original = [{'role': 'user', 'content': 'retained original'}]
    store.replace_transcript(meta.session_id, original)
    original_atomic = store._atomic
    def fail_branch(path, value, **kwargs):
        if path.name.endswith('branch_compaction-fail.jsonl'):
            raise OSError('offline checkpoint failure')
        return original_atomic(path, value, **kwargs)
    monkeypatch.setattr(store, '_atomic', fail_branch)
    with pytest.raises(OSError):
        store.save_compaction_checkpoint(meta.session_id, 'compaction-fail', original,
                                         metadata={'label': 'Before compaction'})
    assert store.load_transcript(meta.session_id) == original
    assert not store.branch_metadata(meta.session_id)
