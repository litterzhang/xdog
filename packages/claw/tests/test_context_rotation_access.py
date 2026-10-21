"""One-way context rotation preserves access before publication and fails closed."""
from __future__ import annotations

import json
from unittest.mock import Mock

import pytest
from xdog.agent.access import AccessState
from xdog.claw.core.persistence.navigation_journal import JOURNAL, NavigationRecoveryError
from xdog.claw.core.persistence.transcript_store import TranscriptStore
from xdog.claw.core.runtime.group import GroupRuntime
from xdog.claw.core.types import Group


def saved_files(directory):
    return {path.name: path.read_bytes() for path in directory.iterdir()
            if path.is_file() and not path.name.startswith('.')}


def protected(directory):
    store = TranscriptStore(directory, owner_group='main')
    current = store.create_session('main')
    scope = AccessState(read_only=True, revision=7).to_dict()
    store.save_access(current.session_id, scope)
    return store, current.session_id, scope


def test_history_activation_apis_are_absent_not_only_hidden():
    for name in ('activate_session', 'set_active_branch', 'commit_navigation', 'save_branch'):
        assert not hasattr(TranscriptStore, name), name
    assert hasattr(TranscriptStore, 'save_compaction_checkpoint')
    assert hasattr(TranscriptStore, 'load_branch')


@pytest.mark.parametrize('source', ['sidecar', 'catalog', 'legacy-plan'])
def test_rotation_inherits_current_scope_without_new_sidecar_and_survives_reload(tmp_path, source):
    store = TranscriptStore(tmp_path, owner_group='main')
    old = store.create_session('main')
    scope = AccessState(read_only=True, revision=7).to_dict()
    if source == 'sidecar':
        store.save_access(old.session_id, scope)
    elif source == 'catalog':
        store._index['main']['access'] = scope
        store._save_index()
    else:
        store._path(old.session_id).with_suffix('.plan.json').write_text(json.dumps({
            'mode': 'planning', 'revision': 7, 'objective': 'Historical read-only note', 'steps': []}))
    store = TranscriptStore(tmp_path, owner_group='main')
    fresh = store.reset_session('main')
    assert fresh.session_id != old.session_id and store.get_access(fresh.session_id) == scope
    assert store._index['main']['access'] == scope
    assert store._catalog['sessions'][fresh.session_id]['access'] == scope
    assert not store._path(fresh.session_id).with_suffix('.access.json').exists()
    assert store.get_access(old.session_id) == scope
    store.increment_turn(fresh.session_id)
    reloaded = TranscriptStore(tmp_path, owner_group='main')
    assert reloaded.get_access(fresh.session_id) == scope
    # The current index independently retains access if catalog recovery is needed.
    reloaded._catalog_file.write_text('{broken')
    recovered = TranscriptStore(tmp_path, owner_group='main')
    assert recovered.get_active_session('main').session_id == fresh.session_id
    assert recovered.get_access(fresh.session_id) == scope


def test_plain_new_context_is_writable_but_other_groups_do_not_inherit_a_foreign_restriction(tmp_path):
    store, _, _ = protected(tmp_path)
    other = store.create_session('other')
    assert store.get_access(other.session_id) == AccessState().to_dict()
    fresh = store.reset_session('other')
    assert store.get_access(fresh.session_id) == AccessState().to_dict()


def test_scope_is_durable_before_pointer_write_and_memory_publication(tmp_path, monkeypatch):
    store, old, scope = protected(tmp_path)
    original = store._atomic
    seen = []
    def observe(path, value, **kwargs):
        assert store.get_active_session('main').session_id == old
        if path == store._index_file:
            fresh = value['main']['session_id']
            catalog = json.loads(store._catalog_file.read_text())
            assert catalog['sessions'][fresh]['access'] == scope
            assert value['main']['access'] == scope
            assert json.loads(store._index_file.read_text())['main']['session_id'] == old
            seen.append(fresh)
        return original(path, value, **kwargs)
    monkeypatch.setattr(store, '_atomic', observe)
    fresh = store.create_session('main')
    assert seen == [fresh.session_id] and store.get_active_session('main').session_id == fresh.session_id
    assert store.get_access(fresh.session_id) == scope


@pytest.mark.parametrize('point', ['transcript', 'catalog', 'index', 'commit'])
@pytest.mark.parametrize('when', ['before', 'after'])
def test_io_failure_never_publishes_a_writable_context_or_loses_restriction(tmp_path, monkeypatch, point, when):
    from xdog.claw.core.persistence import navigation_journal
    store, old, scope = protected(tmp_path)
    before = saved_files(tmp_path)
    atomic = store._atomic
    manifest = navigation_journal.write_manifest
    def fail(path, value, **kwargs):
        target = (path.suffix == '.jsonl' if point == 'transcript' else
                  path == store._catalog_file if point == 'catalog' else path == store._index_file)
        if target and when == 'before':
            raise OSError('Injected context persistence failure')
        result = atomic(path, value, **kwargs)
        if target and when == 'after':
            raise OSError('Injected context persistence failure')
        return result
    def fail_commit(directory, value):
        if value['phase'] == 'committed' and when == 'before':
            raise OSError('Injected context commit failure')
        manifest(directory, value)
        if value['phase'] == 'committed' and when == 'after':
            raise OSError('Injected context commit failure')
    with monkeypatch.context() as patch:
        if point == 'commit':
            patch.setattr(navigation_journal, 'write_manifest', fail_commit)
        else:
            patch.setattr(store, '_atomic', fail)
        with pytest.raises(OSError, match='Injected context'):
            store.create_session('main')
    assert store.get_active_session('main').session_id == old
    assert store.get_access(old) == scope and saved_files(tmp_path) == before
    assert not (tmp_path / JOURNAL).exists()
    reopened = TranscriptStore(tmp_path, owner_group='main')
    assert reopened.get_active_session('main').session_id == old and reopened.get_access(old) == scope


@pytest.mark.parametrize('corruption', ['null-catalog', 'bad-sidecar'])
def test_invalid_current_scope_refuses_new_context_without_writable_fallback(tmp_path, corruption):
    store = TranscriptStore(tmp_path)
    old = store.create_session('main')
    if corruption == 'null-catalog':
        store._index['main']['access'] = None
        store._save_index()
    else:
        store._path(old.session_id).with_suffix('.access.json').write_text('{broken')
    reopened = TranscriptStore(tmp_path)
    before = saved_files(tmp_path)
    with pytest.raises(ValueError, match='access scope'):
        reopened.reset_session('main')
    assert reopened.get_active_session('main').session_id == old.session_id
    assert saved_files(tmp_path) == before


def test_failed_rollback_blocks_scope_reads_and_rotation_until_storage_recovery(tmp_path, monkeypatch):
    from xdog.claw.core.persistence import navigation_journal
    store, old, scope = protected(tmp_path)
    before = saved_files(tmp_path)
    atomic, replace = store._atomic, navigation_journal.os.replace
    def fail_index(path, value, **kwargs):
        if path == store._index_file:
            raise OSError('Index write unavailable')
        return atomic(path, value, **kwargs)
    def fail_restore(source, destination):
        if '.navigation-restore-' in str(source):
            raise OSError('Rollback storage unavailable')
        return replace(source, destination)
    with monkeypatch.context() as patch:
        patch.setattr(store, '_atomic', fail_index)
        patch.setattr(navigation_journal.os, 'replace', fail_restore)
        with pytest.raises(OSError, match='Rollback storage'):
            store.reset_session('main')
        for operation in (lambda: store.get_active_session('main'), lambda: store.get_access(old),
                          lambda: store.create_session('main')):
            with pytest.raises(NavigationRecoveryError):
                operation()
    reopened = TranscriptStore(tmp_path, owner_group='main')
    assert reopened.get_active_session('main').session_id == old
    assert reopened.get_access(old) == scope and saved_files(tmp_path) == before


@pytest.mark.parametrize('operation', ['new', 'reset'])
@pytest.mark.parametrize('fail', [False, True])
async def test_group_new_reset_preserves_readonly_even_after_failure(tmp_path, monkeypatch, operation, fail):
    runtime = GroupRuntime(group=Group(id='main'), data_dir=tmp_path, model='test/dummy',
                           stream_fn=Mock(side_effect=AssertionError('No model execution')))
    original = runtime.get_or_create_session(allow_reset=False)
    original.access.set_read_only(True)
    old = original.meta.session_id
    try:
        with monkeypatch.context() as patch:
            atomic = runtime.transcript_store._atomic
            def fault(path, value, **kwargs):
                if path == runtime.transcript_store._index_file:
                    raise OSError('New-context write failed')
                return atomic(path, value, **kwargs)
            if fail:
                patch.setattr(runtime.transcript_store, '_atomic', fault)
            def rotate():
                if operation == 'new':
                    runtime.navigate_session('new', '', '', -1, old)
                else:
                    runtime.reset_session()
            if fail:
                with pytest.raises(OSError, match='New-context write'):
                    rotate()
                assert runtime.get_new_session_id() == old
            else:
                rotate()
                assert runtime.get_new_session_id() != old
        current = runtime.get_or_create_session(allow_reset=False)
        assert current.access.state.read_only
        reopened = TranscriptStore(runtime.transcript_store._sessions_dir, owner_group='main')
        assert reopened.get_access(current.meta.session_id)['read_only'] is True
    finally:
        if runtime._active_session is not None:
            await runtime._active_session.children.runtime.aclose()
            await runtime._active_session.extensions.aclose()
            runtime._active_session.dispose()
        for extension in runtime._retired_extensions:
            await extension.aclose()
        runtime.mcp.close()


CONTEXT_CRASH = r'''
import os, sys
from pathlib import Path
from xdog.claw.core.persistence.transcript_store import TranscriptStore
import xdog.claw.core.persistence.navigation_journal as journal
root, stage = Path(sys.argv[1]), sys.argv[2]
store = TranscriptStore(root, owner_group='main')
count = 0
atomic = store._atomic
manifest = journal.write_manifest
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


@pytest.mark.parametrize('stage', ['1', '2', '3', 'committed'])
def test_real_process_interruption_cannot_publish_context_without_readonly(tmp_path, stage):
    import subprocess
    import sys
    store, old, scope = protected(tmp_path)
    before = saved_files(tmp_path)
    child = subprocess.run([sys.executable, '-c', CONTEXT_CRASH, str(tmp_path), stage],
                           capture_output=True, text=True, timeout=15)
    assert child.returncode == (72 if stage == 'committed' else 71), child.stderr
    assert (tmp_path / JOURNAL).exists()
    reopened = TranscriptStore(tmp_path, owner_group='main')
    current = reopened.get_active_session('main')
    assert reopened.get_access(current.session_id) == scope
    if stage == 'committed':
        assert current.session_id != old
        assert reopened._index['main']['access'] == scope
        assert reopened._catalog['sessions'][current.session_id]['access'] == scope
        assert not reopened._path(current.session_id).with_suffix('.access.json').exists()
    else:
        assert current.session_id == old and saved_files(tmp_path) == before
    assert not (tmp_path / JOURNAL).exists()
