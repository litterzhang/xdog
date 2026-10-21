"""Stable group records, original-parent fences and actual shutdown; no old role replay."""
from __future__ import annotations

import asyncio
import json
import os
import threading
from pathlib import Path

import pytest
from xdog.claw.config import ClawConfig
from xdog.claw.core.runtime.orchestrator import Orchestrator
from xdog.claw.core.types import Group
from xdog.skills.goal.host import OperationContext
from xdog.skills.goal.store import GoalStore

from packages.claw.tests.test_goal_host import Model, activate, application, planned


async def test_idle_rotation_preserves_same_group_goal_and_task_files_without_work_replay(tmp_path):
    async with application(tmp_path) as (app, runtime, model):
        await activate(app)
        host = runtime.capabilities.get('goal')
        goal = await planned(host)
        old, owner = runtime._active_session, host.owner
        runtime.reset_session()
        await activate(app)
        assert runtime._active_session is not old and runtime.capabilities.get('goal') is host and host.owner == owner
        assert (await host.service.current()).id == goal.id
        assert (await host.service.status())['tasks'][0]['worker'] == 'main'
        assert not runtime._active_session.children.runtime._children
        assert all(not {'task_report', 'goal_context'}.intersection(t.name for t in c.tools) for _, c in model.calls)


async def test_native_goal_pause_status_clear_do_not_cancel_real_optional_child_or_allow_rotation(tmp_path):
    async with application(tmp_path) as (app, runtime, model):
        await activate(app)
        host, session = runtime.capabilities.get('goal'), runtime._active_session
        await planned(host)
        model.gate = threading.Event()
        child = await session.children.runtime.start('ordinary child; no Goal role')
        try:
            assert session.children.runtime.active
            result = await app.manage_extensions('main', 'command', 'goal', 'pause User pause', session.meta.session_id)
            assert 'Status: paused' in result['output']
            assert (await host.service.current()).status == 'paused'
            status = await app.manage_extensions('main', 'command', 'goal', 'status', session.meta.session_id)
            assert 'Status: paused' in status['output']
            raw = await app.manage_extensions('main', 'command', 'goal', 'status --json', session.meta.session_id)
            assert json.loads(raw['output'])['current']['status'] == 'paused'
            await app.manage_extensions('main', 'command', 'goal', 'clear User archived', session.meta.session_id)
            assert await host.service.current() is None and session.children.runtime.active
            with pytest.raises(RuntimeError, match='Child work'):
                runtime.reset_session()
        finally:
            model.gate.set()
            await session.children.runtime.control('wait', child)


async def test_another_group_never_reads_this_registry_or_binds_its_parent(tmp_path):
    async with application(tmp_path) as (app, runtime, _):
        goal = await planned(runtime.capabilities.get('goal'))
        app.register_group(Group(id='other', workspace=str(tmp_path / 'other')))
        await activate(app)
        await activate(app, 'other')
        host, other = runtime.capabilities.get('goal'), app._runtimes['other'].capabilities.get('goal')
        assert host.owner != other.owner and await other.service.current() is None
        with pytest.raises(ValueError, match='Unknown Goal'):
            await other.service.status(goal.id)
        with pytest.raises(ValueError, match='owner'):
            host.attach(await app._runtimes['other']._capability_host.prepare_parent())


async def test_legacy_stores_are_never_migrated_or_executed_and_new_registry_is_explicit(tmp_path):
    model = Model()
    app = Orchestrator(ClawConfig(data_dir=str(tmp_path / 'data')), stream_fn=model, data_dir=tmp_path / 'data')
    app.register_group(Group(id='main', workspace=str(tmp_path / 'workspace')))
    runtime = app._runtimes['main']
    group_dir = runtime.data_dir / 'groups/main'
    old_json, old_db = group_dir / 'goals.json', group_dir / 'goals/journal.sqlite3'
    old_db.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    original = b'old independently reviewed workers; do not read or upgrade\n'
    old_json.write_bytes(original)
    old_db.write_bytes(original)
    try:
        await app.start()
        host = runtime.capabilities.get('goal')
        assert await host.service.current() is None and not model.calls
        assert old_json.read_bytes() == old_db.read_bytes() == original
        assert {str(old_json), str(old_db)} <= set(host._legacy_paths)
        assert host.service.store.path.name == 'registry.sqlite3'
    finally:
        await app.stop()


async def test_process_reopen_restores_main_tasks_without_inventing_an_agent_or_completion(tmp_path):
    async with application(tmp_path) as (app, runtime, _):
        host = runtime.capabilities.get('goal')
        goal = await planned(host)
        owner, path = host.owner, host.service.store.path
    store = await GoalStore.open(path, owner)
    await store.close()
    async with application(tmp_path) as (app, runtime, model):
        host = runtime.capabilities.get('goal')
        assert host.owner == owner and (await host.service.current()).id == goal.id
        saved = await host.service.status()
        assert saved['tasks'][0]['status'] == 'pending' and saved['tasks'][0]['worker'] == 'main'
        assert runtime._active_session is None and not model.calls


@pytest.mark.parametrize('daily,idle', [(True, False), (False, True), (True, True)])
async def test_unfinished_goal_never_vetoes_idle_context_rotation(tmp_path, monkeypatch, daily, idle):
    async with application(tmp_path) as (app, runtime, _):
        await activate(app)
        host = runtime.capabilities.get('goal')
        goal = await planned(host)
        old = runtime._active_session
        monkeypatch.setattr(runtime.transcript_store, 'needs_daily_reset', lambda *args: daily)
        monkeypatch.setattr(runtime.transcript_store, 'needs_idle_reset', lambda *args: idle)
        await activate(app)
        assert runtime._active_session is not old and runtime.capabilities.get('goal') is host
        assert (await host.service.current()).id == goal.id


async def test_relative_workspace_binds_canonical_goal_owner_and_filesystem(tmp_path):
    workspace = tmp_path / 'relative-workspace'
    app = Orchestrator(ClawConfig(data_dir=str(tmp_path / 'data')), stream_fn=Model(), data_dir=tmp_path / 'data')
    app.register_group(Group(id='main', workspace=os.path.relpath(workspace, Path.cwd())))
    try:
        await app.start()
        await activate(app)
        runtime = app._runtimes['main']
        assert runtime.capabilities.get('goal').owner.workspace == workspace
        assert runtime._active_session.agent.tool_context['fs_workspace'] == str(workspace)
    finally:
        await app.stop()


@pytest.mark.parametrize('group_ids', [('main',), ('main', 'later')])
async def test_shutdown_joins_admitted_capability_open_before_releasing_registry_lease(
        tmp_path, monkeypatch, group_ids):
    original = GoalStore.open
    entered, release = asyncio.Event(), asyncio.Event()
    opened = []
    async def delayed(path, owner):
        store = await original(path, owner)
        opened.append(store)
        entered.set()
        await release.wait()
        return store
    monkeypatch.setattr(GoalStore, 'open', delayed)
    model = Model()
    app = Orchestrator(ClawConfig(data_dir=str(tmp_path / 'data')), stream_fn=model, data_dir=tmp_path / 'data')
    for group in group_ids:
        app.register_group(Group(id=group, workspace=str(tmp_path / group)))
    opening = asyncio.create_task(app.start())
    closing = None
    try:
        await asyncio.wait_for(entered.wait(), 3)
        closing = asyncio.create_task(app.stop())
        await asyncio.sleep(.01)
        assert not closing.done()
    finally:
        release.set()
        await asyncio.gather(opening, return_exceptions=True)
        if closing is not None:
            await closing
        else:
            await app.stop()
    assert len(opened) == 1 and not model.calls
    assert not (tmp_path / 'data/groups/later/goals').exists()
    reopened = await original(opened[0].path, opened[0].owner)
    await reopened.close()


async def test_retired_parent_tool_cannot_borrow_new_real_user_authority_even_for_reads(tmp_path):
    async with application(tmp_path) as (app, runtime, model):
        await activate(app)
        binding = runtime.capabilities.get('goal')
        existing = await binding.service.create('Original', 'Original record', context=OperationContext(True))
        await binding.service.change('complete', summary='Main finished original request')
        old_tool = next(t for t in runtime._active_session.agent.state.tools if t.name == 'goal')
        runtime.reset_session()
        fresh = await runtime.prepare_session(allow_reset=False)
        new_tool = next(t for t in fresh.agent.state.tools if t.name == 'goal')
        assert new_tool is not old_tool and all(t is not old_tool for t in fresh.agent.state.tools)
        results = []
        model.parent_action = {'action': 'create', 'title': 'New authorized context',
                               'outcome': 'Actual human delegation'}
        def stream(selected, context, options):
            source = model(selected, context, options)
            async def events():
                if context.messages[-1].role == 'user':
                    assert fresh._user_operation_authorized
                    results.append(await old_tool.execute('read', {'action': 'status', 'goal_id': existing.id}))
                    results.append(await old_tool.execute('write', {'action': 'create', 'title': 'Borrowed authority'}))
                async for event in source:
                    yield event
            from xdog.ai.utils.event_stream import EventStream
            return EventStream.from_async_generator(events(), result_future=source._result_future)
        fresh.agent.set_stream_fn(stream)
        await activate(app)
        assert results and all(r.is_error for r in results)
        assert {g.title for g in await binding.service.list()} == {'Original', 'New authorized context'}
        assert next(t for t in fresh.agent.state.tools if t.name == 'goal') is new_tool
