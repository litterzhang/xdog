"""Shutdown must settle resources before asyncio.run closes the owning event loop."""
from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from xdog.ai.types import AssistantMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.config import ClawConfig
from xdog.claw.core.memory.manager import MemoryManager
from xdog.claw.core.runtime.gateway import GatewayServer


def make_server(tmp_path, monkeypatch):
    monkeypatch.setattr("xdog.claw.core.runtime.gateway._build_model_and_options",
                        lambda config: ("offline/test", lambda *args: EventStream.empty(AssistantMessage())))
    return GatewayServer(ClawConfig(model="offline/test", data_dir=str(tmp_path / "data"),
        socket_path=str(tmp_path / "gateway.sock"), pid_file=str(tmp_path / "gateway.pid")))


@pytest.mark.parametrize("trigger", ["signal", "direct", "cancel"])
async def test_run_forever_waits_for_one_complete_shutdown(tmp_path, monkeypatch, trigger):
    server = make_server(tmp_path, monkeypatch)
    loop = asyncio.get_running_loop()
    handlers, removed = {}, []
    monkeypatch.setattr(loop, "add_signal_handler", lambda sig, callback: handlers.__setitem__(sig, callback))
    monkeypatch.setattr(loop, "remove_signal_handler", lambda sig: removed.append(sig))
    entered, release = asyncio.Event(), asyncio.Event()
    run = asyncio.create_task(server.run_forever())
    caller = None
    try:
        async with asyncio.timeout(3):
            while signal.SIGTERM not in handlers:
                await asyncio.sleep(0.001)
        original = server._orchestrator.stop
        async def held_stop():
            entered.set()
            await release.wait()
            await original()
        stop = AsyncMock(side_effect=held_stop)
        monkeypatch.setattr(server._orchestrator, "stop", stop)
        if trigger == "signal":
            handlers[signal.SIGTERM]()
            handlers[signal.SIGINT]()  # Repeated signals must not create competing closers.
        elif trigger == "direct":
            caller = asyncio.create_task(server.stop())
        else:
            run.cancel()
        await asyncio.wait_for(entered.wait(), 1)
        await asyncio.sleep(0)
        assert not run.done(), "run_forever returned while cleanup still owned resources"
        release.set()
        await asyncio.gather(run, *([caller] if caller else []), return_exceptions=True)
        assert stop.await_count == 1
        assert not Path(server._config.pid_file).exists()
        assert not Path(server._config.socket_path).exists()
        assert set(removed) == {signal.SIGINT, signal.SIGTERM}
    finally:
        release.set()
        if not run.done():
            run.cancel()
        await asyncio.gather(run, *([caller] if caller else []), return_exceptions=True)
        await server.stop()


async def test_stop_settles_original_cleanup_despite_caller_cancellation(tmp_path, monkeypatch):
    server = make_server(tmp_path, monkeypatch)
    await server.start()
    entered, release = asyncio.Event(), asyncio.Event()
    original = server._orchestrator.stop
    async def held_stop():
        entered.set()
        await release.wait()
        await original()
    stop = AsyncMock(side_effect=held_stop)
    monkeypatch.setattr(server._orchestrator, "stop", stop)
    first = asyncio.create_task(server.stop())
    second = None
    try:
        await asyncio.wait_for(entered.wait(), 1)
        first.cancel()
        second = asyncio.create_task(server.stop())
        await asyncio.sleep(0.01)
        assert not first.done(), "cancellation must wait for the real cleanup owner"
        release.set()
        outcomes = await asyncio.gather(first, second, return_exceptions=True)
        assert isinstance(outcomes[0], asyncio.CancelledError)
        assert outcomes[1] is None
        assert stop.await_count == 1
    finally:
        release.set()
        await asyncio.gather(first, *([second] if second else []), return_exceptions=True)
        await server.stop()


async def test_shutdown_closes_live_memory_database_thread(tmp_path, monkeypatch):
    server = make_server(tmp_path, monkeypatch)
    await server.start()
    memory = server._orchestrator._runtimes["main"].memory
    await memory._searcher._indexer.get_all_chunks()  # Real SQLite worker, no embedding/model request.
    connection = memory._searcher._indexer._conn
    worker = connection._thread
    assert worker.is_alive()
    try:
        await server.stop()
        assert connection._connection is None
        await asyncio.to_thread(worker.join, 1)
        assert not worker.is_alive()
    finally:
        await memory._searcher.close()
        await server.stop()


async def test_keyword_only_memory_close_is_safe_and_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr("xdog.claw.core.memory.manager._HAS_HYBRID_SEARCH", False)
    memory = MemoryManager(tmp_path / "workspace", tmp_path / "data", "main")
    await memory.close()
    await memory.close()


@pytest.mark.parametrize("mode", ["plain", "memory"])
def test_sigterm_process_finishes_cleanup_and_exits(tmp_path, mode):
    fixture = Path(__file__).parent / "fixtures/gateway_shutdown_peer.py"
    with (tmp_path / "peer.log").open("w") as log:
        process = subprocess.Popen([sys.executable, str(fixture), str(tmp_path), mode],
                                   stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            deadline = time.monotonic() + 5
            while not (tmp_path / "ready").exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.01)
            assert (tmp_path / "ready").exists(), (tmp_path / "peer.log").read_text()
            process.send_signal(signal.SIGTERM)
            assert process.wait(timeout=5) == 0
            assert (tmp_path / "cleanup-complete").exists()
            assert not (tmp_path / "gateway.pid").exists()
            assert not (tmp_path / "gateway.sock").exists()
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=3)


async def test_completed_stop_allows_fresh_start_and_second_cleanup(tmp_path, monkeypatch):
    server = make_server(tmp_path, monkeypatch)
    for _ in range(2):
        await server.start()
        assert not server._shutdown_event.is_set()
        assert Path(server._config.pid_file).exists()
        await server.stop()
        assert not Path(server._config.pid_file).exists()


async def test_channel_error_does_not_skip_other_channel_or_memory_cleanup(tmp_path, monkeypatch):
    from types import SimpleNamespace
    server = make_server(tmp_path, monkeypatch)
    await server.start()
    memory = server._orchestrator._runtimes["main"].memory
    original = memory.close
    first = SimpleNamespace(disconnect=AsyncMock(side_effect=ValueError("channel failed")))
    second = SimpleNamespace(disconnect=AsyncMock())
    server._orchestrator._channels.extend([first, second])
    close = AsyncMock(side_effect=original)
    monkeypatch.setattr(memory, "close", close)
    with pytest.raises(ValueError, match="channel failed"):
        await server.stop()
    first.disconnect.assert_awaited_once()
    second.disconnect.assert_awaited_once()
    close.assert_awaited_once()
    # Failed shutdown remains an explicit failure, not a successful fresh start.
    with pytest.raises(ValueError, match="channel failed"):
        await server.start()


async def test_channel_workers_settle_before_resources_and_mcp_failure_still_closes_memory(tmp_path, monkeypatch):
    from types import SimpleNamespace
    server = make_server(tmp_path, monkeypatch)
    await server.start()
    runtime = server._orchestrator._runtimes["main"]
    order = []
    async def disconnect():
        assert not runtime.mcp._closed
        order.append("channel")
    original_mcp = runtime.mcp.aclose
    async def close_mcp():
        order.append("mcp")
        await original_mcp()
        raise ValueError("mcp cleanup failed")
    original_memory = runtime.memory.close
    async def close_memory():
        order.append("memory")
        await original_memory()
    server._orchestrator._channels.append(SimpleNamespace(disconnect=disconnect))
    monkeypatch.setattr(runtime.mcp, "aclose", close_mcp)
    monkeypatch.setattr(runtime.memory, "close", close_memory)
    with pytest.raises(ValueError, match="mcp cleanup failed"):
        await server.stop()
    assert order == ["channel", "mcp", "memory"]
