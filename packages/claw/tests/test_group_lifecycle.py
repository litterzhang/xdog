"""Group owns actual retired/current resources, independent of application UI."""
from __future__ import annotations

import asyncio
import threading

import pytest

from packages.claw.tests.test_active_tool_persistence import create


async def test_group_close_joins_current_and_retired_workers_and_fences_reuse(tmp_path):
    runtime, calls = create(tmp_path, extensions=("contextual",))
    first = runtime.get_or_create_session(allow_reset=False)
    await first.initialize_extensions()
    old = first.extensions
    old_thread = old._manager._thread
    runtime.reset_session()
    second = await runtime.prepare_session(allow_reset=False)
    await second.initialize_extensions()
    live_thread = second.extensions._manager._thread
    try:
        await runtime.aclose()
        await runtime.aclose()
        assert not old_thread.is_alive() and not live_thread.is_alive()
        assert old._closed and second.extensions._closed
        assert second.agent.closed
        assert not calls
        with pytest.raises(RuntimeError, match="clos"):
            await runtime.prepare_session(allow_reset=False)
        with pytest.raises(RuntimeError, match="clos"):
            runtime.get_or_create_session(allow_reset=False)
        with pytest.raises(RuntimeError, match="clos"):
            runtime.reset_session()
    finally:
        await old.aclose()
        await second.extensions.aclose()
        await first.children.runtime.aclose()
        await second.children.runtime.aclose()
        first.dispose()
        second.dispose()
        await runtime.mcp.aclose()


@pytest.mark.parametrize("retired", [False, True])
async def test_group_close_repeated_cancel_settles_actual_extension_commit(tmp_path, monkeypatch, retired):
    runtime, calls = create(tmp_path, extensions=("contextual",))
    session = runtime.get_or_create_session(allow_reset=False)
    await session.initialize_extensions()
    extension = session.extensions
    entered, release, joining = (threading.Event() for _ in range(3))
    actual = runtime.transcript_store._atomic
    thread = extension._manager._thread
    actual_join = thread.join

    def persist(path, value, **kwargs):
        actual(path, value, **kwargs)
        if path.name.endswith(".extensions.json"):
            entered.set()
            assert release.wait(10)

    def join(*args, **kwargs):
        joining.set()
        return actual_join(*args, **kwargs)

    monkeypatch.setattr(runtime.transcript_store, "_atomic", persist)
    monkeypatch.setattr(thread, "join", join)
    command = closing = None
    try:
        command = asyncio.create_task(extension.command("ext-contextual-remember", "group close boundary"))
        assert await asyncio.to_thread(entered.wait, 3)
        if retired:
            runtime._drop_session()
        closing = asyncio.create_task(runtime.aclose())
        assert await asyncio.to_thread(joining.wait, 3)
        for _ in range(3):
            closing.cancel()
            await asyncio.sleep(0)
        assert not closing.done() and thread.is_alive()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await closing
        await runtime.aclose()
        assert not thread.is_alive() and not calls
        assert runtime.transcript_store.get_extension_entries(session.meta.session_id)["contextual"][0]["data"] == {
            "text": "group close boundary"
        }
    finally:
        release.set()
        await asyncio.gather(*(task for task in (command, closing) if task is not None), return_exceptions=True)
        await extension.aclose()
        await session.children.runtime.aclose()
        session.dispose()
        await runtime.mcp.aclose()


async def test_group_close_keeps_cleanup_error_and_still_closes_later_resources(tmp_path, monkeypatch):
    from xdog.claw.core.memory.manager import MemoryManager

    runtime, _ = create(tmp_path, extensions=("contextual",))
    runtime.memory = MemoryManager(runtime.workspace_dir, tmp_path / "memory-data", "main")
    session = runtime.get_or_create_session(allow_reset=False)
    await session.initialize_extensions()
    client = session.extensions._manager._clients["contextual"]
    close = client.close
    memory_close = runtime.memory.close
    errors, settled = [], []

    async def fail():
        await close()
        errors.append(True)
        raise OSError("extension cleanup failed")

    async def memory():
        await memory_close()
        settled.append(True)

    monkeypatch.setattr(client, "close", fail)
    monkeypatch.setattr(runtime.memory, "close", memory)
    try:
        for _ in range(2):
            with pytest.raises(OSError, match="extension cleanup failed"):
                await runtime.aclose()
        assert errors == [True] and settled == [True]
        assert client.process is None and not session.extensions._manager._thread.is_alive()
    finally:
        await session.children.runtime.aclose()
        session.dispose()
        await runtime.mcp.aclose()
        await memory_close()


@pytest.mark.parametrize('stage', ['check', 'prepare'])
async def test_close_during_preparation_cannot_publish_an_agent_after_shutdown(tmp_path, monkeypatch, stage):
    from xdog.claw.core.persistence import session_preparation

    runtime, calls = create(tmp_path)
    entered, release = threading.Event(), threading.Event()
    original = getattr(session_preparation, stage)

    def held(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(session_preparation, stage, held)
    opening = asyncio.create_task(runtime.prepare_session(allow_reset=False))
    closing = None
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        closing = asyncio.create_task(runtime.aclose())
        await asyncio.sleep(0)
        assert runtime._closing and not closing.done()
        release.set()
        with pytest.raises(RuntimeError, match='clos'):
            await asyncio.wait_for(opening, 3)
        await asyncio.wait_for(closing, 3)
        assert runtime._active_session is None and not calls
    finally:
        release.set()
        await asyncio.gather(*(task for task in (opening, closing) if task is not None), return_exceptions=True)
        await runtime.aclose()
