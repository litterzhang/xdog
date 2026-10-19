"""Owner-scoped metadata naming, real socket receipts, rollback and no history replay."""
from __future__ import annotations

import asyncio
import threading

import pytest
from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.claw.core.persistence.transcript_store import TranscriptStore


def test_old_or_busy_gateway_and_forged_receipt_cannot_name_another_owner_or_replay():
    ui = ChatApp('/tmp/unused-name.sock')
    try:
        ui._handle_response({'type': 'pong', 'session_controls': 1, 'session_id': 'original'})
        ui._name_session('Unsent')
        assert ui._send_queue.empty()
        ui._handle_response({'type': 'pong', 'session_controls': 1, 'session_rename': True,
                             'session_id': 'original'})
        ui._name_session('Unsent')
        assert ui._send_queue.empty()
        ui._handle_response({'type': 'pong', 'session_controls': 1, 'session_rename': 1,
                             'session_id': 'original', 'session_name': 'Old'})
        ui._active_run_id = 'run'
        ui._name_session('Unsent')
        assert ui._send_queue.empty()
        ui._active_run_id = None
        ui._editor.set_text('NAME-DRAFT')
        ui._name_session('New')
        request = ui._send_queue.get_nowait()
        assert request['expected_session'] == 'original' and request['name'] == 'New'
        ui._handle_response({'type': 'session_result', 'request_id': request['request_id'],
                             'success': True, 'group_id': 'foreign', 'session_id': 'original',
                             'session_name': 'Forged'})
        assert ui._state['session_name'] == 'Old' and ui._pending_command is None
        ui._name_session('Uncertain')
        ui._send_queue.get_nowait()
        ui._handle_response({'type': 'internal_error', 'message': 'lost rename receipt'})
        ui._handle_response({'type': '_connection_opened', 'generation': 1})
        ui._handle_response({'type': 'pong', 'session_controls': 1, 'session_rename': 1,
                             'session_id': 'original', 'session_name': 'Actual'})
        assert ui._state['session_name'] == 'Actual' and ui._send_queue.empty()
        assert ui._editor.get_text() == 'NAME-DRAFT'
    finally:
        ui._request_exit()


def test_name_store_rejects_foreign_inactive_and_stale_owner_without_writes(tmp_path):
    store = TranscriptStore(tmp_path)
    original = store.create_session('main')
    other = store.create_session('other')
    inactive = store.create_session('main')
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir() if path.is_file()}
    for group, sid in [('main', other.session_id), ('main', original.session_id), ('other', inactive.session_id)]:
        with pytest.raises(ValueError):
            store.rename_session(group, sid, 'Wrong owner')
    with pytest.raises(ValueError):
        store.rename_session('main', inactive.session_id, '\x1bunsafe')
    assert before == {path.name: path.read_bytes() for path in tmp_path.iterdir() if path.is_file()}


def test_named_archive_is_read_only_and_current_name_survives_catalog_recovery(tmp_path):
    store = TranscriptStore(tmp_path, owner_group='main')
    named = store.create_session('main')
    store.rename_session('main', named.session_id, 'Durable named session')
    replacement = store.reset_session('main')
    rows = {row['session_id']: row for row in store.list_sessions('main')}
    assert rows[named.session_id]['label'] == 'Durable named session' and not rows[replacement.session_id]['label']
    assert not hasattr(store, 'activate_session')
    assert store.get_active_session('main').session_id == replacement.session_id
    store.rename_session('main', replacement.session_id, 'Current context')
    reloaded = TranscriptStore(tmp_path, owner_group='main')
    assert next(row for row in reloaded.list_sessions('main') if row['session_id'] == named.session_id
                )['label'] == 'Durable named session'
    (tmp_path / 'catalog.json').write_text('{broken')
    recovered = TranscriptStore(tmp_path, owner_group='main')
    assert recovered.get_active_session('main').label == 'Current context'
    assert recovered.get_active_session('main').session_id == replacement.session_id
    assert any(row['session_id'] == named.session_id for row in recovered.list_sessions('main'))


def test_name_runtime_refuses_to_rebind_stale_cached_agent_to_a_new_disk_owner(tmp_path):
    from xdog.claw.core.runtime.group import GroupRuntime
    from xdog.claw.core.types import Group

    runtime = GroupRuntime(group=Group(id='main'), data_dir=tmp_path, model='test/dummy')
    owner = runtime.get_or_create_session(allow_reset=False)
    original = owner.meta
    target = runtime.transcript_store.create_session('main')
    before = runtime.transcript_store._catalog_file.read_bytes()
    with pytest.raises(ValueError, match='Cached session owner changed'):
        runtime.rename_session(target.session_id, 'Never rebind this agent')
    assert owner.meta == original and runtime._active_session is owner
    assert runtime.transcript_store._catalog_file.read_bytes() == before
    assert not runtime.transcript_store.get_active_session('main').label
    owner.dispose()


def test_name_cancellation_at_storage_admission_creates_no_journal_or_name_write(tmp_path, monkeypatch):
    from contextlib import contextmanager

    from xdog.claw.core.persistence import navigation_journal

    store = TranscriptStore(tmp_path, owner_group='main')
    meta = store.create_session('main')
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir() if path.is_file()}
    canceled = threading.Event()
    original = navigation_journal.ownership
    @contextmanager
    def canceled_admission(directory):
        with original(directory):
            canceled.set()
            yield
    monkeypatch.setattr(navigation_journal, 'ownership', canceled_admission)
    with pytest.raises(asyncio.CancelledError):
        store.rename_session('main', meta.session_id, 'Unadmitted', lambda: not canceled.is_set())
    assert not store.get_active_session('main').label
    for name, data in before.items():
        assert (tmp_path / name).read_bytes() == data
    assert not (tmp_path / navigation_journal.JOURNAL).exists()
    assert not list(tmp_path.glob('.navigation-backup-*'))


def test_name_capability_withdrawal_detaches_pending_receipt_without_replay():
    ui = ChatApp('/tmp/unused-name-capability.sock')
    try:
        ui._handle_response({'type': 'pong', 'session_controls': 1, 'session_rename': 1,
                             'session_id': 'original', 'session_name': 'Retained'})
        ui._name_session('Pending')
        request = ui._send_queue.get_nowait()
        ui._handle_response({'type': 'pong', 'session_controls': 1, 'session_id': 'original',
                             'session_name': 'Actual'})
        assert ui._session_mutation_id is None and ui._pending_command is None
        ui._handle_response({'type': 'session_result', 'request_id': request['request_id'],
                             'group_id': 'main', 'session_id': 'original', 'success': True,
                             'session_name': 'Late ignored'})
        assert ui._state['session_name'] == 'Actual'
        ui._name_session('Unavailable')
        assert ui._send_queue.empty()
    finally:
        ui._request_exit()
