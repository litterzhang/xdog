"""Actual application entrypoints revalidate after queueing, not just on receipt."""
from __future__ import annotations

import asyncio

import pytest
from xdog.claw.config import ClawConfig
from xdog.claw.core.inputs.models import InputScope
from xdog.claw.core.runtime.orchestrator import Orchestrator

from packages.claw.tests.test_active_tool_persistence import create


@pytest.mark.parametrize('operation',
                         ['access', 'model', 'reasoning', 'child', 'extension', 'argument', 'compact', 'skill'])
async def test_queued_controls_cannot_bootstrap_or_mutate_a_successor(tmp_path, monkeypatch, operation):
    runtime, calls = create(tmp_path)
    original = runtime.get_or_create_session(allow_reset=False)
    expected = original.meta.session_id
    app = Orchestrator(ClawConfig(data_dir=str(tmp_path / 'app')))
    app._runtimes['main'] = runtime
    controller = app.group_controller('main')
    prepare = runtime.prepare_session
    prepared = []

    async def track(*args, **kwargs):
        prepared.append(True)
        return await prepare(*args, **kwargs)

    monkeypatch.setattr(runtime, 'prepare_session', track)
    operations = {
        'access': lambda: app.manage_access('main', 'restrict', expected),
        'model': lambda: app.extension_models('main', expected, 'other/model'),
        'reasoning': lambda: app.reasoning('main', expected, 'off'),
        'child': lambda: app.manage_children('main', 'start', '', 'task', 'read-only', expected),
        'extension': lambda: app.manage_extensions('main', 'list', '', '', expected),
        'argument': lambda: app.complete_extension_argument('main', expected, 'command', '', 'request'),
        'compact': lambda: app.compact_session('main', expected, '', lambda: True),
        'skill': lambda: app.skills_control('main', expected, 'list', ''),
    }
    work = None
    try:
        async with controller.acquire(is_user=True):
            work = asyncio.create_task(operations[operation]())
            await asyncio.sleep(0)
            assert not work.done()
            runtime.reset_session()
        with pytest.raises(ValueError, match='changed'):
            await asyncio.wait_for(work, 3)
        assert not prepared and not calls
        assert runtime._active_session is None
        assert runtime.get_new_session_id() != expected
    finally:
        if work is not None:
            await asyncio.gather(work, return_exceptions=True)
        await app.stop()


async def test_context_projection_tracks_queued_scopes_and_shutdown_fences(tmp_path):
    runtime, _ = create(tmp_path)
    app = Orchestrator(ClawConfig(data_dir=str(tmp_path / 'app')))
    app._runtimes['main'] = runtime
    controller = app.group_controller('main')
    scope = InputScope('main', runtime.workspace_dir)
    first_entered, first_release, second_entered, second_release = (asyncio.Event() for _ in range(4))

    async def mutation(entered, release):
        async with controller.operation(context_change=True):
            entered.set()
            await release.wait()

    first = asyncio.create_task(mutation(first_entered, first_release))
    second = None
    try:
        await asyncio.wait_for(first_entered.wait(), 2)
        second = asyncio.create_task(mutation(second_entered, second_release))
        await asyncio.sleep(0)
        first_release.set()
        await first
        await asyncio.wait_for(second_entered.wait(), 2)
        assert app.context_changing('main') and app.is_group_running('main')
        assert app._execution_snapshot(scope)['state'] == 'running'
        assert app.source_lease('main') == ('', -1)
        second_release.set()
        await second
        assert not app.context_changing('main')
        assert app._execution_snapshot(scope)['state'] == 'idle'
        app.request_stop()
        assert app.try_reserve_group('main') is None
        assert app._execution_snapshot(scope)['state'] == 'closing'
    finally:
        first_release.set()
        second_release.set()
        await asyncio.gather(*(job for job in (first, second) if job is not None), return_exceptions=True)
        await app.stop()


async def test_cold_argument_completion_cannot_activate_a_context(tmp_path):
    runtime, calls = create(tmp_path)
    app = Orchestrator(ClawConfig(data_dir=str(tmp_path / 'app')))
    app._runtimes['main'] = runtime
    try:
        with pytest.raises(ValueError, match='no activation'):
            await app.complete_extension_argument('main', '', 'command', '', 'request')
        assert runtime._active_session is None and not calls
        assert runtime.get_new_session_id() is None
    finally:
        await app.stop()


def test_retired_unfenced_application_paths_are_not_exposed():
    # No callers remain: use original input/attempt cancellation, async context
    # control and durable output delivery, never these old bypasses.
    for name in ('_send_response', 'reset_group', 'abort_group', 'wait_group_idle'):
        assert not hasattr(Orchestrator, name)
