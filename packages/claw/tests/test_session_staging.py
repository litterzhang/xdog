"""Reader-responsive staged navigation using actual group, socket, app and target-entry owners."""
from __future__ import annotations

import asyncio
import json
import shutil
import threading
from pathlib import Path
from unittest.mock import patch

import pytest
from xdog.ai.types import AssistantMessage, UserMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.persistence.transcript_convert import messages_to_transcript
from xdog.claw.core.runtime.gateway import GatewayServer
from xdog.claw.core.types import UserInput

ALIAS = 'ext-stagedprovider-local/choice'


def entries(**flags):
    return {'stagedprovider': [{'id': 'flags', 'kind': 'note', 'data': flags}]}


@pytest.mark.parametrize('operation', ['resume', 'branch'])
@pytest.mark.parametrize('scenario', ['success', 'missing', 'failure', 'save-failure', 'cancel', 'commit-cancel'])
async def test_actual_claw_stages_provider_before_pointer_context_and_target_state_changes(
    tmp_path, monkeypatch, operation, scenario,
):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions', root)
    marker = tmp_path / 'stage-entered'
    config = ClawConfig(model=ALIAS, data_dir=str(tmp_path / 'data'), socket_path=str(tmp_path / 'stage.sock'),
        pid_file=str(tmp_path / 'stage.pid'), groups=(GroupDef(id='main', is_main=True),),
        extensions=('stagedprovider',), extension_dir=str(root),
        extension_settings={'stagedprovider': {'marker': str(marker)}})
    calls = []
    def base(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())
    from xdog.claw.core.persistence.transcript_store import TranscriptStore
    seeded = TranscriptStore(tmp_path / 'data' / 'groups' / 'main' / 'sessions', owner_group='main')
    original_meta = seeded.create_session('main')
    seeded.save_extension_entries(original_meta.session_id, entries(allow=True))
    server = GatewayServer(config)
    with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=(ALIAS, base)):
        await server.start()
    runtime = server._orchestrator._runtimes['main']
    store = runtime.transcript_store
    original_meta = store.get_active_session('main') or store.create_session('main')
    original = runtime.get_or_create_session(allow_reset=False)
    await original.initialize_extensions(require_model=True)
    original.agent.replace_messages([UserMessage(content='ORIGINAL-GROUP-CONTEXT'), AssistantMessage()])
    original._restored = True
    store.replace_transcript(original_meta.session_id, messages_to_transcript(original.agent.state.messages))
    original._persisted_count = 2
    target_source = messages_to_transcript([UserMessage(content='TARGET-GROUP-CONTEXT'), AssistantMessage()])
    target_entries = entries(allow=scenario not in {'missing', 'failure'}, fail=scenario == 'failure',
                             hold=scenario == 'cancel')
    if operation == 'resume':
        target = store.create_session('main')
        store.replace_transcript(target.session_id, target_source)
        store.save_extension_entries(target.session_id, target_entries)
        store.activate_session('main', original_meta.session_id)
        target_id, branch = target.session_id, ''
    else:
        store.save_branch(original_meta.session_id, 'target', target_source,
                          metadata={'extension_entries': target_entries, 'label': 'Target provider branch'})
        target_id, branch = original_meta.session_id, 'target'
    tracked = {path: path.read_bytes() for path in store._sessions_dir.iterdir() if path.is_file()}
    old_state, old_plan, old_ext = original.agent.state, original.plan, original.extensions
    old_children, old_mcp = original.children, runtime.mcp
    old_process = old_ext._manager._clients['stagedprovider'].process
    disk_started, disk_release = threading.Event(), threading.Event()
    owners = []
    if scenario == 'commit-cancel':
        navigate = server._orchestrator.navigate_session
        async def capture_owner(*args):
            owners.append(asyncio.current_task())
            return await navigate(*args)
        monkeypatch.setattr(server._orchestrator, 'navigate_session', capture_owner)
        atomic = store._atomic
        def delayed_pointer(path, value, **kwargs):
            if path == store._index_file:
                disk_started.set()
                assert disk_release.wait(8)
            return atomic(path, value, **kwargs)
        monkeypatch.setattr(store, '_atomic', delayed_pointer)
    if scenario == 'save-failure':
        atomic = store._atomic
        def fail_pointer(path, value, **kwargs):
            # Fail AFTER target entries/plan/transcript writes; transaction must restore them.
            if path == store._index_file:
                raise OSError('offline active pointer commit failure')
            return atomic(path, value, **kwargs)
        monkeypatch.setattr(store, '_atomic', fail_pointer)
    ui = ChatApp(config.socket_path)
    reader, writer = await asyncio.open_unix_connection(config.socket_path, limit=4 * 1024 * 1024)
    task = asyncio.create_task(ui._run_connection(reader, writer))
    async def wait(predicate):
        async with asyncio.timeout(10):
            while not predicate():
                ui._poll()
                await asyncio.sleep(0.003)
    try:
        await wait(lambda: ui._connection_ready)
        ui._editor.set_text('RETAINED-GROUP-DRAFT')
        ui._navigate_session(operation, target_id, branch)
        if scenario == 'cancel':
            await wait(marker.exists)
            assert runtime._active_session is original and old_process.returncode is None
            # Reader remains usable while target setup is blocked.
            ui._queue_request({'type': 'ping', 'group_id': 'main', 'saved_source': 0})
            await wait(lambda: not ui._saved_source_supported)
            ui._handle_escape()
        if scenario == 'commit-cancel':
            await wait(disk_started.is_set)
            ui._cancel_session_navigation()
            await wait(lambda: owners[0].cancelling() >= 1)
            assert 'main' in server._orchestrator._navigation_groups
            assert runtime._active_session is original
            ui._cancel_session_navigation()
            await wait(lambda: owners[0].cancelling() >= 2)
            disk_release.set()
        await wait(lambda: ui._session_mutation_id is None and not ui._history_loading)
        assert ui._editor.get_text() == 'RETAINED-GROUP-DRAFT' and not calls
        if scenario in {'success', 'commit-cancel'}:
            active = runtime._active_session
            assert active is not original and active.meta.session_id == target_id
            assert active.agent.state.model == ALIAS and active.agent.context_window == 32000
            assert 'TARGET-GROUP-CONTEXT' in str(active.agent.state.messages)
            observed = json.loads(await active.extensions.command('ext-stagedprovider-status', ''))
            assert observed['starts'] == 1 and observed['session_id'] == target_id
            rows = store.get_extension_entries(target_id)['stagedprovider']
            assert sum('prepared' in row['data'] for row in rows) == 1
            assert runtime.mcp is old_mcp and old_ext._closed
            active.extensions.entries.append('stagedprovider', [{'kind': 'note', 'data': {'adopted': True}}])
            assert 'adopted' in str(store.get_extension_entries(target_id))
            active.plan.change('start', active.plan.state.revision, 'adopted plan')
            assert store.get_plan(target_id)['objective'] == 'adopted plan'
            result = await active.run_turn(UserInput(group_id='main', content='run adopted provider'))
            assert 'STAGED-OWNED-ANSWER' in result.response_text and not calls
        else:
            assert runtime._active_session is original and original.agent.state == old_state
            assert original.plan is old_plan and original.extensions is old_ext and original.children is old_children
            assert runtime.mcp is old_mcp and not old_ext._closed and old_process.returncode is None
            assert store.get_active_session('main').session_id == original_meta.session_id
            assert ui._gateway_session_id == original_meta.session_id
            for path, data in tracked.items():
                assert path.read_bytes() == data, path.name
            assert not list(store._sessions_dir.glob('.navigation-*'))
    finally:
        disk_release.set()
        ui._request_exit()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        writer.close()
        await writer.wait_closed()
        await server.stop()


@pytest.mark.parametrize('outcome', ['success', 'failure', 'cancel'])
async def test_sync_stage_runner_owns_loop_and_settles_cleanup_inside_async_caller(outcome):
    from xdog.claw.core.runtime.session_staging import owned_run

    caller_loop = asyncio.get_running_loop()
    caller_thread = threading.get_ident()
    observed = []

    async def initialize():
        observed.append((asyncio.get_running_loop(), threading.get_ident()))
        try:
            await asyncio.sleep(0)
            if outcome == 'failure':
                raise ValueError('target initialization failed')
            if outcome == 'cancel':
                raise asyncio.CancelledError
            return 'prepared'
        finally:
            await asyncio.sleep(0)
            observed.append('settled')

    if outcome == 'success':
        assert owned_run(initialize) == 'prepared'
    else:
        error = ValueError if outcome == 'failure' else asyncio.CancelledError
        with pytest.raises(error):
            owned_run(initialize)
    stage_loop, stage_thread = observed[0]
    assert stage_loop is not caller_loop and stage_loop.is_closed()
    assert stage_thread != caller_thread and observed[-1] == 'settled'
    assert asyncio.get_running_loop() is caller_loop and not caller_loop.is_closed()
    assert not any(thread.name.startswith('xdog-claw-stage') for thread in threading.enumerate())


async def test_sync_prepared_session_close_owns_cleanup_inside_async_caller():
    from types import SimpleNamespace

    from xdog.claw.core.runtime.session_staging import PreparedSession

    caller_loop = asyncio.get_running_loop()
    observed = []

    async def close_children():
        assert asyncio.get_running_loop() is not caller_loop
        await asyncio.sleep(0)
        observed.append('children')

    async def close_extensions():
        await asyncio.sleep(0)
        observed.append('extensions')

    session = SimpleNamespace(children=SimpleNamespace(supervisor=SimpleNamespace(aclose=close_children)),
                              extensions=SimpleNamespace(aclose=close_extensions),
                              dispose=lambda: observed.append('disposed'))
    PreparedSession(session, None, None).close()
    assert observed == ['children', 'extensions', 'disposed']
    assert asyncio.get_running_loop() is caller_loop and not caller_loop.is_closed()
    assert not any(thread.name.startswith('xdog-claw-stage') for thread in threading.enumerate())
