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


async def test_missing_current_pointer_never_guesses_or_executes_an_archived_conversation(tmp_path):
    from xdog.ai.types import TextContent
    from xdog.claw.core.runtime.orchestrator import Orchestrator
    from xdog.claw.core.types import Group, UserInput

    calls = []

    def model(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage(content=(TextContent(text="original saved conversation"),)))

    def application(stream):
        app = Orchestrator(ClawConfig(data_dir=str(tmp_path / "data")), model="test/pointer", stream_fn=stream)
        app.register_group(Group(id="main", workspace=str(tmp_path / "workspace"), enabled_tools=()))
        return app

    app = application(model)
    try:
        await app.start()
        await app.route_message(UserInput(group_id="main", content="original durable input"))
        store = app._runtimes["main"].transcript_store
        meta = store.get_active_session("main")
        history_path = store._path(meta.session_id)
        original = history_path.read_bytes()
    finally:
        await app.stop()
    (store._sessions_dir / "sessions.json").unlink()  # Damage only this private fixture's authoritative pointer.

    def no_model(*args):
        raise AssertionError("An archive/missing pointer must not select or initialize an executable context")

    restored = application(no_model)
    try:
        await restored.start()
        current = restored._runtimes["main"]
        assert current._active_session is None and current.get_new_session_id() is None
        result = await restored.route_message(UserInput(group_id="main", content="must await pointer recovery"))
        assert result is not None and result.error and "pointer" in result.error
        assert current._active_session is None and not (store._sessions_dir / "sessions.json").exists()
        assert history_path.read_bytes() == original and len(calls) == 1
        import pytest
        with pytest.raises(ValueError, match="pointer"):
            await restored.navigate_session("main", "new", "", "", -1, "")
        with pytest.raises(ValueError, match="pointer"):
            current.reset_session()
        assert not (store._sessions_dir / "sessions.json").exists()
        assert current.transcript_store.load_transcript(meta.session_id), "Archives remain readonly inspectable data"
    finally:
        await restored.stop()


async def test_native_endpoint_never_replaces_an_existing_non_socket_file(tmp_path):
    import pytest

    path = tmp_path / "existing.sock"
    original = b"fixture-owned non-socket bytes; never unlink this as a stale endpoint\n"
    path.write_bytes(original)
    config = ClawConfig(data_dir=str(tmp_path / "data"), model="test/socket", socket_path=str(path),
                        pid_file=str(tmp_path / "io.pid"), groups=(GroupDef(id="main", is_main=True),))
    server = GatewayServer(config)
    with patch("xdog.claw.core.runtime.gateway._build_model_and_options", return_value=(
        "test/socket", lambda *args: EventStream.empty(AssistantMessage())
    )):
        try:
            with pytest.raises((ValueError, RuntimeError), match="socket"):
                await server.start()
            assert path.read_bytes() == original
        finally:
            await server.stop()


async def test_cancelled_socket_acquisition_closes_late_inode_before_publication(tmp_path, monkeypatch):
    import pytest
    from xdog.claw.core.runtime import unix_endpoint

    entered, release = threading.Event(), threading.Event()
    acquired = []
    original = unix_endpoint.bind

    def actual_bind(path):
        result = original(path)
        acquired.append(result)
        entered.set()
        assert release.wait(5)
        return result

    config = ClawConfig(data_dir=str(tmp_path / "data"), model="test/socket", socket_path=str(tmp_path / "late.sock"),
                        pid_file=str(tmp_path / "late.pid"), groups=(GroupDef(id="main", is_main=True),))
    server = GatewayServer(config)
    monkeypatch.setattr(unix_endpoint, "bind", actual_bind)
    with patch("xdog.claw.core.runtime.gateway._build_model_and_options", return_value=(
        "test/socket", lambda *args: EventStream.empty(AssistantMessage())
    )):
        starting = asyncio.create_task(server.start())
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            assert Path(config.socket_path).stat().st_mode & 0o777 == 0o600
            assert server._server is None and server._bound_endpoint is None
            for _ in range(3):
                starting.cancel()
                await asyncio.sleep(0)
            assert not starting.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await starting
            assert acquired[0].socket.fileno() == -1
            assert not Path(config.socket_path).exists() and not Path(config.pid_file).exists()
        finally:
            release.set()
            await asyncio.gather(starting, return_exceptions=True)
            await server.stop()


def test_endpoint_cleanup_never_unlinks_a_replacement_file(tmp_path):
    from xdog.claw.core.runtime.unix_endpoint import bind, close

    path = tmp_path / "owned.sock"
    endpoint = bind(path)
    path.unlink()
    replacement = b"fixture replacement data must survive closing the original socket"
    path.write_bytes(replacement)
    close(endpoint)
    assert endpoint.socket.fileno() == -1 and path.read_bytes() == replacement
