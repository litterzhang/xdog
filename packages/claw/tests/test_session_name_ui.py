"""Owner-scoped metadata naming, real socket receipts, rollback and no history replay."""
from __future__ import annotations

import asyncio
import subprocess
import sys
import threading
from unittest.mock import patch

import pytest
from xdog.ai.types import AssistantMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.persistence.transcript_store import TranscriptStore
from xdog.claw.core.runtime.gateway import GatewayServer


@pytest.mark.parametrize('scenario', ['success', 'failure', 'cancel-before', 'cancel-commit'])
async def test_actual_gateway_name_receipt_browser_search_reload_and_failure_keep_same_history_owner(
    tmp_path, monkeypatch, scenario,
):
    config = ClawConfig(model='offline/model', data_dir=str(tmp_path / 'data'),
        socket_path=str(tmp_path / 'name.sock'), pid_file=str(tmp_path / 'name.pid'),
        groups=(GroupDef(id='main', is_main=True),))
    seed = TranscriptStore(tmp_path / 'data/groups/main/sessions', owner_group='main')
    meta = seed.create_session('main')
    seed.replace_transcript(meta.session_id, [{'role': 'user', 'content': 'UNCHANGED-NAME-HISTORY'}])
    def stream(*args):
        pytest.fail('Session naming cannot invoke a model')
        return EventStream.empty(AssistantMessage())
    server = GatewayServer(config)
    with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=(config.model, stream)):
        await server.start()
    runtime = server._orchestrator._runtimes['main']
    owner = runtime.get_or_create_session(allow_reset=False)
    await owner.initialize_extensions()
    owner._restore_messages()
    store = runtime.transcript_store
    before = {path: path.read_bytes() for path in store._sessions_dir.iterdir() if path.is_file()}
    state, plan, extensions, children = owner.agent.state, owner.plan, owner.extensions, owner.children
    source = runtime._source_revision
    original = store._atomic
    failure = scenario in {'failure', 'cancel-before'}
    entered, release = threading.Event(), threading.Event()
    owners = []
    if scenario.startswith('cancel'):
        rename = server._orchestrator.rename_session
        async def capture_owner(*args):
            owners.append(asyncio.current_task())
            return await rename(*args)
        monkeypatch.setattr(server._orchestrator, 'rename_session', capture_owner)
    if scenario == 'cancel-before':
        rename_runtime = runtime.rename_session
        def held_rename(*args):
            entered.set()
            assert release.wait(8)
            return rename_runtime(*args)
        monkeypatch.setattr(runtime, 'rename_session', held_rename)
    if scenario == 'cancel-commit':
        def held_commit(path, value, **kwargs):
            original(path, value, **kwargs)
            if path == store._index_file:
                entered.set()
                assert release.wait(8)
        monkeypatch.setattr(store, '_atomic', held_commit)
    if scenario == 'failure':
        def fail_index(path, value, **kwargs):
            if path == store._index_file:
                raise OSError('offline name commit failure')
            return original(path, value, **kwargs)
        monkeypatch.setattr(store, '_atomic', fail_index)
    ui = ChatApp(config.socket_path)
    reader, writer = await asyncio.open_unix_connection(config.socket_path, limit=4 * 1024 * 1024)
    task = asyncio.create_task(ui._run_connection(reader, writer))
    async def wait(predicate):
        async with asyncio.timeout(8):
            while not predicate():
                ui._poll()
                await asyncio.sleep(0.003)
    try:
        await wait(lambda: ui._connection_ready)
        assert ui._session_rename_supported
        ui._editor.set_text('RETAINED-NAME-DRAFT')
        epoch = ui._output_epoch
        ui._handle_submit('/name 开发 release review')
        if scenario.startswith('cancel'):
            await wait(entered.is_set)
            ui._cancel_session_navigation()
            await wait(lambda: owners[0].cancelling() >= 1)
            ui._cancel_session_navigation()
            await wait(lambda: owners[0].cancelling() >= 2)
            assert 'main' in server._orchestrator._navigation_groups
            release.set()
        await wait(lambda: ui._session_mutation_id is None and ui._pending_command is None)
        assert runtime._active_session is owner and owner.agent.state is state
        assert owner.plan is plan and owner.extensions is extensions and owner.children is children
        assert source == runtime._source_revision and epoch == ui._output_epoch
        assert ui._editor.get_text() == 'RETAINED-NAME-DRAFT'
        assert 'UNCHANGED-NAME-HISTORY' in str(ui._chat_log.render(140))
        if failure:
            assert not store.get_active_session('main').label and not owner.meta.label
            for path, data in before.items():
                assert path.read_bytes() == data, path.name
            assert not list(store._sessions_dir.glob('.navigation-backup-*'))
        else:
            assert owner.meta.label == '开发 release review'
            assert store.get_active_session('main').label == ui._state['session_name'] == '开发 release review'
            reloaded = TranscriptStore(store._sessions_dir, owner_group='main')
            assert reloaded.get_active_session('main').label == '开发 release review'
            assert reloaded.list_sessions('main')[0]['label'] == '开发 release review'
            # Ordinary future metadata persistence cannot clobber the name.
            store.increment_turn(meta.session_id)
            assert store.get_active_session('main').label == '开发 release review'
            ui._open_navigation()
            ui._navigation_provider.request('sessions', 'release review')
            await wait(lambda: not ui._navigation_provider.snapshot().busy)
            rows = ui._navigation_provider.snapshot().entries
            assert len(rows) == 1 and rows[0].title == '开发 release review' and rows[0].active
            ui._close_navigation()
            ui._handle_submit('/name -')
            await wait(lambda: ui._session_mutation_id is None)
            assert not owner.meta.label and not store.list_sessions('main')[0]['label']
            assert not ui._state['session_name']
        assert store._path(meta.session_id).read_bytes() == before[store._path(meta.session_id)]
    finally:
        release.set()
        ui._request_exit()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        writer.close()
        await writer.wait_closed()
        await server.stop()


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


@pytest.mark.parametrize('boundary', ['catalog.json', 'sessions.json'])
def test_name_process_death_recovers_catalog_and_index_together_without_transcript_changes(tmp_path, boundary):
    store = TranscriptStore(tmp_path, owner_group='main')
    meta = store.create_session('main')
    store.append_turn(meta.session_id, {'role': 'user', 'content': 'ORIGINAL'})
    store.increment_turn(meta.session_id)
    original = {path.name: path.read_bytes() for path in tmp_path.iterdir() if path.is_file()}
    code = '''
import os, sys
from pathlib import Path
from xdog.claw.core.persistence.transcript_store import TranscriptStore
store = TranscriptStore(Path(sys.argv[1]), owner_group='main')
atomic = store._atomic
def interrupted(path, value, **kwargs):
    atomic(path, value, **kwargs)
    if path.name == sys.argv[3]:
        os._exit(73)
store._atomic = interrupted
store.rename_session('main', sys.argv[2], 'Interrupted name')
'''
    result = subprocess.run([sys.executable, '-c', code, str(tmp_path), meta.session_id, boundary], timeout=10)
    assert result.returncode == 73
    recovered = TranscriptStore(tmp_path, owner_group='main')
    assert not recovered.get_active_session('main').label and not recovered.list_sessions('main')[0]['label']
    for name, data in original.items():
        assert (tmp_path / name).read_bytes() == data, name
    assert not list(tmp_path.glob('.navigation-backup-*'))


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


def test_named_session_archive_reactivation_and_active_catalog_recovery_keep_explicit_label(tmp_path):
    store = TranscriptStore(tmp_path, owner_group='main')
    named = store.create_session('main')
    store.rename_session('main', named.session_id, 'Durable named session')
    replacement = store.reset_session('main')
    rows = {row['session_id']: row for row in store.list_sessions('main')}
    assert rows[named.session_id]['label'] == 'Durable named session' and not rows[replacement.session_id]['label']
    store.activate_session('main', named.session_id)
    assert store.get_active_session('main').label == 'Durable named session'
    (tmp_path / 'catalog.json').write_text('{broken')
    recovered = TranscriptStore(tmp_path, owner_group='main')
    assert recovered.get_active_session('main').label == 'Durable named session'
    assert next(row for row in recovered.list_sessions('main') if row['session_id'] == named.session_id
                )['label'] == 'Durable named session'


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
