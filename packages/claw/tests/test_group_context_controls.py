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
    goals = Mock()
    goals.has_unfinished_goals.return_value = False
    runtime = GroupRuntime(group=Group(id='main'), data_dir=tmp_path / 'data', goal_manager=goals,
                           model='test/dummy', stream_fn=Mock(side_effect=AssertionError('No model call expected')))
    try:
        yield runtime, goals
    finally:
        if runtime._active_session is not None:
            await runtime._active_session.children.supervisor.aclose()
            await runtime._active_session.extensions.aclose()
            runtime._active_session.dispose()
        for extension in runtime._retired_extensions:
            await extension.aclose()
        runtime.mcp.close()


@pytest.mark.parametrize('daily,idle', [(True, False), (False, True), (True, True)])
async def test_unfinished_group_goal_suppresses_automatic_context_rotation(owner, monkeypatch, daily, idle):
    runtime, goals = owner
    session = runtime.get_or_create_session(allow_reset=False)
    goals.has_unfinished_goals.return_value = True
    monkeypatch.setattr(runtime.transcript_store, 'needs_daily_reset', lambda *args: daily)
    monkeypatch.setattr(runtime.transcript_store, 'needs_idle_reset', lambda *args: idle)
    assert runtime.get_or_create_session() is session
    assert runtime.get_new_session_id() == session.meta.session_id
    goals.has_unfinished_goals.return_value = False
    fresh = runtime.get_or_create_session()
    assert fresh is not session and fresh.meta.session_id != session.meta.session_id
    assert runtime.goal_manager is goals


async def test_manual_new_retains_group_goals_and_history_but_never_activates_old_route(owner):
    runtime, goals = owner
    goals.has_unfinished_goals.return_value = True
    first = runtime.get_or_create_session(allow_reset=False)
    identifier = first.meta.session_id
    runtime.transcript_store.append_turn(identifier, {'role': 'user', 'content': 'Historical evidence'})
    marker = runtime.workspace_dir / '.xdog' / 'goals' / 'goal' / 'goal.md'
    marker.parent.mkdir(parents=True)
    marker.write_text('Original Goal conditions')
    runtime.navigate_session('new', '', '', -1, identifier)
    assert runtime._active_session is None and runtime.get_new_session_id() != identifier
    assert runtime.goal_manager is goals and marker.read_text() == 'Original Goal conditions'
    assert runtime.transcript_store.load_transcript(identifier)[0]['content'] == 'Historical evidence'
    fresh = runtime.get_or_create_session(allow_reset=False)
    with pytest.raises(ValueError, match='read-only'):
        runtime.navigate_session('resume', identifier, '', -1, fresh.meta.session_id)
    assert runtime._active_session is fresh
    goals.attach.assert_called_once_with(runtime)


async def test_rotation_requires_child_idle_and_nonstreaming_parent(owner, monkeypatch):
    runtime, goals = owner
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
    assert runtime.goal_manager is goals


@pytest.mark.parametrize('args', [('old', '', -1), ('', 'legacy', -1), ('', '', 0)])
async def test_new_cannot_smuggle_history_target_and_cancel_has_no_effect(owner, args):
    runtime, _ = owner
    meta = runtime.transcript_store.create_session('main')
    with pytest.raises(ValueError, match='cannot select'):
        runtime.navigate_session('new', *args, meta.session_id)
    with pytest.raises(asyncio.CancelledError):
        runtime.navigate_session('new', '', '', -1, meta.session_id, alive=lambda: False)
    assert runtime.get_new_session_id() == meta.session_id and runtime._active_session is None


@pytest.mark.parametrize('factory', [False, True])
@pytest.mark.parametrize('custom_workspace', [False, True])
def test_goal_workspace_and_attach_are_consistent_in_both_construction_paths(
    tmp_path, monkeypatch, factory, custom_workspace,
):
    from xdog.claw.core.runtime import group as module
    managers = []
    def create_manager(**kwargs):
        manager = Mock()
        managers.append((manager, kwargs))
        return manager
    monkeypatch.setattr(module, 'GoalManager', create_manager)
    monkeypatch.setattr(module, '_get_model_limits', lambda model: (200_000, 0))
    monkeypatch.setattr(module, 'resolve_model_name', lambda configured, fallback: '')
    group = Group(id='main', workspace=str(tmp_path / 'custom') if custom_workspace else '')
    runtime = (GroupRuntime.create(group, tmp_path / 'data') if factory else
               GroupRuntime(group=group, data_dir=tmp_path / 'data'))
    try:
        expected = Path(group.workspace) if group.workspace else tmp_path / 'data/groups/main/workspace'
        assert runtime.workspace_dir == expected
        assert managers[0][1]['goals_dir'] == expected / '.xdog' / 'goals'
        assert runtime.goal_manager is managers[0][0]
        managers[0][0].attach.assert_called_once_with(runtime)
        managers[0][0].goals_summary.return_value = 'Group Goal facade'
        assert runtime.goals_summary() == 'Group Goal facade'
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


async def test_real_group_goal_survives_context_rotation_and_disables_daily_idle_reset(tmp_path, monkeypatch):
    runtime = GroupRuntime(group=Group(id='main'), data_dir=tmp_path / 'data', model='test/dummy',
                           stream_fn=Mock(side_effect=AssertionError('No model call expected')))
    manager = runtime.goal_manager
    try:
        assert manager.runtime is runtime
        goal = manager.create('main', 'Durable Goal', 'Keep the original conditions', ['Inspect actual evidence'], [])
        assert manager.has_unfinished_goals()
        before = manager.tracker.get_goal(goal.id)
        directory = runtime.workspace_dir / '.xdog' / 'goals'
        assert directory.is_dir()
        files = {path.relative_to(directory): path.read_bytes() for path in directory.rglob('*') if path.is_file()}
        assert files
        session = runtime.get_or_create_session(allow_reset=False)
        monkeypatch.setattr(runtime.transcript_store, 'needs_daily_reset', lambda *args: True)
        monkeypatch.setattr(runtime.transcript_store, 'needs_idle_reset', lambda *args: True)
        assert runtime.get_or_create_session() is session
        runtime.navigate_session('new', '', '', -1, session.meta.session_id)
        assert runtime.goal_manager is manager and manager.runtime is runtime
        assert manager.tracker.get_goal(goal.id) == before
        assert all((directory / relative).read_bytes() == content for relative, content in files.items())
        assert runtime.get_or_create_session() is not session
        assert manager.has_unfinished_goals()
    finally:
        if runtime._active_session is not None:
            await runtime._active_session.children.supervisor.aclose()
            await runtime._active_session.extensions.aclose()
            runtime._active_session.dispose()
        for extension in runtime._retired_extensions:
            await extension.aclose()
        runtime.mcp.close()


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
