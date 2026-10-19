"""Real private ACP binding/path IO is owned; protocol state remains on its loop."""
from __future__ import annotations

import asyncio
import os
import socket
import stat
import tempfile
import threading
from pathlib import Path

import pytest
from acp import RequestError
from xdog.agent.acp.adapter import AcpAdapter
from xdog.agent.acp.contracts import Peer
from xdog.claw.acp.server import ClawAcpServer

from packages.claw.tests.test_acp_backend import enqueue_params, make_backend


async def test_private_acp_endpoint_io_is_off_loop_and_private_before_listen(tmp_path, monkeypatch):
    app, backend, model = await make_backend(tmp_path)
    owner, calls, modes = threading.get_ident(), [], []
    server = None
    original_listen = socket.socket.listen
    with tempfile.TemporaryDirectory(prefix='acp-io-', dir='/data/t.Vy5SaQ') as directory:
        endpoint = Path(directory) / 'peer.sock'
        originals = {name: getattr(os, name) for name in ('mkdir', 'lstat', 'chmod', 'unlink')}

        def observe(name, path, *args, **kwargs):
            if isinstance(path, (str, os.PathLike)) and Path(path).is_relative_to(directory):
                calls.append((name, threading.get_ident()))
            return originals[name](path, *args, **kwargs)

        def listen(sock, *args, **kwargs):
            if sock.family == socket.AF_UNIX and sock.getsockname() == str(endpoint):
                modes.append(stat.S_IMODE(endpoint.stat().st_mode))
            return original_listen(sock, *args, **kwargs)

        try:
            with monkeypatch.context() as trace:
                for name in originals:
                    trace.setattr(os, name, lambda path, *a, _name=name, **kw: observe(_name, path, *a, **kw))
                trace.setattr(socket.socket, 'listen', listen)
                server = await ClawAcpServer.start(app, endpoint)
                await server.close()
            assert modes == [0o600], 'ACP accepted connections before applying private socket permissions'
            assert calls and all(thread != owner for _name, thread in calls), calls
            assert not endpoint.exists() and not model.inputs
        finally:
            if server is not None:
                await server.close()
            await backend.close()
            await app.stop()


@pytest.mark.parametrize('mode', ['load', 'adapter', 'prompt', 'enqueue', 'status'])
async def test_acp_canonical_workspace_checks_use_owned_io(tmp_path, monkeypatch, mode):
    app, backend, model = await make_backend(tmp_path)
    peer = Peer('actual-peer', None)
    ref = await backend.load_session('group:main', tmp_path, peer, None)
    owner, calls = threading.get_ident(), []
    original = Path.resolve

    def resolve(path, *args, **kwargs):
        if path == tmp_path:
            calls.append(threading.get_ident())
        return original(path, *args, **kwargs)

    async def emit(update):
        pass

    try:
        with monkeypatch.context() as trace:
            trace.setattr(Path, 'resolve', resolve)
            if mode == 'load':
                assert (await backend.load_session('group:main', tmp_path, peer, emit)).cwd == tmp_path
            elif mode == 'adapter':
                adapter = AcpAdapter(backend)
                adapter.peer = peer
                await adapter.load_session(str(tmp_path), 'group:main', mcp_servers=[])
            elif mode == 'prompt':
                await backend.prompt(ref, peer, 'Actual canonical ACP message', {}, emit)
            elif mode == 'enqueue':
                await backend.extension(peer, 'xdog/enqueue', enqueue_params(tmp_path))
            else:
                await backend.extension(peer, 'xdog/inbox_status', {'session_id': 'group:main', 'cwd': str(tmp_path)})
        assert calls and all(thread != owner for thread in calls), 'ACP resolved workspace paths on its protocol loop'
    finally:
        await backend.close()
        await app.stop()


@pytest.mark.parametrize('action', ['cancel', 'disconnect'])
async def test_late_workspace_resolution_cannot_publish_cancelled_or_disconnected_load(tmp_path, monkeypatch, action):
    app, backend, model = await make_backend(tmp_path)
    peer = Peer('late-load', None)
    entered, release = threading.Event(), threading.Event()
    original = Path.resolve
    owner = threading.get_ident()

    def resolve(path, *args, **kwargs):
        value = original(path, *args, **kwargs)
        if path == tmp_path:
            entered.set()
            if threading.get_ident() != owner:
                assert release.wait(5)
        return value

    monkeypatch.setattr(Path, 'resolve', resolve)
    work = asyncio.create_task(backend.load_session('group:main', tmp_path, peer, None))
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        assert not work.done() and not backend._bindings, 'Load published before its actual path resolver settled'
        if action == 'cancel':
            for _ in range(3):
                work.cancel()
                await asyncio.sleep(0)
            assert not work.done()
        else:
            await backend.disconnect(peer)
        release.set()
        with pytest.raises(asyncio.CancelledError if action == 'cancel' else RequestError):
            await work
        assert not backend._bindings and not model.inputs
    finally:
        release.set()
        await asyncio.gather(work, return_exceptions=True)
        await backend.close()
        await app.stop()


async def test_native_socket_cleanup_also_remains_owned_io(tmp_path, monkeypatch):
    from unittest.mock import patch

    from xdog.ai.types import AssistantMessage
    from xdog.ai.utils.event_stream import EventStream
    from xdog.claw.config import ClawConfig, GroupDef
    from xdog.claw.core.runtime.gateway import GatewayServer

    endpoint = tmp_path / 'native.sock'
    server = GatewayServer(ClawConfig(model='test/cleanup', data_dir=str(tmp_path / 'data'),
        socket_path=str(endpoint), pid_file=str(tmp_path / 'pid'), groups=(GroupDef(id='main'),)))
    calls, owner = [], threading.get_ident()
    original = os.unlink

    def unlink(path, *args, **kwargs):
        if Path(path) == endpoint:
            calls.append(threading.get_ident())
        return original(path, *args, **kwargs)

    try:
        with patch('xdog.claw.core.runtime.gateway._build_model_and_options',
                   return_value=('test/cleanup', lambda *args: EventStream.empty(AssistantMessage()))):
            await server.start()
        with monkeypatch.context() as trace:
            trace.setattr(os, 'unlink', unlink)
            await server.stop()
        assert calls and all(thread != owner for thread in calls), 'asyncio cleanup bypassed the native inode owner'
    finally:
        await server.stop()


async def test_cancelled_private_bind_closes_actual_late_descriptor_before_return(tmp_path, monkeypatch):
    from xdog.claw.acp import server as module

    app, backend, model = await make_backend(tmp_path)
    entered, release = threading.Event(), threading.Event()
    original = module.bind_private
    acquired = []

    def actual_bind(path):
        value = original(path)
        acquired.append(value)
        entered.set()
        assert release.wait(5)
        return value

    work = None
    with tempfile.TemporaryDirectory(prefix='acp-late-', dir='/data/t.Vy5SaQ') as directory:
        endpoint = Path(directory) / 'peer.sock'
        try:
            monkeypatch.setattr(module, 'bind_private', actual_bind)
            work = asyncio.create_task(ClawAcpServer.start(app, endpoint))
            assert await asyncio.to_thread(entered.wait, 3)
            assert acquired[0].socket.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN) == 0
            assert stat.S_IMODE(endpoint.stat().st_mode) == 0o600
            for _ in range(3):
                work.cancel()
                await asyncio.sleep(0)
            assert not work.done() and not model.inputs
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await work
            assert acquired[0].socket.fileno() == -1 and not endpoint.exists()
        finally:
            release.set()
            if work is not None:
                await asyncio.gather(work, return_exceptions=True)
            await backend.close()
            await app.stop()


@pytest.mark.parametrize('kind', ['file', 'stale-socket', 'symlink'])
async def test_private_acp_refuses_existing_endpoint_without_native_stale_cleanup(tmp_path, kind):
    app, backend, model = await make_backend(tmp_path)
    with tempfile.TemporaryDirectory(prefix='acp-old-', dir='/data/t.Vy5SaQ') as directory:
        endpoint = Path(directory) / 'peer.sock'
        target = Path(directory) / 'target'
        target.write_bytes(b'Original unrelated target')
        if kind == 'stale-socket':
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.bind(str(endpoint))
        elif kind == 'file':
            endpoint.write_bytes(b'Original endpoint bytes')
        else:
            endpoint.symlink_to(target)
        before = endpoint.lstat()
        try:
            with pytest.raises(ValueError, match='already exists'):
                await ClawAcpServer.start(app, endpoint)
            after = endpoint.lstat()
            assert (before.st_dev, before.st_ino) == (after.st_dev, after.st_ino)
            if kind == 'file':
                assert endpoint.read_bytes() == b'Original endpoint bytes'
            assert target.read_bytes() == b'Original unrelated target' and not model.inputs
        finally:
            await backend.close()
            await app.stop()


async def test_private_acp_cleanup_preserves_replacement_and_reports_actual_backend_error(tmp_path, monkeypatch):
    app, backend, model = await make_backend(tmp_path)
    with tempfile.TemporaryDirectory(prefix='acp-end-', dir='/data/t.Vy5SaQ') as directory:
        endpoint = Path(directory) / 'peer.sock'
        server = await ClawAcpServer.start(app, endpoint)
        original = server.backend.close
        calls = []

        async def actual_close_then_fail():
            await original()
            calls.append(True)
            raise OSError('Actual ACP peer cleanup failed')

        try:
            endpoint.unlink()
            endpoint.write_bytes(b'Replacement file is not the receiver inode')
            monkeypatch.setattr(server.backend, 'close', actual_close_then_fail)
            for _ in range(2):
                with pytest.raises(OSError, match='peer cleanup failed'):
                    await server.close()
            assert endpoint.read_bytes() == b'Replacement file is not the receiver inode'
            assert server._endpoint is None and calls == [True] and not model.inputs
        finally:
            try:
                await server.close()
            except OSError:
                pass
            await backend.close()
            await app.stop()


async def test_acp_preserves_canonical_alias_and_cancel_during_prompt_validation(tmp_path, monkeypatch):
    from xdog.claw.core.inputs.models import InputScope

    app, backend, model = await make_backend(tmp_path)
    peer = Peer('cancel-validation', None)
    alias = tmp_path / 'workspace-alias'
    alias.symlink_to(tmp_path, target_is_directory=True)
    ref = await backend.load_session('group:main', alias, peer, None)
    assert ref.cwd == tmp_path
    entered, release = threading.Event(), threading.Event()
    original = Path.resolve

    def resolve(path, *args, **kwargs):
        value = original(path, *args, **kwargs)
        if path == tmp_path:
            entered.set()
            assert release.wait(5)
        return value

    async def emit(update):
        raise AssertionError('No message was accepted before explicit cancel')

    work = None
    try:
        monkeypatch.setattr(Path, 'resolve', resolve)
        work = asyncio.create_task(backend.prompt(ref, peer, 'Do not accept', {}, emit))
        assert await asyncio.to_thread(entered.wait, 3)
        assert backend._prompts[peer.id, ref.id] is work
        await backend.cancel(ref, peer)
        assert not work.done() and not model.inputs
        assert await backend.inputs.store.records(InputScope('main', tmp_path)) == ()
        release.set()
        response = await work
        assert response.stop_reason == 'cancelled' and response.field_meta['xdog']['execution_continues'] is False
        assert not model.inputs
    finally:
        release.set()
        if work is not None:
            await asyncio.gather(work, return_exceptions=True)
        await backend.close()
        await app.stop()


async def test_endpoint_replacement_during_actual_bind_is_not_chmodded_or_published(tmp_path, monkeypatch):
    app, backend, model = await make_backend(tmp_path)
    original = socket.socket.bind
    server = None
    with tempfile.TemporaryDirectory(prefix='acp-race-', dir='/data/t.Vy5SaQ') as directory:
        endpoint = Path(directory) / 'peer.sock'

        def bind(sock, address):
            value = original(sock, address)
            if address == str(endpoint):
                endpoint.unlink()
                endpoint.write_bytes(b'Concurrent replacement is not the bound socket')
                endpoint.chmod(0o640)
            return value

        try:
            monkeypatch.setattr(socket.socket, 'bind', bind)
            with pytest.raises(ValueError, match='changed'):
                server = await ClawAcpServer.start(app, endpoint)
            assert endpoint.read_bytes() == b'Concurrent replacement is not the bound socket'
            assert stat.S_IMODE(endpoint.stat().st_mode) == 0o640 and not model.inputs
        finally:
            if server is not None:
                await server.close()
            await backend.close()
            await app.stop()
