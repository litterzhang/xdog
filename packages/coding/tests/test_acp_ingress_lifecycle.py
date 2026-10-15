"""Service leases and endpoint cleanup never replace another live listener."""

import asyncio
import socket
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def ingress_config(tmp_path):
    from xdog.coding.acp.ingress import IngressConfig

    with tempfile.TemporaryDirectory(prefix='acp-life-') as short:
        yield IngressConfig(tmp_path/'sessions',tmp_path/'inbox',Path(short)/'acp.sock')


async def test_competing_starts_share_a_single_lease_and_can_restart_after_close(ingress_config):
    from xdog.coding.acp.ingress import IngressServer

    results = await asyncio.gather(IngressServer.start(ingress_config), IngressServer.start(ingress_config),
                                   return_exceptions=True)
    servers = [value for value in results if isinstance(value, IngressServer)]
    assert len(servers) == 1
    assert len([v for v in results if isinstance(v, RuntimeError)]) == 1
    await servers[0].aclose()
    next_server = await IngressServer.start(ingress_config)
    assert next_server.generation != servers[0].generation
    await next_server.aclose()


async def test_stale_refused_socket_is_replaced_only_with_free_lease(ingress_config):
    from xdog.coding.acp.ingress import IngressServer

    sock = socket.socket(socket.AF_UNIX)
    sock.bind(str(ingress_config.address))
    sock.close()
    ingress_config.address.chmod(0o600)
    server = await IngressServer.start(ingress_config)
    try:
        assert server.server.is_serving()
    finally:
        await server.aclose()


async def test_another_live_listener_is_never_unlinked_even_with_free_ingress_lease(ingress_config):
    from xdog.coding.acp.ingress import IngressServer

    def close(reader, writer):
        writer.close()

    existing = await asyncio.start_unix_server(close, path=str(ingress_config.address))
    ingress_config.address.chmod(0o600)
    original = ingress_config.address.stat().st_ino
    try:
        with pytest.raises(RuntimeError):
            await IngressServer.start(ingress_config)
        assert ingress_config.address.stat().st_ino == original
        assert existing.is_serving()
    finally:
        existing.close()
        await existing.wait_closed()


@pytest.mark.parametrize('kind', ['regular-file','socket-mode','directory-mode','symlink'])
async def test_bad_endpoint_metadata_is_not_silently_fixed(ingress_config, kind):
    from xdog.coding.acp.ingress import IngressServer

    address = ingress_config.address
    if kind == 'regular-file':
        address.touch(mode=0o600)
    elif kind == 'symlink':
        address.symlink_to(address.parent/'nonexistent')
    else:
        sock = socket.socket(socket.AF_UNIX)
        sock.bind(str(address))
        sock.close()
        address.chmod(0o666 if kind == 'socket-mode' else 0o600)
        if kind == 'directory-mode':
            address.parent.chmod(0o755)
    try:
        with pytest.raises((ValueError,OSError)):
            await IngressServer.start(ingress_config)
        assert address.exists() or address.is_symlink()
    finally:
        address.parent.chmod(0o700)


async def test_cleanup_does_not_remove_replacement_inode(ingress_config):
    from xdog.coding.acp.ingress import IngressServer

    server = await IngressServer.start(ingress_config)
    ingress_config.address.unlink()
    ingress_config.address.touch(mode=0o600)
    await server.aclose()
    assert ingress_config.address.is_file()


async def test_cancelled_start_releases_lease_for_a_new_explicit_start(ingress_config, monkeypatch):
    from xdog.coding.acp.ingress import IngressServer

    original = asyncio.start_unix_server
    entered = asyncio.Event()

    async def wait(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(asyncio,'start_unix_server',wait)
    task = asyncio.create_task(IngressServer.start(ingress_config))
    await asyncio.wait_for(entered.wait(),3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    monkeypatch.setattr(asyncio,'start_unix_server',original)
    server = await IngressServer.start(ingress_config)
    await server.aclose()


async def test_cancelled_publication_settles_its_worker_before_releasing_lease(ingress_config, monkeypatch):
    import threading

    from xdog.coding.acp.ingress import IngressServer

    entered, release = threading.Event(), threading.Event()
    original = IngressServer._publish

    def blocked(self):
        entered.set()
        release.wait(5)
        original(self)

    monkeypatch.setattr(IngressServer, '_publish', blocked)
    starting = asyncio.create_task(IngressServer.start(ingress_config))
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        starting.cancel()
        await asyncio.sleep(0.02)
        assert not starting.done(), 'cancel must not release a lease while its publisher is alive'
        with pytest.raises(RuntimeError):
            await IngressServer.start(ingress_config)
    finally:
        release.set()
        await asyncio.gather(starting, return_exceptions=True)
    monkeypatch.setattr(IngressServer, '_publish', original)
    server = await IngressServer.start(ingress_config)
    try:
        assert server.server.is_serving()
    finally:
        await server.aclose()


async def test_missing_control_record_does_not_authorize_stealing_live_lease(ingress_config):
    from xdog.coding.acp.ingress import IngressServer
    from xdog.coding.core.sessions.ownership import runtime_directory

    server = await IngressServer.start(ingress_config)
    (runtime_directory(ingress_config.sessions_dir)/'.acp-ingress.json').unlink()
    try:
        with pytest.raises(RuntimeError):
            await IngressServer.start(ingress_config)
        assert server.server.is_serving()
    finally:
        await server.aclose()


async def test_service_close_closes_active_connections_before_awaiting_server(ingress_config):
    from xdog.coding.acp.ingress import IngressServer

    from packages.coding.tests.test_acp_ingress import connected

    server = await IngressServer.start(ingress_config)
    closing = None
    try:
        async with connected(ingress_config.address):
            closing = asyncio.create_task(server.aclose())
            done, pending = await asyncio.wait({closing},timeout=1)
            assert closing in done, 'server shutdown must close its active transports before wait_closed'
    finally:
        if closing is not None:
            await closing
        await server.aclose()


async def test_repeated_cancellation_cannot_abandon_a_live_lease_acquisition_worker(ingress_config, monkeypatch):
    import threading

    from xdog.coding.acp.ingress import IngressServer

    original = IngressServer._acquire
    entered, release = threading.Event(), threading.Event()

    def blocked(self):
        entered.set()
        release.wait(5)
        return original(self)

    monkeypatch.setattr(IngressServer,'_acquire',blocked)
    task = asyncio.create_task(IngressServer.start(ingress_config))
    try:
        assert await asyncio.to_thread(entered.wait,3)
        task.cancel()
        await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.sleep(0.01)
        assert not task.done(), 'repeated cancellation cannot leave an acquiring worker unowned'
    finally:
        release.set()
        await asyncio.gather(task,return_exceptions=True)
    monkeypatch.setattr(IngressServer,'_acquire',original)
    server = await IngressServer.start(ingress_config)
    await server.aclose()
