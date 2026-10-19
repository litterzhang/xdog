"""Memory disk work settles under its original group operation, without another Agent."""
from __future__ import annotations

import asyncio
import builtins
import io
import os
import threading
from pathlib import Path

import pytest
from xdog.claw.core.memory.manager import MemoryManager
from xdog.claw.core.memory.simple_search import SimpleMemorySearch
from xdog.claw.core.tools.tool_memory import create_memory_tool


@pytest.mark.parametrize('manager', [False, True])
@pytest.mark.parametrize('action', ['get', 'daily', 'memory'])
async def test_actual_memory_tool_file_io_runs_off_loop(tmp_path, monkeypatch, manager, action):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    (workspace / 'MEMORY.md').write_text('Actual retained memory\n')
    resource = MemoryManager(workspace, tmp_path / 'data', 'main') if manager else None
    ctx = {'workspace_dir': str(workspace), '_memory_manager': resource}
    owner = threading.get_ident()
    opened = []
    original_open, original_io = builtins.open, io.open

    def observe(actual, file, *args, **kwargs):
        if isinstance(file, (str, os.PathLike)) and Path(file).is_relative_to(workspace):
            opened.append(threading.get_ident())
        return actual(file, *args, **kwargs)

    try:
        monkeypatch.setattr(builtins, 'open', lambda file, *a, **kw: observe(original_open, file, *a, **kw))
        monkeypatch.setattr(io, 'open', lambda file, *a, **kw: observe(original_io, file, *a, **kw))
        args = ({'action': 'get', 'filename': 'MEMORY.md'} if action == 'get' else
                {'action': 'write', 'target': action, 'text': 'Actual appended evidence'})
        result = await create_memory_tool().execute('actual-memory', args, None, None, ctx=ctx)
        text = result.content[0].text
        assert 'Actual retained memory' in text if action == 'get' else 'Written' in text or 'Appended' in text
        assert opened and all(thread != owner for thread in opened), 'Memory tool performed actual file IO on its owner'
    finally:
        if resource is not None:
            await resource.close()


async def test_keyword_search_cancellation_waits_for_actual_file_read(tmp_path, monkeypatch):
    (tmp_path / 'MEMORY.md').write_text('Actual durable memory evidence\n')
    reader = SimpleMemorySearch(tmp_path)
    entered, release, exited = (threading.Event() for _ in range(3))
    original = reader._search

    def actual_read(*args):
        value = original(*args)
        entered.set()
        try:
            assert release.wait(5)
            return value
        finally:
            exited.set()

    monkeypatch.setattr(reader, '_search', actual_read)
    work = asyncio.create_task(reader.search('durable'))
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        for _ in range(3):
            work.cancel()
            await asyncio.sleep(0)
        assert not work.done() and not exited.is_set(), 'Memory read outlived its cancelled caller'
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await work
        assert exited.is_set()
    finally:
        release.set()
        await asyncio.gather(work, return_exceptions=True)
        await asyncio.to_thread(exited.wait, 3)


async def test_cancelled_memory_write_settles_real_append_without_replay(tmp_path, monkeypatch):
    from xdog.claw.config import ClawConfig
    from xdog.claw.core.runtime.orchestrator import Orchestrator
    from xdog.claw.core.types import Group

    app = Orchestrator(ClawConfig(data_dir=str(tmp_path / 'data')), model='test/memory')
    app.register_group(Group(id='main', workspace=str(tmp_path / 'workspace')))
    runtime = app._runtimes['main']
    entered, release = threading.Event(), threading.Event()
    calls = []
    original = runtime.memory.long_term.append

    def actual_append(text):
        original(text)
        calls.append(text)
        entered.set()
        assert release.wait(5)

    monkeypatch.setattr(runtime.memory.long_term, 'append', actual_append)
    tool = next(tool for tool in runtime.tools if tool.name == 'memory')
    reservation = app.try_reserve_group('main')
    assert reservation is not None
    work = app.start_group_operation(reservation, tool.execute('actual-write',
        {'action': 'write', 'target': 'memory', 'text': 'Unique append evidence'}, None, None,
        ctx={'workspace_dir': str(runtime.workspace_dir), '_memory_manager': runtime.memory}))
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        for _ in range(3):
            work.cancel()
            await asyncio.sleep(0)
        assert not work.done() and app.is_group_running('main')
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await work
        assert not app.is_group_running('main') and calls == ['Unique append evidence']
        assert runtime.memory.long_term.read().count('Unique append evidence') == 1
    finally:
        release.set()
        await asyncio.gather(work, return_exceptions=True)
        await app.stop()


async def test_parallel_memory_writes_preserve_existing_capacity_check(tmp_path):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    manager = MemoryManager(workspace, tmp_path / 'data', 'main')
    manager.long_term.write('x' * 2100)
    tools = (create_memory_tool(), create_memory_tool())
    try:
        results = await asyncio.gather(*(tool.execute('parallel-write',
            {'action': 'write', 'target': 'memory', 'text': 'z' * 60}, None, None,
            ctx={'workspace_dir': str(workspace), '_memory_manager': manager}) for tool in tools))
        texts = [result.content[0].text for result in results]
        assert sum('Appended' in text for text in texts) == 1
        assert sum('capacity' in text for text in texts) == 1
        assert manager.long_term.read().count('z') == 60
    finally:
        await manager.close()
