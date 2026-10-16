"""Durable intake order, non-permission claims and conservative version migration."""
import hashlib
import json
import subprocess
import sys
from dataclasses import replace

import pytest


@pytest.fixture
def inbox(tmp_path):
    from xdog.coding.core.session_manager import SessionManager
    from xdog.coding.core.sessions.inbox_models import InboxMessage
    from xdog.coding.core.sessions.inbox_store import InboxStore

    manager = SessionManager(tmp_path / 'sessions')
    session = manager.create_session(working_dir=tmp_path)
    store = InboxStore(manager._dir, tmp_path / 'inbox')
    message = InboxMessage(session.session_id, tmp_path, 'message', 'Review changes', (), 'external', {})
    return store, message


def test_intake_records_pending_in_arrival_order_after_reopen(inbox):
    from xdog.coding.core.sessions.inbox_store import InboxStore

    store, message = inbox
    receipts = [store.enqueue(replace(message, message_id=key)) for key in ('z', 'a', 'm')]
    assert all(receipt.record.status == 'pending' for receipt in receipts)
    reopened = InboxStore(store.sessions_dir, store.root)
    rows = reopened.records(message.session_id, message.cwd)
    assert [row.message.message_id for row in rows] == ['z', 'a', 'm']
    assert [row.sequence for row in rows] == [1, 2, 3]
    duplicate = reopened.enqueue(replace(message, message_id='z'))
    assert duplicate.duplicate and duplicate.record.sequence == 1
    assert reopened.enqueue(replace(message, message_id='new')).record.sequence == 4


def test_claim_is_not_permission_and_batch_running_binds_exact_attempts(inbox):
    store, message = inbox
    for key in ('one', 'two'):
        store.enqueue(replace(message, message_id=key))
    claimed = store.reserve(message.session_id, ('one', 'two'), cwd=message.cwd, branch_id='', generation='g')
    assert [row.status for row in claimed] == ['claimed', 'claimed']
    assert len({row.request_id for row in claimed}) == 2
    running = store.begin_batch(message.session_id, claimed, batch_id='batch-one', generation='g')
    assert [row.status for row in running] == ['running', 'running']
    assert all(row.batch_id == 'batch-one' and row.attempts[-1].batch_id == 'batch-one' for row in running)
    with pytest.raises(ValueError, match='claim|attempt|state|context'):
        store.begin_batch(message.session_id, claimed, batch_id='other', generation='g')


def test_recover_unstarted_claim_and_never_replay_running_message(inbox):
    store, message = inbox
    for key in ('unstarted', 'running'):
        store.enqueue(replace(message, message_id=key))
    rows = store.reserve(message.session_id, ('unstarted', 'running'), cwd=message.cwd, branch_id='', generation='old')
    store.begin_batch(message.session_id, (rows[1],), batch_id='active', generation='old')
    recovered = store.recover(message.session_id, 'new')
    assert [(row.message.message_id, row.status) for row in recovered] == [
        ('unstarted', 'pending'), ('running', 'interrupted')]
    assert recovered[0].attempts and not recovered[0].owner_generation
    assert recovered[1].batch_id == 'active'
    with pytest.raises(ValueError):
        store.reserve(message.session_id, ('running',), cwd=message.cwd, branch_id='', generation='new')
    retry = store.retry(message.session_id, 'running', cwd=message.cwd, branch_id='')
    assert retry.status == 'pending' and retry.attempts[-1].status == 'interrupted'
    next_attempt = store.reserve(message.session_id, ('running',), cwd=message.cwd, branch_id='', generation='new')[0]
    assert len(next_attempt.attempts) == 2 and next_attempt.attempt_id != recovered[1].attempt_id


def test_cancel_before_execution_has_no_invented_attempt_and_is_terminal(inbox):
    store, message = inbox
    store.enqueue(message)
    row = store.cancel_pending(message.session_id, message.message_id, cwd=message.cwd, branch_id='', generation='g')
    assert row.status == 'cancelled' and row.attempts == () and not row.attempt_id
    assert store.enqueue(message).record.status == 'cancelled'
    with pytest.raises(ValueError):
        store.reserve(message.session_id, (message.message_id,), cwd=message.cwd, branch_id='', generation='g')


def test_partial_running_publication_never_starts_batch(inbox, monkeypatch):
    store, message = inbox
    for key in ('one', 'two'):
        store.enqueue(replace(message, message_id=key))
    claimed = store.reserve(message.session_id, ('one', 'two'), cwd=message.cwd, branch_id='', generation='old')
    save = store._save

    def fail_second(directory, record, old=None):
        if record.status == 'running' and record.message.message_id == 'two':
            raise OSError('second running fsync failed')
        return save(directory, record, old)

    monkeypatch.setattr(store, '_save', fail_second)
    with pytest.raises(OSError, match='fsync'):
        store.begin_batch(message.session_id, claimed, batch_id='partial', generation='old')
    monkeypatch.setattr(store, '_save', save)
    rows = store.recover(message.session_id, 'new')
    assert [row.status for row in rows] == ['interrupted', 'pending']
    # This pins store publication only; Task 4 also proves zero actual model calls on this fault.


def test_release_unstarted_preserves_attempt_and_does_not_release_running(inbox):
    store, message = inbox
    store.enqueue(message)
    claimed = store.reserve(message.session_id, (message.message_id,), cwd=message.cwd, branch_id='', generation='g')
    store.release_unstarted(message.session_id, (message.message_id,), generation='g')
    row = store.records(message.session_id, message.cwd)[0]
    assert row.status == 'pending' and len(row.attempts) == 1
    claimed = store.reserve(message.session_id, (message.message_id,), cwd=message.cwd, branch_id='', generation='g')
    store.begin_batch(message.session_id, claimed, batch_id='batch', generation='g')
    store.release_unstarted(message.session_id, (message.message_id,), generation='g')
    assert store.records(message.session_id, message.cwd)[0].status == 'running'


def write_legacy(store, message, status):
    directory = store.root / message.session_id
    for folder in ('pending', 'running', 'terminal'):
        (directory / folder).mkdir(mode=0o700, parents=True, exist_ok=True)
    store.root.chmod(0o700)
    directory.chmod(0o700)
    attempted = status != 'pending_review'
    row = {'version': 1,
           'message': {'session_id': message.session_id, 'cwd': str(message.cwd),
                       'message_id': message.message_id, 'instruction': message.instruction,
                       'source': message.source, 'evidence': [], 'provenance': {'legacy': True}},
           'branch_id': '', 'status': status, 'owner_generation': 'old' if attempted else '',
           'attempt_id': 'attempt-' + message.message_id if attempted else '',
           'request_id': 'request-' + message.message_id if attempted else '', 'error': '',
           'attempts': [{'id': 'attempt-' + message.message_id, 'request_id': 'request-' + message.message_id,
                         'generation': 'old', 'branch_id': '', 'status': status, 'error': ''}] if attempted else []}
    path = directory / 'pending' / (hashlib.sha256(message.message_id.encode()).hexdigest() + '.json')
    path.write_text(json.dumps(row))
    path.chmod(0o600)
    return path


def test_legacy_migration_retains_attempt_facts_and_blocks_running_replay(inbox):
    store, message = inbox
    paths = [write_legacy(store, replace(message, message_id=str(i)), status)
             for i, status in enumerate(('pending_review', 'approved', 'running', 'succeeded'))]
    rows = store.recover(message.session_id, 'new')
    assert [row.status for row in rows] == ['pending', 'pending', 'interrupted', 'succeeded']
    assert rows[1].attempts[0].id == 'attempt-1'
    assert rows[1].attempts[0].status == 'approved'  # Historical fact, never current authority.
    assert not rows[1].attempt_id and not rows[1].owner_generation
    assert rows[2].attempts[0].status == 'interrupted' and rows[2].attempt_id == 'attempt-2'
    assert all(row.message.provenance == {'legacy': True} for row in rows)
    persisted = [json.loads(path.read_text()) for path in store.root.rglob('*.json')]
    assert len(persisted) == len(paths) and all(row['version'] == 2 for row in persisted)
    assert [row.sequence for row in rows] == [1, 2, 3, 4]
    assert store.recover(message.session_id, 'new') == rows


@pytest.mark.parametrize('stage,expected', [('reserve', 'pending'), ('running', 'interrupted')])
def test_actual_process_death_obeys_claim_and_running_boundaries(inbox, stage, expected):
    store, message = inbox
    store.enqueue(message)
    script = '''
import os,sys
from pathlib import Path
from xdog.coding.core.sessions.inbox_store import InboxStore
s=InboxStore(Path(sys.argv[1]),Path(sys.argv[2]))
a=s.reserve(sys.argv[3],('message',),cwd=Path(sys.argv[4]),branch_id='',generation='old')
if sys.argv[5]=='running':s.begin_batch(sys.argv[3],a,batch_id='crash-batch',generation='old')
os._exit(17)
'''
    result = subprocess.run([sys.executable, '-c', script, str(store.sessions_dir), str(store.root),
                             message.session_id, str(message.cwd), stage], capture_output=True, text=True, timeout=20)
    assert result.returncode == 17, result.stderr
    assert store.recover(message.session_id, 'new')[0].status == expected


def test_intake_order_survives_restart_and_concurrent_writers(inbox):
    from xdog.coding.core.sessions.inbox_store import InboxStore

    store, message = inbox
    script = '''
import sys
from pathlib import Path
from xdog.coding.core.sessions.inbox_store import InboxStore
from xdog.coding.core.sessions.inbox_models import InboxMessage
s=InboxStore(Path(sys.argv[1]),Path(sys.argv[2]))
s.enqueue(InboxMessage(sys.argv[3],Path(sys.argv[4]),sys.argv[5],'Review',(),'worker',{}))
'''
    workers = [subprocess.Popen([sys.executable, '-c', script, str(store.sessions_dir), str(store.root),
                                message.session_id, str(message.cwd), str(i)], stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True) for i in range(4)]
    for worker in workers:
        _, error = worker.communicate(timeout=20)
        assert worker.returncode == 0, error
    original = store.records(message.session_id, message.cwd)
    assert len(original) == 4 and [row.sequence for row in original] == [1, 2, 3, 4]
    assert InboxStore(store.sessions_dir, store.root).records(message.session_id, message.cwd) == original
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in store.root.rglob('*') if p.is_file())
    assert not list(store.root.rglob('*.sock'))


@pytest.mark.parametrize('ids', [([],), (None,), (True,), ('message', 'message')])
def test_malformed_reservation_ids_fail_without_changing_pending_work(inbox, ids):
    store, message = inbox
    store.enqueue(message)
    with pytest.raises(ValueError):
        store.reserve(message.session_id, ids, cwd=message.cwd, branch_id='', generation='g')
    assert store.records(message.session_id, message.cwd)[0].status == 'pending'


@pytest.mark.parametrize('field,value', [('sequence', True), ('sequence', 0), ('sequence', 2**63),
                                        ('legacy_order', 1), ('version', True), ('status', 'approved')])
def test_corrupt_v2_state_never_becomes_executable_work(inbox, field, value):
    store, message = inbox
    store.enqueue(message)
    path = next(store.root.rglob('*.json'))
    raw = json.loads(path.read_text())
    raw[field] = value
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError):
        store.records(message.session_id, message.cwd)


def test_counter_directory_fsync_failure_does_not_ack_and_keeps_gap(inbox, monkeypatch):
    from xdog.coding.core.sessions import inbox_store

    store, message = inbox
    original = inbox_store._sync_directory
    directory = store.root / message.session_id

    def fail(path):
        if path == directory and (directory / '.sequence').exists():
            raise OSError('counter directory fsync failed')
        original(path)

    monkeypatch.setattr(inbox_store, '_sync_directory', fail)
    with pytest.raises(OSError, match='counter'):
        store.enqueue(message)
    assert not list(store.root.rglob('*.json'))
    monkeypatch.setattr(inbox_store, '_sync_directory', original)
    receipt = store.enqueue(message)
    assert receipt.record.sequence == 2 and not receipt.duplicate
    assert store.enqueue(message).duplicate


def test_changed_claim_content_cannot_enter_the_selected_batch(inbox):
    store, message = inbox
    store.enqueue(message)
    claimed = store.reserve(message.session_id, (message.message_id,), cwd=message.cwd, branch_id='', generation='g')
    path = next(store.root.rglob('*.json'))
    raw = json.loads(path.read_text())
    raw['message']['instruction'] = 'Different contents after selection'
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match='context|attempt'):
        store.begin_batch(message.session_id, claimed, batch_id='batch', generation='g')
    assert store.records(message.session_id, message.cwd)[0].status == 'claimed'


def test_claim_cas_distinguishes_json_boolean_from_number(inbox):
    store, message = inbox
    message = replace(message, provenance={'value': 1})
    store.enqueue(message)
    claimed = store.reserve(message.session_id, (message.message_id,), cwd=message.cwd, branch_id='', generation='g')
    path = next(store.root.rglob('*.json'))
    raw = json.loads(path.read_text())
    raw['message']['provenance']['value'] = True
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match='context|attempt'):
        store.begin_batch(message.session_id, claimed, batch_id='changed', generation='g')


@pytest.mark.parametrize('status', ['claimed', 'succeeded', 'interrupted'])
def test_current_attempt_batch_identity_must_match_even_outside_running(inbox, status):
    store, message = inbox
    store.enqueue(message)
    rows = store.reserve(message.session_id, (message.message_id,), cwd=message.cwd, branch_id='', generation='g')
    if status != 'claimed':
        store.begin_batch(message.session_id, rows, batch_id='actual-batch', generation='g')
        store.finish(message.session_id, message.message_id, generation='g', attempt_id=rows[0].attempt_id,
                     status=status)
    path = next(store.root.rglob('*.json'))
    raw = json.loads(path.read_text())
    raw['batch_id'] = 'forged-batch'
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match='batch'):
        store.records(message.session_id, message.cwd)
