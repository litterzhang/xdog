"""Owned offline subprocess death at real managed-write and recovery boundaries."""
from __future__ import annotations

import json
import subprocess
import sys

import pytest
from xdog.agent.access import AccessState
from xdog.claw.core.persistence.navigation_journal import (
    JOURNAL,
    NavigationJournal,
    NavigationRecoveryError,
    ownership,
    recover,
)
from xdog.claw.core.persistence.transcript_store import TranscriptStore


def create(directory):
    store = TranscriptStore(directory, owner_group='main')
    original = store.create_session('main')
    store.replace_transcript(original.session_id, [{'role': 'user', 'content': 'original active'}])
    target = store.create_session('main')
    store.replace_transcript(target.session_id, [{'role': 'user', 'content': 'original target'}])
    store.save_active_tools(target.session_id, ('previously-selected',))
    store.save_extension_entries(target.session_id, {'fixture': [{'id': 'old', 'kind': 'note', 'data': 'old state'}]})
    store.activate_session('main', original.session_id)
    return store, original.session_id, target.session_id


def snapshot(directory):
    return {path.name: path.read_bytes() for path in directory.iterdir()
            if path.is_file() and not path.name.startswith('.')}


SCRIPT = r'''
import os, sys
from pathlib import Path
from xdog.agent.access import AccessState
from xdog.claw.core.persistence.transcript_store import TranscriptStore
import xdog.claw.core.persistence.navigation_journal as journal
root, target, kind, point = Path(sys.argv[1]), sys.argv[2], sys.argv[3], int(sys.argv[4])
store = TranscriptStore(root, owner_group='main')
count = 0
atomic = store._atomic
if kind == 'atomic':
    def crash(path, value, **kwargs):
        global count
        atomic(path, value, **kwargs)
        count += 1
        if count == point:
            os._exit(71)
    store._atomic = crash
elif kind == 'marker':
    original = journal.write_manifest
    def marker(directory, value):
        if value['phase'] == 'committed' and point == 0:
            os._exit(71)
        original(directory, value)
        if value['phase'] == 'committed' and point == 1:
            os._exit(72)
    journal.write_manifest = marker
store.commit_navigation('main', target, 'chosen', [{'role': 'user', 'content': 'committed target'}],
    AccessState(read_only=True, revision=1).to_dict(),
    {'fixture':[{'id':'new','kind':'note','data':'committed entry'}]},
    branches=[('return', [{'role':'user','content':'retained return head'}], {'label':'Return checkpoint'})],
    active_tools=())
print(count)
'''


@pytest.mark.parametrize('point', [1, 7, 13])
def test_real_process_death_at_transaction_boundaries_restores_exact_old_state(tmp_path, point):
    _, old, target = create(tmp_path)
    before = snapshot(tmp_path)
    child = subprocess.run([sys.executable, '-c', SCRIPT, str(tmp_path), target, 'atomic', str(point)],
                           capture_output=True, text=True, timeout=15)
    assert child.returncode == 71, (point, child.stdout, child.stderr)
    assert (tmp_path / JOURNAL).is_file()
    reopened = TranscriptStore(tmp_path, owner_group='main')
    assert snapshot(tmp_path) == before
    assert reopened.get_active_session('main').session_id == old
    assert reopened.load_transcript(target)[0]['content'] == 'original target'
    assert not list(tmp_path.glob('.navigation-backup-*')) and not (tmp_path / JOURNAL).exists()


def test_tampered_or_missing_backup_blocks_store_before_exposing_mixed_state(tmp_path):
    path = tmp_path / 'owned.jsonl'
    path.write_text('original')
    with ownership(tmp_path):
        NavigationJournal(tmp_path, {path})
    backup = next(tmp_path.glob('.navigation-backup-*'))
    path.write_text('partial new')
    backup.write_text('tampered')
    with pytest.raises(NavigationRecoveryError, match='checksum'):
        TranscriptStore(tmp_path)
    assert path.read_text() == 'partial new' and (tmp_path / JOURNAL).exists()


@pytest.mark.parametrize('bad', ['../outside.jsonl', '/outside.jsonl', '.hidden.jsonl'])
def test_manifest_cannot_write_outside_owned_managed_names(tmp_path, bad):
    path = tmp_path / 'owned.jsonl'
    path.write_text('original')
    with ownership(tmp_path):
        NavigationJournal(tmp_path, {path})
    manifest = json.loads((tmp_path / JOURNAL).read_text())
    manifest['files'][0]['name'] = bad
    (tmp_path / JOURNAL).write_text(json.dumps(manifest))
    with pytest.raises(NavigationRecoveryError):
        recover(tmp_path)
    assert path.read_text() == 'original'


def test_failed_recovery_retains_journal_and_blocks_live_store_until_reopen(tmp_path, monkeypatch):
    store, old, target = create(tmp_path)
    before = snapshot(tmp_path)
    atomic = store._atomic
    from xdog.claw.core.persistence import navigation_journal
    replace = navigation_journal.os.replace
    def fail_pointer(path, value, **kwargs):
        if path == store._index_file:
            raise OSError('injected pointer failure')
        return atomic(path, value, **kwargs)
    def fail_restore(source, destination):
        if '.navigation-restore-' in str(source):
            raise OSError('injected temporarily unavailable recovery storage')
        return replace(source, destination)
    with monkeypatch.context() as faults:
        faults.setattr(store, '_atomic', fail_pointer)
        faults.setattr(navigation_journal.os, 'replace', fail_restore)
        with pytest.raises(OSError, match='recovery storage'):
            store.commit_navigation('main', target, 'new', [{'role': 'user', 'content': 'new target'}],
                AccessState(read_only=False, revision=0).to_dict(), {}, branches=[])
        assert (tmp_path / JOURNAL).exists() and list(tmp_path.glob('.navigation-backup-*'))
        with pytest.raises(NavigationRecoveryError, match='recovery incomplete'):
            store.get_active_session('main')
        with pytest.raises(NavigationRecoveryError):
            store.load_transcript(target)
    reopened = TranscriptStore(tmp_path, owner_group='main')
    assert snapshot(tmp_path) == before and reopened.get_active_session('main').session_id == old


def test_committed_cleanup_failure_is_not_false_rollback_and_restarts_cleanly(tmp_path, monkeypatch):
    store, _, target = create(tmp_path)
    from xdog.claw.core.persistence import navigation_journal
    cleanup = navigation_journal.cleanup
    with monkeypatch.context() as faults:
        faults.setattr(navigation_journal, 'cleanup', lambda *args: (_ for _ in ()).throw(OSError('cleanup only')))
        store.commit_navigation('main', target, 'new', [{'role': 'user', 'content': 'committed state'}],
            AccessState(read_only=False, revision=0).to_dict(), {}, branches=[])
        assert json.loads((tmp_path / JOURNAL).read_text())['phase'] == 'committed'
        assert store.get_active_session('main').session_id == target
    reopened = TranscriptStore(tmp_path, owner_group='main')
    assert reopened.load_transcript(target)[0]['content'] == 'committed state'
    assert not (tmp_path / JOURNAL).exists()
    assert navigation_journal.cleanup is cleanup
