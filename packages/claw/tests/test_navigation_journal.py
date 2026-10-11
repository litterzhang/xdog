"""Owned offline subprocess death at real managed-write and recovery boundaries."""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest
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
    {'mode':'planning','revision':1,'objective':'committed plan','explanation':'','steps':[]},
    {'fixture':[{'id':'new','kind':'note','data':'committed entry'}]},
    branches=[('return', [{'role':'user','content':'retained return head'}], {'label':'Return checkpoint'})],
    active_tools=())
print(count)
'''


@pytest.mark.parametrize('point', range(1, 14))
def test_real_process_death_after_each_managed_write_restores_exact_old_state(tmp_path, point):
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


@pytest.mark.parametrize('point', [0, 1])
def test_commit_marker_distinguishes_unacknowledged_and_committed_restart(tmp_path, point):
    _, old, target = create(tmp_path)
    before = snapshot(tmp_path)
    child = subprocess.run([sys.executable, '-c', SCRIPT, str(tmp_path), target, 'marker', str(point)],
                           capture_output=True, text=True, timeout=15)
    assert child.returncode == (71 if point == 0 else 72), child.stderr
    reopened = TranscriptStore(tmp_path, owner_group='main')
    if point == 0:
        assert snapshot(tmp_path) == before and reopened.get_active_session('main').session_id == old
    else:
        assert reopened.get_active_session('main').session_id == target
        assert reopened.load_transcript(target)[0]['content'] == 'committed target'
        assert reopened.get_plan(target)['objective'] == 'committed plan'
        assert reopened.get_extension_entries(target)['fixture'][0]['data'] == 'committed entry'
        assert reopened.load_branch(target, 'return')[0]['content'] == 'retained return head'
        assert reopened.get_active_tools(target) == ()
        assert reopened.get_active_tools(target, 'return') == ('previously-selected',)
    assert not (tmp_path / JOURNAL).exists()


def test_interrupted_recovery_replays_idempotently_without_consuming_backups(tmp_path):
    first, second = tmp_path / 'first.jsonl', tmp_path / 'second.jsonl'
    first.write_text('first original')
    second.write_text('second original')
    with ownership(tmp_path):
        NavigationJournal(tmp_path, {first, second})
    first.write_text('first changed')
    second.write_text('second changed')
    script = r'''
import os, sys
from pathlib import Path
import xdog.claw.core.persistence.navigation_journal as journal
replace = journal.os.replace
count = 0
def die_after_one(source, target):
    global count
    replace(source, target)
    count += 1
    if count == 1:
        os._exit(73)
journal.os.replace = die_after_one
journal.recover(Path(sys.argv[1]))
'''
    child = subprocess.run([sys.executable, '-c', script, str(tmp_path)], capture_output=True, timeout=15)
    assert child.returncode == 73
    assert len(list(tmp_path.glob('.navigation-backup-*'))) == 2
    recover(tmp_path)
    assert first.read_text() == 'first original' and second.read_text() == 'second original'


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


@pytest.mark.skipif(os.name != 'posix', reason='POSIX advisory lock proof')
def test_second_process_cannot_recover_or_modify_live_navigation_owner(tmp_path):
    path = tmp_path / 'owned.jsonl'
    path.write_text('original')
    with ownership(tmp_path):
        NavigationJournal(tmp_path, {path})
        child = subprocess.run([sys.executable, '-c',
            'from pathlib import Path; import sys; from xdog.claw.core.persistence.navigation_journal import recover; '
            'recover(Path(sys.argv[1]))', str(tmp_path)], capture_output=True, text=True, timeout=15)
        assert child.returncode != 0 and 'Another process owns' in child.stderr
        assert path.read_text() == 'original' and (tmp_path / JOURNAL).exists()
    recover(tmp_path)


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
                {'mode': 'off', 'revision': 0, 'objective': '', 'explanation': '', 'steps': []}, {}, branches=[])
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
            {'mode': 'off', 'revision': 0, 'objective': '', 'explanation': '', 'steps': []}, {}, branches=[])
        assert json.loads((tmp_path / JOURNAL).read_text())['phase'] == 'committed'
        assert store.get_active_session('main').session_id == target
    reopened = TranscriptStore(tmp_path, owner_group='main')
    assert reopened.load_transcript(target)[0]['content'] == 'committed state'
    assert not (tmp_path / JOURNAL).exists()
    assert navigation_journal.cleanup is cleanup
