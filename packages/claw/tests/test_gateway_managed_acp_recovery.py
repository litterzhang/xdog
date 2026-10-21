"""Real gateway startup recovers only its default managed ACP socket."""
from __future__ import annotations

import asyncio
import socket
import sys
import tempfile
from pathlib import Path
from unittest.mock import Mock

import pytest
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer


@pytest.fixture
def gateway_config(monkeypatch):
    # Model construction returns a name and stream function, as in test_gateway.py.
    # No turn is submitted: even an accidental model call must fail locally.
    stream = Mock(side_effect=AssertionError("Gateway startup must not call a model"))
    monkeypatch.setattr(
        "xdog.claw.core.runtime.gateway._build_model_and_options",
        Mock(return_value=("test/dummy", stream)),
    )
    # pytest's nested tmp_path can exceed the Unix-domain socket path limit.
    with tempfile.TemporaryDirectory(prefix="gw-acp-", dir="/tmp") as directory:
        root = Path(directory)
        yield ClawConfig(
            model="test-model",
            data_dir=str(root / "data"),
            socket_path=str(root / "gateway.sock"),
            pid_file=str(root / "gateway.pid"),
            groups=(GroupDef(id="main", name="TestClaw", is_main=True),),
        )
        stream.assert_not_called()


def _leave_stale_socket(path: Path) -> None:
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stale:
        stale.bind(str(path))
    assert path.is_socket()


async def test_gateway_recovers_default_managed_acp_stale_socket(gateway_config):
    endpoint = Path(gateway_config.data_dir) / "acp" / "agent.sock"
    _leave_stale_socket(endpoint)
    server = GatewayServer(gateway_config)
    try:
        await server.start()
        assert server._acp_server is not None
        assert server._acp_server.path == endpoint
        assert endpoint.is_socket()
        assert Path(gateway_config.socket_path).is_socket()
        assert Path(gateway_config.pid_file).is_file()
        # The replacement endpoint really listens, rather than just leaving a path.
        _, writer = await asyncio.open_unix_connection(str(endpoint))
        writer.close()
        await writer.wait_closed()
    finally:
        await server.stop()

    assert not endpoint.exists()
    assert not Path(gateway_config.socket_path).exists()
    assert not Path(gateway_config.pid_file).exists()


async def test_gateway_rejects_explicit_acp_stale_socket_without_removing_it(gateway_config):
    from dataclasses import replace

    endpoint = Path(gateway_config.data_dir).parent / "explicit" / "agent.sock"
    _leave_stale_socket(endpoint)
    original = endpoint.lstat()
    config = replace(gateway_config, acp_socket_path=str(endpoint))
    server = GatewayServer(config)
    try:
        with pytest.raises(ValueError, match="ACP endpoint already exists"):
            await server.start()
        assert endpoint.is_socket()
        current = endpoint.lstat()
        assert (current.st_dev, current.st_ino) == (original.st_dev, original.st_ino)
    finally:
        await server.stop()

    assert endpoint.is_socket()
    current = endpoint.lstat()
    assert (current.st_dev, current.st_ino) == (original.st_dev, original.st_ino)
    assert not Path(config.socket_path).exists()
    assert not Path(config.pid_file).exists()


async def test_managed_acp_lease_survives_bind_until_process_crash(gateway_config):
    from xdog.claw.core.runtime.unix_endpoint import bind_private, close

    endpoint = Path(gateway_config.data_dir) / "acp" / "agent.sock"
    # Keep the bound descriptor and lease alive without listening or running services.
    child_code = (
        "import signal, sys\n"
        "from pathlib import Path\n"
        "from xdog.claw.core.runtime.unix_endpoint import bind_private\n"
        "owned = bind_private(Path(sys.argv[1]), recover_stale=True)\n"
        "print('bound', flush=True)\n"
        "signal.pause()\n"
    )
    child = await asyncio.create_subprocess_exec(
        sys.executable, "-c", child_code, str(endpoint),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    recovered = None
    try:
        assert child.stdout is not None
        assert await asyncio.wait_for(child.stdout.readline(), timeout=10) == b"bound\n"
        original = endpoint.lstat()
        with pytest.raises(RuntimeError, match="Managed ACP endpoint is already owned"):
            await asyncio.to_thread(bind_private, endpoint, recover_stale=True)
        current = endpoint.lstat()
        assert (current.st_dev, current.st_ino) == (original.st_dev, original.st_ino)

        child.kill()  # Only our isolated test process; no graceful endpoint cleanup.
        await asyncio.wait_for(child.wait(), timeout=5)
        assert endpoint.is_socket()
        recovered = await asyncio.to_thread(bind_private, endpoint, recover_stale=True)
        assert recovered.path == endpoint
        assert endpoint.is_socket()
    finally:
        if child.returncode is None:
            child.kill()
        await asyncio.wait_for(child.communicate(), timeout=5)
        if recovered is not None:
            await asyncio.to_thread(close, recovered)

    assert not endpoint.exists()
