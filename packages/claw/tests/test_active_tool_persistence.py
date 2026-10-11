"""Claw restart/navigation selections preserve availability, not permission grants."""
from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import pytest
from xdog.agent import AgentTool
from xdog.ai.types import AssistantMessage, TextContent, UserMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.core.persistence.transcript_store import TranscriptStore
from xdog.claw.core.runtime.group import GroupRuntime
from xdog.claw.core.types import Group


def create(tmp_path, *, extensions=()):
    calls = []
    def stream(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())
    directory = tmp_path / 'extensions'
    for name in extensions:
        if not (directory / name).exists():
            shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions' / name, directory / name)
    workspace = tmp_path / 'workspace'
    workspace.mkdir(exist_ok=True)
    runtime = GroupRuntime(group=Group(id='main', enabled_tools=('current_time',)), data_dir=tmp_path / 'data',
                           workspace_dir=workspace,
                           model='offline/model', stream_fn=stream, extensions=extensions, extension_dir=str(directory))
    return runtime, calls


async def close(runtime):
    if runtime._active_session is not None:
        await runtime._active_session.children.supervisor.aclose()
        await runtime._active_session.extensions.aclose()
        runtime._active_session.dispose()
    for extension in runtime._retired_extensions:
        await extension.aclose()
    runtime.mcp.close()


@pytest.mark.parametrize('selection', [None, [], ['current_time']])
async def test_restart_new_switch_and_checkpoints_keep_separate_selection(tmp_path, selection):
    runtime, calls = create(tmp_path)
    reopened = None
    try:
        session = runtime.get_or_create_session(allow_reset=False)
        await session.initialize_extensions()
        await session.select_active_tools(selection, expected_revision=session.agent.tool_selection_revision)
        session.agent.replace_messages([UserMessage(content='saved'), AssistantMessage()])
        session._persist()
        identifier = session.meta.session_id
        expected = None if selection is None else tuple(selection)
        assert runtime.transcript_store.get_active_tools(identifier) == expected
        await close(runtime)
        reopened, later_calls = create(tmp_path)
        restored = reopened.get_or_create_session(allow_reset=False)
        assert restored.agent.active_tool_selection == (None if expected is None else ())
        await restored.initialize_extensions()
        assert restored.agent.active_tool_selection == expected
        await asyncio.to_thread(reopened.navigate_session, 'fork', identifier, '', -1, identifier)
        branch = reopened.transcript_store.list_branches(identifier)[0]
        await restored.select_active_tools([], expected_revision=restored.agent.tool_selection_revision)
        await asyncio.to_thread(reopened.navigate_session, 'goto', identifier, branch, 1, identifier)
        assert reopened._active_session.agent.active_tool_selection == expected
        returns = [row for row in reopened.transcript_store.branch_metadata(identifier)
                   if row['label'].startswith('Return checkpoint')]
        assert reopened.transcript_store.get_active_tools(identifier, returns[-1]['branch_id']) == ()
        reopened.reset_session()
        fresh = reopened.get_or_create_session(allow_reset=False)
        assert fresh.meta.session_id != identifier and fresh.agent.active_tool_selection is None
        await asyncio.to_thread(reopened.navigate_session, 'resume', identifier, '', -1, fresh.meta.session_id)
        assert reopened._active_session.agent.active_tool_selection == expected
        assert not calls and not later_calls
    finally:
        if reopened is not None:
            await close(reopened)
        await close(runtime)


async def test_real_extension_restore_waits_for_inventory_and_sdk_branch_failure_is_atomic(tmp_path, monkeypatch):
    runtime, calls = create(tmp_path, extensions=('dialogue',))
    reopened = None
    try:
        session = runtime.get_or_create_session(allow_reset=False)
        await session.initialize_extensions()
        selected = [next(tool.name for tool in session.agent.state.tools if tool.name.startswith('ext_dialogue_ask_'))]
        await session.select_active_tools(selected, expected_revision=session.agent.tool_selection_revision)
        session.agent.replace_messages([UserMessage(content='saved'), AssistantMessage()])
        branch = session.create_branch()
        await close(runtime)
        reopened, later_calls = create(tmp_path, extensions=('dialogue',))
        session = reopened.get_or_create_session(allow_reset=False)
        assert session.agent.active_tool_selection == ()
        await session.initialize_extensions()
        assert session.agent.active_tool_selection == tuple(selected)
        await session.select_active_tools([], expected_revision=session.agent.tool_selection_revision)
        old = session.agent.state
        store = reopened.transcript_store
        original = store._atomic
        def fail(path, *args, **kwargs):
            if path.name == 'sessions.json':
                raise OSError('Injected navigation failure')
            return original(path, *args, **kwargs)
        with monkeypatch.context() as patch:
            patch.setattr(store, '_atomic', fail)
            with pytest.raises(OSError):
                await asyncio.to_thread(session.restore_branch, branch)
        assert session.agent.state is old and store.get_active_tools(session.meta.session_id) == ()
        assert await asyncio.to_thread(session.restore_branch, branch)
        assert session.agent.active_tool_selection == ()  # Lazy rebuilt worker has not restored its inventory yet.
        await session.initialize_extensions()
        assert session.agent.active_tool_selection == tuple(selected)
        assert not calls and not later_calls
    finally:
        if reopened is not None:
            await close(reopened)
        await close(runtime)


@pytest.mark.parametrize('value', [False, {}, ['same', 'same'], [''], [1]])
def test_invalid_saved_selection_never_becomes_default(tmp_path, value):
    store = TranscriptStore(tmp_path)
    meta = store.create_session('main')
    store._path(meta.session_id).with_suffix('.tools.json').write_text(json.dumps({'active': value}))
    with pytest.raises(ValueError):
        store.get_active_tools(meta.session_id)


async def test_unavailable_saved_name_does_not_reappear_on_late_registration(tmp_path):
    runtime, calls = create(tmp_path)
    try:
        store = runtime.transcript_store
        meta = store.create_session('main')
        store.save_active_tools(meta.session_id, ('missing', 'current_time'))
        session = runtime.get_or_create_session(allow_reset=False)
        await session.initialize_extensions()
        assert session.agent.active_tool_selection == ('current_time',)
        session.agent.set_tools((*session.agent.state.tools, AgentTool(name='missing', parameters={})))
        assert session.agent.active_tool_selection == ('current_time',) and not calls
    finally:
        await close(runtime)


async def test_compaction_checkpoint_and_return_head_keep_their_own_tool_selection(tmp_path):
    from unittest.mock import AsyncMock
    runtime, calls = create(tmp_path)
    runtime.summarizer = type('OfflineSummary', (), {'summarize': AsyncMock(return_value='offline summary')})()
    try:
        session = runtime.get_or_create_session(allow_reset=False)
        await session.initialize_extensions()
        await session.select_active_tools(['current_time'], expected_revision=session.agent.tool_selection_revision)
        history = []
        for index in range(6):
            history.extend([UserMessage(content=f'turn {index} ' + 'x' * 2000),
                            AssistantMessage(content=(TextContent(text=f'answer {index}'),))])
        session.agent.replace_messages(history)
        session._persist()
        session._restored = True
        result = await session.compact()
        assert result['changed']
        recovery = session._compaction_metadata['recovery_branch_id']
        store = runtime.transcript_store
        identifier = session.meta.session_id
        assert store.get_active_tools(identifier, recovery) == ('current_time',)
        await session.select_active_tools([], expected_revision=session.agent.tool_selection_revision)
        await asyncio.to_thread(runtime.navigate_session, 'branch', identifier, recovery, -1, identifier)
        assert runtime._active_session.agent.active_tool_selection == ('current_time',)
        return_branch = next(row['branch_id'] for row in store.branch_metadata(identifier)
                             if row['label'].startswith('Return checkpoint'))
        await asyncio.to_thread(runtime.navigate_session, 'branch', identifier, return_branch, -1, identifier)
        assert runtime._active_session.agent.active_tool_selection == ()
        assert not calls
    finally:
        await close(runtime)


@pytest.mark.parametrize('bad', ['symlink', 'directory', 'oversized', 'extra-fields', 'broken-json'])
def test_unowned_or_corrupt_saved_tools_never_restore_defaults(tmp_path, bad):
    store = TranscriptStore(tmp_path / 'sessions')
    meta = store.create_session('main')
    path = store._path(meta.session_id).with_suffix('.tools.json')
    victim = tmp_path / 'victim'
    victim.write_text('{"active": null}')
    if bad == 'symlink':
        path.symlink_to(victim)
    elif bad == 'directory':
        path.mkdir()
    elif bad == 'oversized':
        path.write_bytes(b' ' * (4 * 1024 * 1024 + 1))
    elif bad == 'extra-fields':
        path.write_text('{"active": null, "borrow": true}')
    else:
        path.write_text('{')
    with pytest.raises(ValueError):
        store.get_active_tools(meta.session_id)
    if bad == 'symlink':
        with pytest.raises(ValueError):
            store.save_active_tools(meta.session_id, ())
        assert victim.read_text() == '{"active": null}'
