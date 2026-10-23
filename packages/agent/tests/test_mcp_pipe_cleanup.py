"""Process exit is not pipe settlement: keep backpressured transports on their owner."""
from __future__ import annotations

import asyncio
import sys
import threading
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


async def test_registry_close_error_joins_sibling_client_before_retiring_owner(tmp_path: Path, monkeypatch):
    peer = Path(__file__).parent / 'fixtures' / 'mcp_server.py'
    registry = McpRegistry({name: {'command': sys.executable,
        'args': [str(peer), 'context-only', str(tmp_path / name)]}
        for name in ('failing', 'delayed')}, tmp_path)
    entered, release, failure_seen, cancelled = (threading.Event() for _ in range(4))
    failure = OSError('first client failed after actual process and pipe cleanup')
    owner_closes, early_returns = [], []
    closing = None
    try:
        for name in ('failing', 'delayed'):
            await registry.connect(name)
        failing, delayed = (registry._clients[name] for name in ('failing', 'delayed'))
        processes = (failing.process, delayed.process)
        assert all(process is not None and process.returncode is None for process in processes)
        assert all(set(process._transport._pipes) == {0, 1, 2} for process in processes)
        actual_failing_close, actual_delayed_close = failing.close, delayed.close
        actual_registry_close = registry._close_clients
        loop = registry._loop

        async def observe_registry_close():
            owner_closes.append(asyncio.current_task())
            await actual_registry_close()

        def observe_failure():
            # gather's child-done callback precedes ours. Its owner continuation
            # therefore runs before this queued checkpoint, without a timing sleep.
            early_returns.append(owner_closes[0].done())
            failure_seen.set()

        async def fail_after_actual_close():
            await actual_failing_close()
            assert entered.is_set()
            asyncio.current_task().add_done_callback(lambda done: loop.call_soon(observe_failure))
            raise failure

        async def delayed_close():
            entered.set()
            try:
                assert await asyncio.to_thread(release.wait, 15), 'Test did not release its sibling close'
            except asyncio.CancelledError:
                cancelled.set()
                raise
            finally:
                # RED-path salvage stays on the original owner loop. A broken
                # registry must fail our assertion, not leak a real subprocess.
                await actual_delayed_close()

        monkeypatch.setattr(registry, '_close_clients', observe_registry_close)
        monkeypatch.setattr(failing, 'close', fail_after_actual_close)
        monkeypatch.setattr(delayed, 'close', delayed_close)
        closing = asyncio.create_task(registry.aclose())
        assert await asyncio.to_thread(failure_seen.wait, 10), 'First client close did not settle'
        assert early_returns == [False], 'Registry close returned before its sibling client settled'
        assert not cancelled.is_set(), 'Owner shutdown cancelled the unfinished sibling close'
        assert not closing.done(), 'aclose published the first error before its sibling settled'
        assert registry._thread.is_alive() and not loop.is_closed()
        assert failing.process is None
        assert delayed.process is processes[1] and processes[1].returncode is None

        release.set()
        with pytest.raises(OSError) as error:
            await asyncio.wait_for(asyncio.shield(closing), 10)
        assert error.value is failure
        assert not cancelled.is_set()
        assert failing.process is None and delayed.process is None
        assert not registry._thread.is_alive() and loop.is_closed()
        for process in processes:
            assert process.returncode is not None
            assert process._transport.is_closing()
            assert all(pipe.disconnected for pipe in process._transport._pipes.values())
    finally:
        release.set()
        if closing is not None:
            await asyncio.gather(closing, return_exceptions=True)
        await asyncio.gather(registry.aclose(), return_exceptions=True)


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


async def test_repeated_cancel_during_schema_spawn_cannot_abandon_a_live_process(monkeypatch):
    owner = SchemaOwner()
    original = asyncio.create_subprocess_exec
    entered, release = asyncio.Event(), asyncio.Event()
    processes = []
    async def delayed(*args, **kwargs):
        process = await original(*args, **kwargs)
        processes.append(process)
        entered.set()
        await release.wait()
        return process
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', delayed)
    compiling = asyncio.create_task(owner.compile({'type': 'object'}, input_schema=True))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        for _ in range(3):
            compiling.cancel()
            await asyncio.sleep(0)
            assert not compiling.done(), 'Cancellation abandoned the unreturned subprocess handle'
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await compiling
        assert owner._process is None
        assert processes[0].returncode is not None
        assert processes[0]._transport.is_closing()
        assert all(pipe.disconnected for pipe in processes[0]._transport._pipes.values())
    finally:
        release.set()
        await asyncio.gather(compiling, return_exceptions=True)
        await owner.close()
        for process in processes:
            if process.returncode is None:
                process.kill()
            await salvage(process)
