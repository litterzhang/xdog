"""Process exit is not pipe settlement: keep backpressured transports on their owner."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from xdog.agent.mcp import McpRegistry
from xdog.agent.mcp.config import McpServerConfig
from xdog.agent.mcp.schema_owner import MAX_FRAME as SCHEMA_FRAME
from xdog.agent.mcp.schema_owner import SchemaOwner
from xdog.agent.mcp.stdio import MAX_FRAME, StdioClient


async def salvage(process):
    # RED cleanup only: never leak the intentionally backpressured test process.
    process._transport.close()
    await process.communicate()


async def test_stdio_close_settles_paused_pipes_before_registry_loop_exits(tmp_path: Path):
    registry = McpRegistry(cwd=tmp_path)
    async def exercise():
        client = StdioClient(McpServerConfig('fixture', ()), tmp_path)
        process = await asyncio.create_subprocess_exec(
            sys.executable, '-c', f'import os;os.write(1,b"bad\\n");os.write(1,b"x"*{4 * MAX_FRAME})',
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            start_new_session=True, limit=MAX_FRAME)
        client.process = process
        client._reader = asyncio.create_task(client._read())
        client._stderr = asyncio.create_task(client._drain_stderr())
        registry._clients['fixture'] = client
        try:
            async with asyncio.timeout(10):
                while not (client._reader.done() and process.stdout._paused):
                    await asyncio.sleep(.001)
            await client.close()
            closed = process._transport.is_closing()
            disconnected = all(pipe.disconnected for pipe in process._transport._pipes.values())
            return process, closed, disconnected
        finally:
            await salvage(process)
    try:
        process, closed, disconnected = await registry._bridge(exercise())
        await registry.aclose()
        assert process.returncode is not None
        assert not registry._thread.is_alive() and registry._loop.is_closed()
        assert closed, 'Owner cleanup returned with an open subprocess transport'
        assert disconnected, 'Owner cleanup returned before every pipe disconnected'
    finally:
        await registry.aclose()


async def test_schema_stop_reaps_worker_even_when_unread_output_is_paused():
    owner = SchemaOwner()
    process = await asyncio.create_subprocess_exec(
        sys.executable, '-c', f'import os;os.write(1,b"x"*{4 * SCHEMA_FRAME})',
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        limit=SCHEMA_FRAME)
    owner._process = process
    try:
        async with asyncio.timeout(10):
            while not process.stdout._paused:
                await asyncio.sleep(.001)
        timed_out = False
        try:
            async with asyncio.timeout(2):
                await owner._stop(process)
        except TimeoutError:
            timed_out = True
        assert not timed_out, 'Schema cleanup blocked on a paused pipe after killing its worker'
        assert owner._process is None and process.returncode is not None
        assert process._transport.is_closing()
        assert all(pipe.disconnected for pipe in process._transport._pipes.values())
    finally:
        await salvage(process)
        await owner.close()


async def test_cancelled_schema_stop_retains_process_until_pipe_cleanup_settles(monkeypatch):
    from xdog.agent.mcp import schema_owner

    owner = SchemaOwner()
    process = await asyncio.create_subprocess_exec(
        sys.executable, '-c', 'import time;time.sleep(60)',
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    owner._process = process
    entered, release = asyncio.Event(), asyncio.Event()
    original = schema_owner.close_process_pipes
    async def delayed(current):
        assert current is process
        entered.set()
        await release.wait()
        await original(current)
    monkeypatch.setattr(schema_owner, 'close_process_pipes', delayed)
    stopping = asyncio.create_task(owner._stop(process))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        for _ in range(3):
            stopping.cancel()
            await asyncio.sleep(0)
            assert not stopping.done()
            assert owner._process is process
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await stopping
        assert owner._process is None and process.returncode is not None
        assert process._transport.is_closing()
        assert all(pipe.disconnected for pipe in process._transport._pipes.values())
    finally:
        release.set()
        await asyncio.gather(stopping, return_exceptions=True)
        await salvage(process)
        await owner.close()
