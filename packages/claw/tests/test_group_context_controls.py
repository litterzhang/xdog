"""One group main Agent: context rotation is not historical route activation."""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import Mock

import pytest
from xdog.claw.core.runtime.group import GroupRuntime
from xdog.claw.core.types import Group

from packages.claw.tests.test_full_transcript_source import gateway as gateway
from packages.claw.tests.test_full_transcript_source import read, send


@pytest.fixture
async def owner(tmp_path):
    runtime = GroupRuntime(group=Group(id='main'), data_dir=tmp_path / 'data',
                           model='test/dummy', stream_fn=Mock(side_effect=AssertionError('No model call expected')))
    try:
        yield runtime
    finally:
        if runtime._active_session is not None:
            await runtime._active_session.children.supervisor.aclose()
            await runtime._active_session.extensions.aclose()
            runtime._active_session.dispose()
        for extension in runtime._retired_extensions:
            await extension.aclose()
        runtime.mcp.close()


@pytest.mark.parametrize('daily,idle', [(True, False), (False, True), (True, True)])
async def test_idle_automatic_context_rotation_uses_runtime_resources_not_goal_policy(owner, monkeypatch, daily, idle):
    runtime = owner
    session = runtime.get_or_create_session(allow_reset=False)
    monkeypatch.setattr(runtime.transcript_store, 'needs_daily_reset', lambda *args: daily)
    monkeypatch.setattr(runtime.transcript_store, 'needs_idle_reset', lambda *args: idle)
    fresh = runtime.get_or_create_session()
    assert fresh is not session and fresh.meta.session_id != session.meta.session_id
    assert runtime.capabilities.get("goal") is None


async def test_manual_new_retains_workspace_and_history_but_never_activates_old_route(owner):
    runtime = owner
    first = runtime.get_or_create_session(allow_reset=False)
    identifier = first.meta.session_id
    runtime.transcript_store.append_turn(identifier, {'role': 'user', 'content': 'Historical evidence'})
    marker = runtime.workspace_dir / 'notes' / 'contract.md'
    marker.parent.mkdir(parents=True)
    marker.write_text('Original workspace notes')
    runtime.navigate_session('new', '', '', -1, identifier)
    assert runtime._active_session is None and runtime.get_new_session_id() != identifier
    assert marker.read_text() == 'Original workspace notes'
    assert runtime.transcript_store.load_transcript(identifier)[0]['content'] == 'Historical evidence'
    fresh = runtime.get_or_create_session(allow_reset=False)
    with pytest.raises(ValueError, match='read-only'):
        runtime.navigate_session('resume', identifier, '', -1, fresh.meta.session_id)
    assert runtime._active_session is fresh


async def test_rotation_requires_child_idle_and_nonstreaming_parent(owner, monkeypatch):
    runtime = owner
    session = runtime.get_or_create_session(allow_reset=False)
    identifier = session.meta.session_id
    with monkeypatch.context() as child:
        child.setattr(session, '_children_idle', Mock(side_effect=RuntimeError('Child work is active')))
        with pytest.raises(RuntimeError, match='Child work'):
            runtime.navigate_session('new', '', '', -1, identifier)
        with pytest.raises(RuntimeError, match='Child work'):
            runtime.reset_session()
        assert runtime._active_session is session and runtime.get_new_session_id() == identifier
    session.agent.state.is_streaming = True
    try:
        with pytest.raises(RuntimeError, match='idle agent'):
            runtime.reset_session()
        assert runtime._active_session is session and runtime.get_new_session_id() == identifier
    finally:
        session.agent.state.is_streaming = False
    runtime.reset_session()


@pytest.mark.parametrize('args', [('old', '', -1), ('', 'legacy', -1), ('', '', 0)])
async def test_new_cannot_smuggle_history_target_and_cancel_has_no_effect(owner, args):
    runtime = owner
    meta = runtime.transcript_store.create_session('main')
    with pytest.raises(ValueError, match='cannot select'):
        runtime.navigate_session('new', *args, meta.session_id)
    with pytest.raises(asyncio.CancelledError):
        runtime.navigate_session('new', '', '', -1, meta.session_id, alive=lambda: False)
    assert runtime.get_new_session_id() == meta.session_id and runtime._active_session is None


@pytest.mark.parametrize('factory', [False, True])
@pytest.mark.parametrize('custom_workspace', [False, True])
def test_optional_capabilities_do_not_change_workspace_in_both_construction_paths(
    tmp_path, monkeypatch, factory, custom_workspace,
):
    from xdog.claw.core.runtime import group as module
    monkeypatch.setattr(module, '_get_model_limits', lambda model: (200_000, 0))
    monkeypatch.setattr(module, 'resolve_model_name', lambda configured, fallback: '')
    group = Group(id='main', workspace=str(tmp_path / 'custom') if custom_workspace else '')
    runtime = (GroupRuntime.create(group, tmp_path / 'data') if factory else
               GroupRuntime(group=group, data_dir=tmp_path / 'data'))
    try:
        expected = Path(group.workspace) if group.workspace else tmp_path / 'data/groups/main/workspace'
        assert runtime.workspace_dir == expected
        assert runtime.capabilities.get("goal") is None and not (tmp_path / 'data/groups/main/goals').exists()
    finally:
        runtime.mcp.close()


@pytest.mark.parametrize('action', ['resume', 'fork', 'branch', 'goto'])
async def test_old_wire_operations_are_explicitly_rejected_without_touching_history(gateway, action):
    server, path, calls = gateway
    runtime = server._orchestrator._runtimes['main']
    meta = runtime.transcript_store.create_session('main')
    runtime.transcript_store.append_turn(meta.session_id, {'role': 'user', 'content': 'Untouched history'})
    reader, writer = await asyncio.open_unix_connection(path)
    try:
        await send(writer, dict(type='session_control', request_id='retired', group_id='main', action=action,
                               session_id=meta.session_id, branch_id='legacy', index=0,
                               expected_session=meta.session_id))
        result = await read(reader, 'session_result')
        assert result['success'] is False and 'read-only' in result['error']
        assert runtime.get_new_session_id() == meta.session_id and runtime._active_session is None
        assert runtime.transcript_store.load_transcript(meta.session_id)[0]['content'] == 'Untouched history'
        assert not runtime.transcript_store.list_branches(meta.session_id) and not calls
        await send(writer, dict(type='ping'))
        assert (await read(reader, 'pong'))['session_id'] == meta.session_id
    finally:
        writer.close()
        await writer.wait_closed()


async def test_reset_child_busy_returns_error_and_keeps_gateway_alive(gateway, monkeypatch):
    server, path, calls = gateway
    runtime = server._orchestrator._runtimes['main']
    session = runtime.get_or_create_session(allow_reset=False)
    identifier = session.meta.session_id
    reader, writer = await asyncio.open_unix_connection(path)
    try:
        with monkeypatch.context() as child:
            child.setattr(session, '_children_idle', Mock(side_effect=RuntimeError('Child work is active')))
            await send(writer, dict(type='reset', group_id='main'))
            assert 'Child work' in (await read(reader, 'error'))['message']
            await send(writer, dict(type='ping'))
            assert (await read(reader, 'pong'))['session_id'] == identifier
        await send(writer, dict(type='reset', group_id='main'))
        ack = await read(reader, 'reset_ack')
        assert ack['session_id'] != identifier and not calls
    finally:
        writer.close()
        await writer.wait_closed()




async def test_archived_history_and_legacy_branch_can_be_read_and_exported_without_activation(gateway, tmp_path):
    import json
    import threading

    from xdog.claw.channels.tui.source_reader import SourceReceiver
    from xdog.tui.transcript_transfer import TransferOptions, prepare_transfer, write_snapshot

    server, path, calls = gateway
    runtime = server._orchestrator._runtimes['main']
    store = runtime.transcript_store
    archived = store.create_session('main')
    store.append_turn(archived.session_id, {'role': 'user', 'content': 'ARCHIVED-EVIDENCE'})
    # Existing legacy archive fixture; no branch-writing/activation API.
    store._path(archived.session_id, 'legacy').write_text(
        json.dumps({'role': 'assistant', 'content': 'LEGACY-BRANCH-EVIDENCE'}) + '\n')
    current = store.reset_session('main')
    reader, writer = await asyncio.open_unix_connection(path)
    try:
        await send(writer, dict(type='ping', group_id='main', saved_source=1))
        assert (await read(reader, 'pong'))['saved_source'] == 1
        for branch, evidence in [('', 'ARCHIVED-EVIDENCE'), ('legacy', 'LEGACY-BRANCH-EVIDENCE')]:
            events = []
            receiver = SourceReceiver(events.append)
            receiver.expect('saved', 'main', current.session_id,
                            target_session_id=archived.session_id, branch_id=branch)
            try:
                await send(writer, dict(type='saved_source_read', request_id='saved', group_id='main',
                                       session_id=current.session_id, target_session_id=archived.session_id,
                                       branch_id=branch))
                async with asyncio.timeout(5):
                    while not events or events[-1]['type'] not in {'source_ready', 'source_failed'}:
                        message = json.loads(await reader.readline())
                        receiver.feed(message)
                assert events[-1]['type'] == 'source_ready', events
                records = events[-1]['records']
                assert evidence in '\n'.join(record.body for record in records)
                prepared = prepare_transfer(TransferOptions('export', 'conversation', 'md'), records, 'Saved history')
                target = tmp_path / ('legacy.md' if branch else 'archived.md')
                write_snapshot(prepared.data, target, threading.Event())
                assert evidence in target.read_text()
            finally:
                receiver.close()
            assert runtime.get_new_session_id() == current.session_id and runtime._active_session is None
            assert store.load_transcript(archived.session_id)[0]['content'] == 'ARCHIVED-EVIDENCE'
            assert not calls
    finally:
        writer.close()
        await writer.wait_closed()
