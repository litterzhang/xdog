"""Application metadata is IO-free; actual resource close failures remain observable."""
from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest
from xdog.claw.config import ClawConfig
from xdog.claw.core.runtime.orchestrator import Orchestrator
from xdog.claw.core.types import Group


async def test_registered_workspace_projection_does_not_repeat_filesystem_resolution(tmp_path, monkeypatch):
    app = Orchestrator(ClawConfig(data_dir=str(tmp_path / 'data')), model='test/offline')
    app.register_group(Group(id='main', workspace=str(tmp_path / 'workspace')))
    expected = app._runtimes['main'].workspace_dir
    original = os.lstat
    accesses = []

    def observe(path, *args, **kwargs):
        if isinstance(path, (str, os.PathLike)) and Path(path).is_relative_to(tmp_path):
            accesses.append(str(path))
        return original(path, *args, **kwargs)

    try:
        with monkeypatch.context() as trace:
            trace.setattr(os, 'lstat', observe)
            assert app.get_group_workspace('main') == expected
        assert accesses == [], 'Cached workspace projection must not block ingress on another physical path walk'
    finally:
        await app.stop()


@pytest.mark.parametrize('kind', ['extension', 'children', 'inbox'])
async def test_application_shutdown_reports_actual_settled_resource_error(tmp_path, monkeypatch, kind):
    from packages.claw.tests.test_active_tool_persistence import create

    runtime, calls = create(tmp_path, extensions=('contextual',))
    app = Orchestrator(ClawConfig(data_dir=str(tmp_path / 'data')), model='test/offline')
    app._runtimes['main'] = runtime  # Explicit, unpublished resource composition.
    session = runtime.get_or_create_session(allow_reset=False)
    await session.initialize_extensions()
    client = session.extensions._manager._clients['contextual']
    completed = []
    resource = client if kind == 'extension' else session.children.runtime
    method = 'close' if kind == 'extension' else 'aclose'
    if kind == 'inbox':
        resource, method = (await app.input_service()).store, 'close'
    original = getattr(resource, method)

    async def actual_close_then_error():
        await original()
        completed.append(True)
        raise OSError('actual settled resource error')

    try:
        with monkeypatch.context() as fault:
            fault.setattr(resource, method, actual_close_then_error)
            with pytest.raises(OSError, match='actual settled resource error'):
                await app.stop()
        assert completed == [True] and not calls
        assert client.process is None and runtime.mcp._closed
        assert not session.extensions._manager._thread.is_alive()
    finally:
        # Original resource owner retains the result; do not replay its close.
        try:
            await app.stop()
        except OSError:
            pass
        # A failing old application close can skip unrelated real resources.
        # Settle those test-owned resources even on the expected RED path.
        try:
            await session.extensions.aclose()
        except OSError:
            pass
        await runtime.mcp.aclose()
        if app._outputs is not None:
            await app._outputs.close()


async def test_repeated_application_shutdown_cancel_keeps_actual_cleanup_owner(tmp_path, monkeypatch):
    import asyncio

    from packages.claw.tests.test_active_tool_persistence import create

    runtime, calls = create(tmp_path, extensions=('contextual',))
    app = Orchestrator(ClawConfig(data_dir=str(tmp_path / 'data')), model='test/offline')
    app._runtimes['main'] = runtime
    session = runtime.get_or_create_session(allow_reset=False)
    await session.initialize_extensions()
    client = session.extensions._manager._clients['contextual']
    original = client.close
    entered, release = threading.Event(), threading.Event()
    completed = []

    async def actual_close_then_settle():
        await original()
        entered.set()
        assert await asyncio.to_thread(release.wait, 5)
        completed.append(True)

    monkeypatch.setattr(client, 'close', actual_close_then_settle)
    work = asyncio.create_task(app.stop())
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        for _ in range(3):
            work.cancel()
            await asyncio.sleep(0)
        assert not work.done() and not completed, 'Application shutdown abandoned its actual resource cleanup'
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await work
        assert runtime.mcp._closed, 'Cancelled shutdown skipped remaining owned resources'
        await app.stop()
        assert completed == [True] and client.process is None and runtime.mcp._closed and not calls
    finally:
        release.set()
        await asyncio.gather(work, return_exceptions=True)
        try:
            await app.stop()
        except OSError:
            pass


async def test_application_shutdown_retains_multiple_actual_cleanup_errors(tmp_path, monkeypatch):
    from packages.claw.tests.test_active_tool_persistence import create

    runtime, _calls = create(tmp_path, extensions=('contextual',))
    app = Orchestrator(ClawConfig(data_dir=str(tmp_path / 'data')), model='test/offline')
    app._runtimes['main'] = runtime
    session = runtime.get_or_create_session(allow_reset=False)
    await session.initialize_extensions()
    client = session.extensions._manager._clients['contextual']
    close_client, close_children = client.close, session.children.runtime.aclose
    completed = []

    async def client_error():
        await close_client()
        completed.append('client')
        raise OSError('actual extension cleanup result')

    async def children_error():
        await close_children()
        completed.append('children')
        raise RuntimeError('actual children cleanup result')

    with monkeypatch.context() as fault:
        fault.setattr(client, 'close', client_error)
        fault.setattr(session.children.runtime, 'aclose', children_error)
        for _ in range(2):
            with pytest.raises(BaseExceptionGroup) as caught:
                await app.stop()
            assert {str(error) for error in caught.value.exceptions} == {
                'actual extension cleanup result', 'actual children cleanup result'}
    assert completed == ['children', 'client'] and client.process is None and runtime.mcp._closed
