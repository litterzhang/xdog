"""Compaction commits complete recoverable facts, not partially replaced history."""
from __future__ import annotations

import subprocess
import sys

import pytest
from xdog.claw.core.persistence.navigation_journal import JOURNAL
from xdog.claw.core.persistence.transcript_store import TranscriptStore

ORIGINAL = [{"role": "user", "content": "complete original conversation"}]
COMPACTED = [{"role": "user", "content": "summary", "is_compaction": True}]


def snapshot(root):
    return {p.name: p.read_bytes() for p in root.iterdir() if p.is_file() and not p.name.startswith(".")}


def seeded(root):
    store = TranscriptStore(root, owner_group="main")
    meta = store.create_session("main")
    store.replace_transcript(meta.session_id, ORIGINAL)
    return store, meta.session_id


@pytest.mark.parametrize("point", ["tools", "extensions", "access", "transcript", "catalog"])
@pytest.mark.parametrize("when", ["before", "after"])
def test_failed_checkpoint_write_leaves_no_partial_archive_or_cached_metadata(tmp_path, monkeypatch, point, when):
    store, identifier = seeded(tmp_path)
    before = snapshot(tmp_path)
    atomic = store._atomic

    def fault(path, value, **kwargs):
        target = (path == store._catalog_file if point == "catalog" else
                  path.name.endswith(".jsonl") if point == "transcript" else
                  path.name.endswith(f".{point}.json"))
        if target and when == "before":
            raise OSError("checkpoint failure")
        atomic(path, value, **kwargs)
        if target and when == "after":
            raise OSError("checkpoint failure")

    monkeypatch.setattr(store, "_atomic", fault)
    with pytest.raises(OSError, match="checkpoint failure"):
        store.save_compaction_checkpoint(identifier, "recovery", ORIGINAL, metadata={"recovery_kind": "compaction"})
    assert snapshot(tmp_path) == before
    assert store.branch_metadata(identifier) == []
    assert store.get_extension_entries(identifier, "recovery") == {}
    reopened = TranscriptStore(tmp_path, owner_group="main")
    assert reopened.load_transcript(identifier) == ORIGINAL
    assert reopened.branch_metadata(identifier) == []


def test_replacement_catalog_failure_keeps_original_and_valid_extra_checkpoint(tmp_path, monkeypatch):
    store, identifier = seeded(tmp_path)
    store.save_compaction_checkpoint(identifier, "recovery", ORIGINAL, metadata={"recovery_kind": "compaction"})
    before = snapshot(tmp_path)
    atomic = store._atomic

    def fault(path, value, **kwargs):
        atomic(path, value, **kwargs)
        if path == store._catalog_file:
            raise OSError("replacement catalog failure")

    monkeypatch.setattr(store, "_atomic", fault)
    with pytest.raises(OSError, match="replacement catalog failure"):
        store.replace_transcript(identifier, COMPACTED)
    assert snapshot(tmp_path) == before
    assert store._catalog["sessions"][identifier]["message_count"] == 1
    reopened = TranscriptStore(tmp_path, owner_group="main")
    assert reopened.load_transcript(identifier) == ORIGINAL
    assert reopened.load_branch(identifier, "recovery") == ORIGINAL


CRASH = r'''
import os, sys
from pathlib import Path
from xdog.claw.core.persistence.transcript_store import TranscriptStore
import xdog.claw.core.persistence.navigation_journal as journal
root, identifier, operation, stage = Path(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]
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
if operation == 'checkpoint':
    store.save_compaction_checkpoint(identifier, 'recovery',
        [{'role': 'user', 'content': 'complete original conversation'}], metadata={'recovery_kind': 'compaction'})
else:
    store.replace_transcript(identifier, [{'role': 'user', 'content': 'summary', 'is_compaction': True}])
'''


@pytest.mark.parametrize("operation,stage", [
    *(('checkpoint', str(i)) for i in range(1, 6)), ('checkpoint', 'committed'),
    ('replacement', '1'), ('replacement', '2'), ('replacement', 'committed'),
])
def test_real_process_recovery_keeps_old_or_fully_committed_compaction_without_replay(tmp_path, operation, stage):
    store, identifier = seeded(tmp_path)
    if operation == "replacement":
        store.save_compaction_checkpoint(identifier, "recovery", ORIGINAL, metadata={"recovery_kind": "compaction"})
    before = snapshot(tmp_path)
    child = subprocess.run([sys.executable, "-c", CRASH, str(tmp_path), identifier, operation, stage],
                           capture_output=True, text=True, timeout=15)
    assert child.returncode == (72 if stage == "committed" else 71), child.stderr
    assert (tmp_path / JOURNAL).exists()
    # Recovery is a store-only operation: there is no model/provider/Agent here.
    reopened = TranscriptStore(tmp_path, owner_group="main")
    if stage != "committed":
        assert snapshot(tmp_path) == before
    expected = COMPACTED if operation == "replacement" and stage == "committed" else ORIGINAL
    assert reopened.load_transcript(identifier) == expected
    if operation == "replacement" or stage == "committed":
        assert reopened.load_branch(identifier, "recovery") == ORIGINAL
        assert reopened.branch_metadata(identifier)[0]["recovery_kind"] == "compaction"
    else:
        assert reopened.branch_metadata(identifier) == []
    assert not (tmp_path / JOURNAL).exists()


@pytest.mark.parametrize("operation", ["checkpoint", "replacement"])
def test_failed_compaction_rollback_fences_owner_until_real_reopen(tmp_path, monkeypatch, operation):
    from xdog.claw.core.persistence import navigation_journal
    from xdog.claw.core.persistence.navigation_journal import NavigationRecoveryError

    store, identifier = seeded(tmp_path)
    if operation == "replacement":
        store.save_compaction_checkpoint(identifier, "recovery", ORIGINAL, metadata={"recovery_kind": "compaction"})
    before = snapshot(tmp_path)
    atomic, replace = store._atomic, navigation_journal.os.replace

    def fail_catalog(path, value, **kwargs):
        atomic(path, value, **kwargs)
        if path == store._catalog_file:
            raise OSError("compaction catalog unavailable")

    def fail_restore(source, destination):
        if ".navigation-restore-" in str(source):
            raise OSError("compaction rollback unavailable")
        return replace(source, destination)

    with monkeypatch.context() as patch:
        patch.setattr(store, "_atomic", fail_catalog)
        patch.setattr(navigation_journal.os, "replace", fail_restore)
        with pytest.raises(OSError, match="compaction rollback unavailable"):
            if operation == "checkpoint":
                store.save_compaction_checkpoint(identifier, "recovery", ORIGINAL, metadata={})
            else:
                store.replace_transcript(identifier, COMPACTED)
        with pytest.raises(NavigationRecoveryError):
            store.load_transcript(identifier)
        with pytest.raises(NavigationRecoveryError):
            store.create_session("main")
    reopened = TranscriptStore(tmp_path, owner_group="main")
    assert reopened.load_transcript(identifier) == ORIGINAL
    assert snapshot(tmp_path) == before
