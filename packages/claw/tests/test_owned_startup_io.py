"""Actual gateway bootstrap must not perform storage IO or create Agents on the event loop."""
from __future__ import annotations

import asyncio
import builtins
import io
import os
import threading
from pathlib import Path
from unittest.mock import patch

from xdog.ai.types import AssistantMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer


async def test_gateway_bootstrap_and_pid_io_run_off_loop_before_receiver_readiness(tmp_path, monkeypatch):
    owner = threading.get_ident()
    calls = []
    original_open, original_io_open, original_fsync = builtins.open, io.open, os.fsync

    def opened(actual, file, *args, **kwargs):
        if isinstance(file, (str, os.PathLike)) and Path(file).is_relative_to(tmp_path):
            calls.append((str(file), threading.get_ident()))
        return actual(file, *args, **kwargs)

    def synced(descriptor):
        original_fsync(descriptor)
        path = Path(os.readlink(f"/proc/self/fd/{descriptor}"))
        if path.is_relative_to(tmp_path):
            calls.append((str(path), threading.get_ident()))

    config = ClawConfig(data_dir=str(tmp_path / "data"), model="test/startup",
                        socket_path=str(tmp_path / "gateway.sock"),
                        pid_file=str(tmp_path / "gateway.pid"), groups=(GroupDef(id="main", is_main=True),))
    server = GatewayServer(config)
    monkeypatch.setattr(builtins, "open", lambda file, *a, **kw: opened(original_open, file, *a, **kw))
    monkeypatch.setattr(io, "open", lambda file, *a, **kw: opened(original_io_open, file, *a, **kw))
    monkeypatch.setattr(os, "fsync", synced)
    with patch("xdog.claw.core.runtime.gateway._build_model_and_options", return_value=(
        "test/startup", lambda *args: EventStream.empty(AssistantMessage())
    )):
        try:
            await server.start()
            assert calls and any(path.endswith("gateway.pid") for path, _thread in calls)
            assert all(thread != owner for _path, thread in calls), ("Bootstrap storage IO blocked owner loop", calls)
            assert server._orchestrator._runtimes["main"]._active_session is None
            reader, writer = await asyncio.open_unix_connection(config.socket_path)
            writer.close()
            await writer.wait_closed()
        finally:
            await server.stop()


async def test_cancelled_bootstrap_settles_late_resources_without_publishing_receiver(tmp_path, monkeypatch):
    import pytest
    from xdog.claw.core.runtime import bootstrap

    entered, release, exited = (threading.Event() for _ in range(3))
    built = []
    original = bootstrap.build

    def actual_build(config):
        result = original(config)
        built.append(result.application)
        entered.set()
        try:
            assert release.wait(5)
            return result
        finally:
            exited.set()

    config = ClawConfig(data_dir=str(tmp_path / "data"), model="test/startup", socket_path=str(tmp_path / "late.sock"),
                        pid_file=str(tmp_path / "late.pid"), groups=(GroupDef(id="main", is_main=True),))
    server = GatewayServer(config)
    monkeypatch.setattr(bootstrap, "build", actual_build)
    with patch("xdog.claw.core.runtime.gateway._build_model_and_options", return_value=(
        "test/startup", lambda *args: EventStream.empty(AssistantMessage())
    )):
        starting = asyncio.create_task(server.start())
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            for _ in range(3):
                starting.cancel()
                await asyncio.sleep(0)
            assert not starting.done() and not exited.is_set()
            assert server._orchestrator is None and server._server is None
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await starting
            assert exited.is_set() and built[0]._runtimes["main"].mcp._closed
            assert built[0]._runtimes["main"]._active_session is None
            assert not Path(config.socket_path).exists() and not Path(config.pid_file).exists()
        finally:
            release.set()
            await asyncio.gather(starting, return_exceptions=True)
            await server.stop()
