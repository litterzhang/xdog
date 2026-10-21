"""Durable intake is separate from Agent construction and model history."""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest


@pytest.fixture
def box(tmp_path):
    from xdog.coding.core.session_manager import SessionManager
    from xdog.coding.core.sessions.inbox_models import InboxMessage
    from xdog.coding.core.sessions.inbox_store import InboxStore

    manager = SessionManager(tmp_path / 'sessions')
    data = manager.create_session(working_dir=tmp_path)
    message = InboxMessage(data.session_id, tmp_path, 'm1', 'Review', ({'path': 'a.py'},), 'Eyes', {'route': 'r'})
    return InboxStore(manager._dir, tmp_path / 'inbox'), message, manager, data


def test_message_identity_survives_store_reopen(box):
    from xdog.coding.core.sessions.inbox_store import InboxStore

    store, message, manager, data = box
    first = store.enqueue(message)
    again = InboxStore(manager._dir, store.root).enqueue(message)
    assert first.duplicate is False and again.duplicate is True
    assert first.record == again.record
    assert first.record.status == 'pending'
    assert data.messages == [] and data.model == ''
    assert not list(store.root.rglob('*.sock'))


def test_conflicting_content_cannot_replace_a_message(box):
    store, message, manager, data = box
    store.enqueue(message)
    with pytest.raises(ValueError, match='conflict'):
        store.enqueue(replace(message, instruction='Different instructions'))
    assert store.records(message.session_id, message.cwd)[0].message.instruction == 'Review'


@pytest.mark.parametrize('field,value', [
    ('session_id', '../other'), ('session_id', 'missing'), ('message_id', ''),
    ('message_id', 'x'*257), ('source', ''), ('source', 'x'*257), ('instruction', ''),
    ('instruction', 'x'*(1024*1024)), ('evidence', (float('nan'),)),
    ('provenance', {'permission': float('inf')}), ('cwd', None),
])
def test_invalid_message_never_becomes_durable_work(box, field, value):
    store, message, manager, data = box
    with pytest.raises((ValueError, FileNotFoundError)):
        store.enqueue(replace(message, **{field: value}))
    assert not list(store.root.rglob('*.json')) if store.root.exists() else True


def test_cwd_mismatch_and_absent_identity_reject(box, tmp_path):
    store, message, manager, data = box
    other = tmp_path/'other'
    other.mkdir()
    with pytest.raises(ValueError, match='workspace'):
        store.enqueue(replace(message, cwd=other))
    manager.delete_session(message.session_id)
    with pytest.raises((ValueError, FileNotFoundError)):
        store.enqueue(message)


def test_opaque_id_cannot_escape_hashed_path_and_nested_input_is_snapshotted(box):
    store, message, manager, data = box
    opaque = replace(message, message_id='../outside;$(touch escaped)')
    store.enqueue(opaque)
    message.evidence[0]['path'] = 'later mutation'
    message.provenance['route'] = 'changed'
    persisted = store.records(message.session_id, message.cwd)[0]
    assert persisted.message.evidence == ({'path': 'a.py'},)
    assert persisted.message.provenance == {'route': 'r'}
    assert len(list(store.root.rglob('*.json'))) == 1
    assert not (store.root.parent/'outside').exists()
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in store.root.rglob('*') if p.is_file())
    assert all(p.stat().st_mode & 0o777 == 0o700 for p in store.root.rglob('*') if p.is_dir())


def test_branch_changes_do_not_rebind_duplicates_or_grant_execution(box):
    store, message, manager, data = box
    store.enqueue(message)
    data.active_branch = 'branch-b'
    manager.save_session(data)
    duplicate = store.enqueue(message)
    assert duplicate.record.branch_id == ''
    assert duplicate.record.status == 'pending'
    with pytest.raises(ValueError, match='branch'):
        store.reserve(message.session_id, ('m1',), cwd=message.cwd, branch_id='branch-b', generation='g')


def test_exact_reservation_and_attempt_results_survive_retry(box):
    store, message, manager, data = box
    store.enqueue(message)
    store.enqueue(replace(message, message_id='m2'))
    first = store.reserve(message.session_id, ('m1',), cwd=message.cwd, branch_id='', generation='g1')[0]
    assert first.status == 'claimed' and first.attempt_id and first.request_id
    assert store.records(message.session_id, message.cwd)[1].status == 'pending'
    with pytest.raises(ValueError):
        store.begin_batch(message.session_id, (first,), generation='other', batch_id='test-batch')[0]
    running = store.begin_batch(message.session_id, (first,), generation='g1', batch_id='test-batch')[0]
    assert running.status == 'running'
    assert store.records(message.session_id, message.cwd)[0].status == 'running'  # Query never recovers a live owner.
    recovered = store.recover(message.session_id, 'g2')
    assert next(r for r in recovered if r.message.message_id == 'm1').status == 'interrupted'
    store.retry(message.session_id, 'm1', cwd=message.cwd, branch_id='')
    second = store.reserve(message.session_id, ('m1',), cwd=message.cwd, branch_id='', generation='g2')[0]
    assert second.attempt_id != first.attempt_id and second.request_id != first.request_id
    assert second.attempts[0].status == 'interrupted'
    store.begin_batch(message.session_id, (second,), generation='g2', batch_id='test-batch')[0]
    final = store.finish(message.session_id, 'm1', generation='g2', attempt_id=second.attempt_id, status='succeeded')
    assert final.status == 'succeeded'
    assert [a.status for a in final.attempts] == ['interrupted', 'succeeded']
    assert store.enqueue(message).record.status == 'succeeded'


def test_retirement_releases_only_selected_owner_claims(box):
    store, message, manager, data = box
    store.enqueue(message)
    store.enqueue(replace(message, message_id='m2'))
    store.reserve(message.session_id, ('m1',), cwd=message.cwd, branch_id='', generation='g1')
    store.reserve(message.session_id, ('m2',), cwd=message.cwd, branch_id='', generation='g2')
    store.retire(message.session_id, 'g1')
    assert [r.status for r in store.records(message.session_id, message.cwd)] == ['pending', 'claimed']


@pytest.mark.parametrize('selection', [(), ('m1', 'm1'), ('m1', 'missing')])
def test_bad_batch_reservation_is_not_partially_applied(box, selection):
    store, message, manager, data = box
    store.enqueue(message)
    with pytest.raises(ValueError):
        store.reserve(message.session_id, selection, cwd=message.cwd, branch_id='', generation='g')
    assert store.records(message.session_id, message.cwd)[0].status == 'pending'


def test_pending_capacity_rejects_new_messages_but_not_duplicates(box):
    store, message, manager, data = box
    for i in range(128):
        store.enqueue(replace(message, message_id=f'm{i:03}'))
    assert store.enqueue(replace(message, message_id='m000')).duplicate
    with pytest.raises(ValueError, match='capacity'):
        store.enqueue(replace(message, message_id='overflow'))
    assert len(store.records(message.session_id, message.cwd)) == 128


@pytest.mark.parametrize('kind', ['root-mode', 'root-link', 'file-mode', 'file-link', 'fifo', 'identity-link'])
def test_private_storage_refuses_unsafe_paths(box, kind, tmp_path):
    store, message, manager, data = box
    store.enqueue(message)
    path = next(store.root.rglob('*.json'))
    if kind == 'root-mode':
        store.root.chmod(0o755)
    elif kind == 'root-link':
        actual = tmp_path/'real-inbox'
        store.root.rename(actual)
        store.root.symlink_to(actual, target_is_directory=True)
    elif kind == 'file-mode':
        path.chmod(0o644)
    elif kind == 'file-link':
        actual = tmp_path/'record.json'
        path.rename(actual)
        path.symlink_to(actual)
    elif kind == 'fifo':
        path.unlink()
        os.mkfifo(path, 0o600)
    elif kind == 'identity-link':
        identity = manager._session_path(message.session_id)
        actual = tmp_path/'identity.json'
        identity.rename(actual)
        identity.symlink_to(actual)
    with pytest.raises((ValueError, OSError)):
        store.enqueue(replace(message, message_id='m2'))


def test_corrupt_or_duplicate_authoritative_records_fail_closed(box):
    store, message, manager, data = box
    store.enqueue(message)
    path = next(store.root.rglob('*.json'))
    other = path.parent.parent/'running'/path.name
    other.write_bytes(path.read_bytes())
    other.chmod(0o600)
    with pytest.raises(ValueError, match='Duplicate'):
        store.records(message.session_id, message.cwd)
    other.unlink()
    path.write_text('{"version":1,"version":2}')
    with pytest.raises(ValueError):
        store.records(message.session_id, message.cwd)


def test_terminal_identity_capacity_does_not_evict_dedup_records(box):
    store, message, manager, data = box
    store.enqueue(message)
    attempt = store.reserve(message.session_id, ('m1',), cwd=message.cwd, branch_id='', generation='g')[0]
    store.begin_batch(message.session_id, (attempt,), generation='g', batch_id='test-batch')[0]
    store.finish(message.session_id, 'm1', generation='g', attempt_id=attempt.attempt_id, status='succeeded')
    path = next(store.root.rglob('*.json'))
    template = json.loads(path.read_text())
    for index in range(4095):
        key = f'old-{index}'
        template['message']['message_id'] = key
        template['sequence'] = index + 2
        template['request_id'] = f'old-request-{index}'
        template['attempt_id'] = f'old-attempt-{index}'
        template['batch_id'] = f'old-batch-{index}'
        template['attempts'][-1].update(id=template['attempt_id'], request_id=template['request_id'],
                                         batch_id=template['batch_id'])
        target = path.parent/(hashlib.sha256(key.encode()).hexdigest()+'.json')
        target.write_text(json.dumps(template))
        target.chmod(0o600)
    with pytest.raises(ValueError, match='capacity'):
        store.enqueue(replace(message, message_id='new'))
    assert store.enqueue(message).duplicate


def test_parallel_processes_share_one_durable_identity(box):
    store, message, manager, data = box
    script = '''
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from xdog.coding.core.sessions.inbox_models import InboxMessage
from xdog.coding.core.sessions.inbox_store import InboxStore
s=InboxStore(Path(sys.argv[1]),Path(sys.argv[2]))
m=InboxMessage(sys.argv[3],Path(sys.argv[4]),'same','Review',(),'peer',{})
print(s.enqueue(m).duplicate,flush=True)
'''
    args = [sys.executable, '-c', script, str(manager._dir), str(store.root), message.session_id, str(message.cwd)]
    processes = [subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(4)]
    results = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=20)
        assert process.returncode == 0, stderr
        results.append(stdout.strip())
    assert results.count('False') == 1 and results.count('True') == 3
    assert len(store.records(message.session_id, message.cwd)) == 1


def test_disk_failures_never_acknowledge_or_overwrite_another_identity(box, monkeypatch):
    store, message, manager, data = box
    store.enqueue(message)
    original = os.fsync

    def fail(fd):
        raise OSError('disk failed')

    monkeypatch.setattr(os, 'fsync', fail)
    with pytest.raises(OSError, match='disk failed'):
        store.enqueue(replace(message, message_id='unacked'))
    monkeypatch.setattr(os, 'fsync', original)
    assert store.enqueue(message).duplicate
    assert store.enqueue(replace(message, message_id='unacked')).record.status == 'pending'


@pytest.mark.parametrize('boundary', range(1, 9))
def test_each_fsync_boundary_reports_failure_and_same_id_can_be_recovered(box, monkeypatch, boundary):
    store, message, manager, data = box
    original, calls = os.fsync, []
    owner_thread = threading.get_ident()

    def fail_at(fd):
        # InboxStore.enqueue is synchronous on this thread. Other tests' owned
        # cleanup IO must neither consume this boundary nor receive our fault.
        if threading.get_ident() == owner_thread:
            calls.append(fd)
            if len(calls) == boundary:
                raise OSError('injected publication failure')
        return original(fd)

    monkeypatch.setattr(os, 'fsync', fail_at)
    # Prove a concurrent, unrelated fsync cannot steal the targeted boundary.
    with tempfile.TemporaryFile() as unrelated, ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(os.fsync, unrelated.fileno()).result(timeout=3)
    assert not calls
    failed = False
    try:
        store.enqueue(message)
    except OSError:
        failed = True
    assert failed or len(calls) < boundary
    monkeypatch.setattr(os, 'fsync', original)
    store.enqueue(message)
    assert len(store.records(message.session_id, message.cwd)) == 1
    with pytest.raises(ValueError, match='conflict'):
        store.enqueue(replace(message, instruction='replacement'))


def test_record_cannot_claim_another_workspace(box, tmp_path):
    store, message, manager, data = box
    store.enqueue(message)
    path = next(store.root.rglob('*.json'))
    raw = json.loads(path.read_text())
    raw['message']['cwd'] = str(tmp_path/'other-project')
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match='workspace'):
        store.records(message.session_id, message.cwd)


@pytest.mark.parametrize('change', ['running-without-attempt', 'terminal-attempt-mismatch', 'attempt-branch-mismatch'])
def test_corrupt_attempt_authority_is_not_loaded(box, change):
    store, message, manager, data = box
    store.enqueue(message)
    a = store.reserve(message.session_id, ('m1',), cwd=message.cwd, branch_id='', generation='g')[0]
    store.begin_batch(message.session_id, (a,), generation='g', batch_id='test-batch')[0]
    path = next(store.root.rglob('*.json'))
    raw = json.loads(path.read_text())
    if change == 'running-without-attempt':
        raw.update(attempt_id='', request_id='', attempts=[])
    elif change == 'terminal-attempt-mismatch':
        raw['status'] = 'succeeded'
    elif change == 'attempt-branch-mismatch':
        raw['attempts'][-1]['branch_id'] = 'another-branch'
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError):
        store.records(message.session_id, message.cwd)


def test_inbox_import_and_intake_do_not_require_acp_or_construct_agent(box):
    store, message, manager, data = box
    script = '''
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from importlib.abc import MetaPathFinder
class Block(MetaPathFinder):
 def find_spec(self,fullname,path=None,target=None):
  if fullname=='acp' or fullname.startswith('acp.') or fullname=='xdog.coding.core.agent_session':
   raise AssertionError('intake must not import ACP or AgentSession')
sys.meta_path.insert(0,Block())
from xdog.coding.core.sessions.inbox_store import InboxStore
from xdog.coding.core.sessions.inbox_models import InboxMessage
s=InboxStore(Path(sys.argv[1]),Path(sys.argv[2]))
s.enqueue(InboxMessage(sys.argv[3],Path(sys.argv[4]),'offline','Review',(),'Eyes',{}))
assert len(s.records(sys.argv[3],Path(sys.argv[4])))==1
'''
    result = subprocess.run([sys.executable, '-c', script, str(manager._dir), str(store.root),
                             message.session_id, str(message.cwd)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_durable_scope_identity_survives_reopen_but_changes_with_a_new_store(box,tmp_path):
    from xdog.coding.core.sessions.inbox_store import InboxStore

    store,message,manager,data = box
    first = store.scope_id(message.session_id,message.cwd)
    assert InboxStore(manager._dir,store.root).scope_id(message.session_id,message.cwd)==first
    assert InboxStore(manager._dir,tmp_path/'another-inbox').scope_id(message.session_id,message.cwd)!=first
    (store.root/'.store-id').unlink()
    assert store.scope_id(message.session_id,message.cwd)!=first
