"""Manual compaction through both actual applications, selected adapters, and disk owners."""
from __future__ import annotations

import asyncio
import base64
import json
import shutil
import threading
from pathlib import Path
from unittest.mock import patch

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.ai.types import (
    AssistantMessage,
    ImageContent,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.persistence.transcript_convert import messages_to_transcript
from xdog.claw.core.runtime.gateway import GatewayServer
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.permissions import PermissionManager
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.settings_manager import SettingsManager
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode


def history():
    return [UserMessage(content='old work ' + 'a' * 5000),
        AssistantMessage(content=(ToolCall(id='read', name='filesystem',
                                         arguments={'action': 'read', 'path': 'old.py'}),)),
        ToolResultMessage(tool_call_id='read', tool_name='filesystem',
                          content=(TextContent(text='IMPORTANT-TOOL-RESULT'),)),
        AssistantMessage(content=(TextContent(text='old work done ' + 'b' * 1000),)),
        UserMessage(content='more work ' + 'c' * 3000),
        AssistantMessage(content=(TextContent(text='more work done ' + 'd' * 1000),)),
        UserMessage(content='recent user request'), AssistantMessage(content=(TextContent(text='recent answer'),))]


@pytest.mark.parametrize('kind', ['coding', 'claw'])
@pytest.mark.parametrize('scenario', ['success', 'empty', 'cancel', 'commit-cancel', 'disk-failure',
                                      'empty-history', 'extension', 'extension-failure', 'checkpoint-failure',
                                      'hook-override',
                                      'hook-cancel', 'hook-invalid', 'hook-bad-boundary', 'hook-continue',
                                      'hook-conflict', 'hook-hold', 'hook-commit-cancel',
                                      'hook-auto-override', 'hook-auto-cancel', 'hook-override-large', 'live-tail'])
async def test_actual_manual_compaction_selected_adapter_ownership_failure_and_persistence(
    tmp_path, kind, scenario, monkeypatch,
):
    calls, cleanup = [], threading.Event()
    entered = threading.Event()
    disk_entered, disk_released = threading.Event(), threading.Event()
    options_seen = []
    compact_tasks = []
    def stream(model, context, options):
        calls.append((model, context))
        options_seen.append(options)
        entered.set()
        if scenario == 'cancel':
            async def blocked():
                try:
                    await asyncio.sleep(60)
                    yield None
                finally:
                    cleanup.set()
            return EventStream.from_async_generator(blocked())
        return EventStream.empty(AssistantMessage(content=(TextContent(
            text='' if scenario == 'empty' else 'OWNED-COMPACT-SUMMARY'),)))
    server = task = writer = None
    is_extension = scenario.startswith('extension')
    is_hook = scenario.startswith('hook-')
    name = 'providermock' if is_extension else 'compactor'
    is_auto = scenario.startswith('hook-auto-')
    hook_mode = scenario.removeprefix('hook-').removeprefix('auto-')
    if hook_mode in {'commit-cancel', 'override-large'}:
        hook_mode = 'override'
    is_commit_cancel = scenario in {'commit-cancel', 'hook-commit-cancel'}
    hook_marker = tmp_path / 'hook-entered'
    root = tmp_path / 'extensions'
    settings_path = tmp_path / 'settings.json'
    if is_extension or is_hook:
        shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions', root)
        settings_path.write_text(json.dumps({'extensions': [name], 'extension_dir': str(root),
                                            'extension_settings': {
                                                'compactor': {'mode': hook_mode, 'marker': str(hook_marker)}}}))
    marker = ('CUSTOM-COMPACTION-SUMMARY' if scenario in {
        'hook-override', 'hook-commit-cancel', 'hook-auto-override', 'hook-override-large'} else
              'EXT-PROVIDER-ANSWER' if is_extension else 'OWNED-COMPACT-SUMMARY')
    if kind == 'coding':
        store = SessionManager(tmp_path / 'sessions')
        session = AgentSession(agent=Agent(stream, config=AgentConfig(model='initial/model', context_window=8000)),
            session_data=store.create_session(working_dir=tmp_path), session_manager=store,
            settings=SettingsManager(global_path=settings_path), working_dir=tmp_path,
            tool_registry=None, bash=None, permissions=PermissionManager('deny'))
        ui = InteractiveMode(session)
    else:
        config = ClawConfig(model='initial/model', data_dir=str(tmp_path / 'data'),
            socket_path=str(tmp_path / 'compact.sock'), pid_file=str(tmp_path / 'compact.pid'),
            groups=(GroupDef(id='main', is_main=True),),
            extensions=(name,) if is_extension or is_hook else (),
            extension_settings={'compactor': {'mode': hook_mode, 'marker': str(hook_marker)}},
            extension_dir=str(root) if is_extension or is_hook else '')
        server = GatewayServer(config)
        with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=('initial/model', stream)):
            await server.start()
        ui = ChatApp(config.socket_path)
        reader, writer = await asyncio.open_unix_connection(config.socket_path)
        task = asyncio.create_task(ui._run_connection(reader, writer))
    async def wait(predicate):
        async with asyncio.timeout(8):
            while not predicate():
                ui._poll()
                await asyncio.sleep(0.003)
    try:
        if kind == 'claw':
            await wait(lambda: ui._connection_ready)
            assert ui._compaction_supported
            ui._skills_command('list')  # Bootstrap actual idle owner without a model turn.
            await wait(lambda: not ui._skills_request)
            session = server._orchestrator._runtimes['main']._active_session
            session._runtime.flush_runner = None
            session._restored = True
        original = [] if scenario == 'empty-history' else history()
        if scenario == 'hook-override-large':
            original[0] = UserMessage(content=(TextContent(text='older searchable body ' + 'x' * 5000),
                ImageContent(data=base64.b64encode(b'image' * (1500 * 1024)).decode(), mime_type='image/png')))
            original[1] = AssistantMessage(content=(ThinkingContent(thinking='signed old reasoning',
                thinking_signature='OLD-OPAQUE-SIGNATURE'), *original[1].content))
            if kind == 'claw':
                session._runtime.context_window = 1000
        if is_extension:
            alias = 'ext-providermock-local/' + ('failure' if scenario == 'extension-failure' else 'echo')
            if kind == 'coding':
                await session.initialize_extensions()
                session.set_model(alias)
            else:
                await server._orchestrator.extension_models('main', session.meta.session_id, alias)
        else:
            session.agent.set_model('selected/offline')
        session.agent.replace_messages(original)
        if kind == 'coding':
            session._persist()
            def persisted():
                return session.session_manager.load_session(session.session_id).messages
            if scenario == 'disk-failure':
                monkeypatch.setattr(session.session_manager, 'save_session',
                                   lambda *_: (_ for _ in ()).throw(OSError()))
        else:
            session._store.replace_transcript(session.meta.session_id, messages_to_transcript(original))
            session._persisted_count = len(original)
            def persisted():
                from xdog.claw.core.persistence.transcript_convert import transcript_to_messages
                return transcript_to_messages(session._store.load_transcript(session.meta.session_id))
            if scenario == 'disk-failure':
                monkeypatch.setattr(session._store, 'replace_transcript', lambda *_: (_ for _ in ()).throw(OSError()))
        persisted_before = persisted()
        original_disk = list(original)
        if scenario == 'live-tail':
            original = [*original, UserMessage(content='UNSAVED-LIVE-TAIL'),
                        AssistantMessage(content=(TextContent(text='UNSAVED-LIVE-ANSWER'),))]
            session.agent.replace_messages(original)
        if scenario == 'checkpoint-failure':
            method_owner = session.session_manager if kind == 'coding' else session._store
            method = 'save_session' if kind == 'coding' else 'save_compaction_checkpoint'
            monkeypatch.setattr(method_owner, method, lambda *args, **kwargs: (_ for _ in ()).throw(OSError()))
        if is_commit_cancel:
            if kind == 'claw':
                original_compact = server._orchestrator.compact_session
                async def capture_task(*args):
                    compact_tasks.append(asyncio.current_task())
                    return await original_compact(*args)
                monkeypatch.setattr(server._orchestrator, 'compact_session', capture_task)
            owner = session.session_manager if kind == 'coding' else session._store
            method = 'save_session' if kind == 'coding' else 'replace_transcript'
            original_save = getattr(owner, method)
            def delayed_save(*args):
                disk_entered.set()
                assert disk_released.wait(5)
                original_save(*args)
            monkeypatch.setattr(owner, method, delayed_save)
        # Any ai.load fallback after owner construction is a test failure.
        monkeypatch.setattr('xdog.ai.load', lambda: (_ for _ in ()).throw(AssertionError('primary fallback forbidden')))
        ui._editor.set_text('retained compact draft')
        if is_auto:
            if kind == 'coding':
                session.context_window = 2000
            else:
                session._runtime.context_window = 2000
            ui._handle_submit('continue normal turn after automatic compaction')
        elif kind == 'coding':
            ui._handle_slash_command('compact', 'Keep tool evidence and outstanding tasks')
        else:
            ui._compact_command('Keep tool evidence and outstanding tasks')
        if scenario in {'cancel', 'hook-hold'} or is_commit_cancel:
            await wait(disk_entered.is_set if is_commit_cancel
                       else hook_marker.exists if scenario == 'hook-hold' else entered.is_set)
            assert session._compacting
            with pytest.raises(RuntimeError, match='Compaction'):
                session._children_idle()
            if scenario == 'hook-hold':
                owned_worker = session.extensions._manager._clients['compactor'].process
                assert owned_worker.returncode is None
            if kind == 'coding':
                ui._handle_slash_command('compact', 'cancel')
            else:
                ui._handle_escape()
            if is_commit_cancel:
                compact_owner = ui._command_task if kind == 'coding' else compact_tasks[0]
                await wait(lambda: compact_owner.cancelling() >= 1)
                assert session._compacting  # Reservation cannot leave while owned disk work is live.
                if kind == 'coding':
                    ui._command_loop_ref.call_soon_threadsafe(compact_owner.cancel)
                else:
                    ui._cancel_compaction()
                await wait(lambda: compact_owner.cancelling() >= 2)
                disk_released.set()
        if is_auto:
            await wait(lambda: len(calls) == 1 and (not ui._worker_active if kind == 'coding'
                                                   else ui._active_run_id is None))
        else:
            await wait(lambda: ui._command is None if kind == 'coding' else ui._compact_pending is None)
        assert not session._compacting and not session.agent.is_streaming
        if not is_auto:
            assert ui._editor.get_text() == 'retained compact draft'
        if scenario in {'success', 'extension', 'commit-cancel', 'hook-override', 'hook-continue',
                        'hook-commit-cancel', 'hook-auto-override', 'hook-override-large', 'live-tail'}:
            assert len(session.agent.state.messages) < len(original), str(ui._chat_log.render(120))
            assert marker in str(session.agent.state.messages)
            assert 'recent answer' in str(session.agent.state.messages)
            if is_commit_cancel:
                assert 'inspect /transcript' in str(ui._chat_log.render(120))
            elif not is_auto:
                assert 'Compacted:' in str(ui._chat_log.render(120))
            disk = persisted()
            assert len(disk) == len(session.agent.state.messages)
            assert marker in str(disk) and 'recent answer' in str(disk)
            if kind == 'coding':
                from xdog.agent.messages import messages_to_dicts
                loaded = session.session_manager.load_session(session.session_id)
                assert loaded.to_dict() == session.session_data.to_dict()
                recovery_id = loaded.compaction['recovery_branch_id']
                recovery = next(branch for branch in loaded.branches if branch['branch_id'] == recovery_id)
                assert recovery['messages'] == messages_to_dicts(original)
                assert recovery['recovery_kind'] == 'compaction' and not loaded.active_branch
                assert recovery['label'].startswith('Before compaction #1')
                assert recovery['plan']['mode'] == 'off'
                entries = ui._navigation_entries('tree|' + session.session_id + '|')
                assert any(entry.branch_id == recovery_id and 'Before compaction' in entry.title for entry in entries)
                session.plan.change('start', session.plan.state.revision, 'post-compaction plan')
                assert marker in str(persisted())
                session.extensions.entries.append('fixture', [{'kind': 'note', 'data': 'post-compaction entry'}])
                assert marker in str(persisted())
            else:
                expected = messages_to_transcript(list(session.agent.state.messages))
                actual = messages_to_transcript(disk)
                for row in [*actual, *expected]:
                    row.pop('timestamp', None)
                    if is_auto:
                        row.pop('usage', None)  # Claw persists explicit fallback estimates for this offline turn.
                assert actual == expected
                metadata = session._store.load_transcript(session.meta.session_id)[0]['compaction']
                recovery_id = metadata['recovery_branch_id']
                restored_original = session._store.load_branch(session.meta.session_id, recovery_id)
                from xdog.claw.core.persistence.transcript_convert import transcript_to_messages
                if scenario == 'live-tail':
                    assert len(restored_original) == len(original)
                    assert 'UNSAVED-LIVE-TAIL' in str(restored_original)
                    assert transcript_to_messages(restored_original[:len(original_disk)]) == persisted_before
                else:
                    assert transcript_to_messages(restored_original) == persisted_before
                entries = await server._orchestrator.navigation_entries('main', 'tree|' + session.meta.session_id + '|')
                assert any(entry['branch_id'] == recovery_id and 'Before compaction' in entry['title']
                           for entry in entries if entry['kind'] == 'branch')
        elif scenario == 'hook-auto-cancel':
            assert list(session.agent.state.messages)[:len(original)] == original
            assert persisted()[:len(original)] == persisted_before
        else:
            assert list(session.agent.state.messages) == original
            assert persisted() == persisted_before
            assert 'Compacted:' not in str(ui._chat_log.render(120))
        if scenario in {'empty', 'cancel', 'empty-history', 'checkpoint-failure', 'extension-failure',
                        'hook-cancel', 'hook-invalid', 'hook-bad-boundary', 'hook-conflict', 'hook-hold',
                        'hook-auto-cancel'}:
            branches = (session.session_data.branches if kind == 'coding'
                        else session._store.branch_metadata(session.meta.session_id))
            assert not any(branch.get('recovery_kind') == 'compaction' for branch in branches)
        if is_hook:
            if scenario in {'hook-invalid', 'hook-conflict', 'hook-hold'}:
                assert 'compactor' in session.extensions._failed
                if scenario == 'hook-hold':
                    assert owned_worker.returncode is not None
            else:
                observed = json.loads(await session.extensions.command('ext-compactor-status', ''))
                assert observed['before'] == 1 and observed['prepared_count'] == len(original)
                assert observed['no_borrowed_authority'] and observed['has_tool_result']
                assert observed['focus'] == ('' if is_auto else 'Keep tool evidence and outstanding tasks')
                assert observed['model'] == 'selected/offline'
                assert observed['after'] == (1 if scenario in {
                    'hook-override', 'hook-continue', 'hook-commit-cancel', 'hook-auto-override',
                    'hook-override-large'} else 0)
                assert observed['automatic'] is is_auto
                if scenario in {'hook-override', 'hook-commit-cancel', 'hook-auto-override', 'hook-override-large'}:
                    assert observed['committed']['source'] == 'compactor'
                    assert observed['committed']['first_kept_index'] == 4
                    assert observed['sibling_saw_original']
                    if scenario == 'hook-override-large':
                        assert observed['image_bytes'] > 8 * 1024 * 1024
                    assert 'MUTATED-PRIVATE-COPY' not in str(session.agent.state.messages)
                    if kind == 'coding':
                        saved = session.session_manager.load_session(session.session_id)
                        assert saved.compaction['source'] == 'compactor'
                    else:
                        saved = session._store.load_transcript(session.meta.session_id)[0]
                        assert saved['compaction']['source'] == 'compactor'
                    # Branch serialization/restoration must carry the exact owned
                    # metadata, and a future compaction must recover its clean summary.
                    branch = session.create_branch()
                    if kind == 'coding':
                        session.session_data.compaction = None
                    else:
                        session._compaction_metadata = None
                    assert session.restore_branch(branch)
                    if kind == 'coding':
                        assert session.session_data.compaction['source'] == 'compactor'
                    else:
                        from xdog.claw.core.compaction.transcript import extract_previous_summary
                        restored = session._store.load_transcript(session.meta.session_id)
                        assert restored[0]['compaction']['source'] == 'compactor'
                        assert 'CUSTOM-COMPACTION-SUMMARY' in extract_previous_summary(restored)
            assert len(calls) == (1 if scenario == 'hook-continue' or is_auto else 0)
        elif is_extension:
            assert not calls
            assert await session.extensions.command('ext-providermock-count', '') == '1'
            assert await session.extensions.command('ext-providermock-closed', '') == '1'
        elif scenario == 'empty-history':
            assert not calls
        else:
            assert len(calls) == 1 and calls[0][0] == 'selected/offline'
            assert not calls[0][1].tools
            assert 'Keep tool evidence' in str(calls[0][1].messages)
            assert 'IMPORTANT-TOOL-RESULT' in str(calls[0][1].messages)
        if scenario == 'cancel':
            assert cleanup.is_set() and options_seen[0].cancel.is_set()
        if scenario == 'success':
            before_timeline = session.agent.state.messages
            ui._transcript_command('timeline')
            await wait(lambda: ui._transcript_panel is not None and ui._transcript_panel.settled
                       and 'old work' in str(ui._transcript_panel.records))
            timeline = ui._transcript_panel
            assert 'timeline' in timeline.caption.lower()
            assert session.agent.state.messages == before_timeline and len(calls) == 1
            assert any('Before compaction' in record.title for record in timeline.records)
            recent = [record for record in timeline.records if record.body == 'recent answer']
            assert len(recent) == 1 and 'Current context' in recent[0].title
            assert 'Before compaction' in recent[0].title
            timeline.search('old work')
            await wait(lambda: timeline.settled and bool(timeline._view.matches))
            ui._close_transcript()
            assert ui._editor.get_text() == 'retained compact draft'
            from xdog.tui.keys import KeyEvent
            if kind == 'coding':
                ui._open_navigation('tree|' + session.session_id + '|')
            else:
                ui._open_navigation('tree|' + session.meta.session_id + '|')
            await wait(lambda: ui._navigation_panel is not None and not ui._navigation_panel._view.busy
                       and any(entry.branch_id == recovery_id and entry.kind == 'branch'
                               for entry in ui._navigation_panel._view.entries))
            panel = ui._navigation_panel
            panel._selected = next(index for index, entry in enumerate(panel._view.entries)
                                   if entry.branch_id == recovery_id and entry.kind == 'branch')
            ui._tui._dispatch_input(KeyEvent('enter'))
            await wait(lambda: (ui._command is None and ui._navigation_panel is None) if kind == 'coding'
                       else ui._session_mutation_id is None and not ui._history_loading)
            if kind == 'claw':
                session = server._orchestrator._runtimes['main'].get_or_create_session(allow_reset=False)
                session._restore_messages()
            from xdog.agent.messages import messages_to_dicts
            recovered = messages_to_dicts(list(session.agent.state.messages))
            expected = messages_to_dicts(original)
            for row in [*recovered, *expected]:
                row.pop('timestamp', None)
            assert recovered == expected
            assert session.plan.state.mode == 'off'
            assert ui._editor.get_text() == 'retained compact draft'
            assert len(calls) == 1  # Browsing/restoring never submits a model turn.
            if kind == 'coding':
                assert any(branch['label'].startswith('Return checkpoint') for branch in session.session_data.branches)
            else:
                assert any(branch['label'].startswith('Return checkpoint')
                           for branch in session._store.branch_metadata(session.meta.session_id))
        if kind == 'coding':
            assert session.permissions.mode == 'deny'
        else:
            assert not server._orchestrator.is_group_running('main')
    finally:
        disk_released.set()
        ui._request_exit()
        if 'session' in locals():
            await session.children.supervisor.aclose()
            await session.extensions.aclose()
        if kind == 'coding':
            session.dispose()
        else:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            writer.close()
            await writer.wait_closed()
            await server.stop()


@pytest.mark.parametrize('scenario', ['legacy', 'stale', 'malformed', 'unsolicited', 'withdrawn'])
def test_claw_compaction_legacy_and_invalid_receipts_never_become_agent_input(scenario):
    ui = ChatApp('/tmp/compaction-test-unconnected.sock')
    ui._connection_ready = True
    ui._compaction_supported = scenario != 'legacy'
    ui._gateway_session_id = 'current'
    if scenario == 'legacy':
        ui._compact_command('keep decisions')
        assert ui._compact_pending is None and ui._send_queue.empty()
    else:
        ui._compact_pending = None if scenario == 'unsolicited' else ('request', 'current')
        if scenario == 'withdrawn':
            ui._compaction_supported = False
        ui._handle_response({'type': 'session_compact_result', 'request_id': 'request', 'group_id': ui.group_id,
            'session_id': 'old' if scenario == 'stale' else 'current', 'success': True, 'changed': True,
            'before': [] if scenario == 'malformed' else 8, 'after': 4})
        assert 'Compacted:' not in str(ui._chat_log.render(120))
        assert ui._gateway_session_id == 'current'
    ui._request_exit()


async def test_claw_coalesced_cancel_before_job_start_receipt_reservation_and_no_replay(tmp_path):
    config = ClawConfig(model='test/model', data_dir=str(tmp_path / 'data'),
        socket_path=str(tmp_path / 'cancel.sock'), pid_file=str(tmp_path / 'cancel.pid'),
        groups=(GroupDef(id='main', is_main=True),))
    calls = []
    def stream(*args):
        calls.append(args)
        raise AssertionError('Canceled controls must not call a model')
    server = GatewayServer(config)
    with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=('test/model', stream)):
        await server.start()
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    async def receive(kind, identifier=''):
        async with asyncio.timeout(8):
            while True:
                frame = json.loads(await reader.readline())
                if frame.get('type') == kind and (not identifier or frame.get('request_id') == identifier):
                    return frame
    try:
        writer.write((json.dumps({'type': 'ping', 'group_id': 'main', 'session_compaction': 1}) + '\n').encode())
        await writer.drain()
        assert (await receive('pong'))['session_compaction'] == 1
        request = {'type': 'session_compact', 'request_id': 'coalesced', 'group_id': 'main', 'session_id': ''}
        cancel = {**request, 'type': 'session_compact_cancel'}
        writer.write(('\n'.join(json.dumps(item) for item in (request, cancel)) + '\n').encode())
        await writer.drain()
        receipt = await receive('session_compact_result', 'coalesced')
        assert receipt['canceled'] is True and 'before summary started' in receipt['error']
        await asyncio.sleep(0)
        assert server._orchestrator.try_reserve_group('main')
        server._orchestrator.release_group_reservation('main')
        writer.write((json.dumps(request) + '\n').encode())
        await writer.drain()
        assert (await receive('session_compact_result', 'coalesced'))['success'] is False
        writer.write((json.dumps({'type': 'ping', 'group_id': 'main', 'session_compaction': 0}) + '\n').encode())
        await writer.drain()
        assert (await receive('pong'))['session_compaction'] == 0
        writer.write((json.dumps({**request, 'request_id': 'withdrawn'}) + '\n').encode())
        await writer.drain()
        assert (await receive('session_compact_result', 'withdrawn'))['success'] is False
        assert not calls
    finally:
        writer.close()
        await writer.wait_closed()
        await server.stop()
