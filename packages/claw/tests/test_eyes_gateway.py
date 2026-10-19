"""Only explicitly enabled gateway-owned Eyes registrations recover; no daemon."""
from __future__ import annotations

import asyncio
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pytest
from xdog.ai.types import AssistantMessage, TextContent
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.config import ClawConfig, GroupDef, load_config, save_config
from xdog.claw.core.observers.manager import ManagedObservers
from xdog.claw.core.runtime.gateway import GatewayServer
from xdog.eyes.managed_scheduler import SchedulerClocks


@pytest.mark.parametrize('value', ['true', 1, None])
def test_invalid_opt_in_never_enables_observation(value):
    with pytest.raises(ValueError, match='boolean'):
        ClawConfig(eyes_enabled=value)


def test_explicit_opt_in_round_trips(tmp_path):
    assert ClawConfig().eyes_enabled is False
    path = tmp_path / 'claw.yaml'
    save_config(ClawConfig(eyes_enabled=True), path)
    assert load_config(path).eyes_enabled is True


async def test_gateway_restores_accepted_expired_once_without_tui_or_replay(tmp_path, monkeypatch):
    calls, clock = [], [2_000_000_000.0]
    class ClockedObservers(ManagedObservers):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, clocks=SchedulerClocks(lambda: clock[0], lambda: clock[0]), **kwargs)
    monkeypatch.setattr('xdog.claw.core.observers.manager.ManagedObservers', ClockedObservers)
    def stream(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage(content=(TextContent(text='Restored report'),)))
    monkeypatch.setattr('xdog.claw.core.runtime.gateway._build_model_and_options', lambda _: ('test/gateway', stream))
    with tempfile.TemporaryDirectory(prefix='eyes-gw-') as short:
        data = Path(short) / 'data'
        config = ClawConfig(data_dir=str(data), model='test/gateway', socket_path=str(Path(short) / 'gateway.sock'),
            pid_file=str(Path(short) / 'gateway.pid'), enabled_tools=('eyes',), eyes_enabled=True,
            groups=(GroupDef(id='main', workspace=str(tmp_path)),))
        server = GatewayServer(config)
        await server.start()
        try:
            assert server._observers is not None and server._acp_server is not None
            assert not calls  # Receiver/observer setup never loads a model.
            now = clock[0]
            created = await server._observers.create('main', 'restored', {'type': 'scheduler', 'schedule': {
                'at': datetime.fromtimestamp(now + 10, UTC).isoformat()}}, 'Report later')
        finally:
            await server.stop()
        assert not server._acp_server and not server._observers
        assert not Path(config.socket_path).exists()
        clock[0] += 100
        server = GatewayServer(config)
        await server.start()
        try:
            async with asyncio.timeout(5):
                while True:
                    row = await server._observers.status('main', created['id'])
                    if not row['enabled']:
                        break
                    await asyncio.sleep(.01)
            assert len(calls) == 1
            assert row['occurrences'][0]['execution_status'] == 'succeeded'
            # No creator selected a recipient: success is not an implicit broadcast.
            assert row['occurrences'][0]['delivery_status'] == 'not-required'
        finally:
            await server.stop()
        # Another full receiver/gateway reconstruction does not re-run the once.
        server = GatewayServer(config)
        await server.start()
        try:
            await asyncio.sleep(.03)
            assert len(calls) == 1
        finally:
            await server.stop()


async def test_disabled_gateway_creates_neither_acp_nor_eyes(tmp_path, monkeypatch):
    monkeypatch.setattr('xdog.claw.core.runtime.gateway._build_model_and_options', lambda _: ('test/no-call', None))
    config = ClawConfig(data_dir=str(tmp_path / 'data'), socket_path=str(tmp_path / 'gateway.sock'),
                        pid_file=str(tmp_path / 'gateway.pid'), enabled_tools=())
    gateway = GatewayServer(config)
    await gateway.start()
    try:
        assert gateway._observers is None and gateway._acp_server is None
        assert not (tmp_path / 'data' / 'acp').exists()
        assert await gateway._orchestrator._runtimes['main'].goal_host.service.list() == ()
    finally:
        await gateway.stop()


async def test_private_eye_endpoint_conflict_retains_other_owner(tmp_path, monkeypatch):
    monkeypatch.setattr('xdog.claw.core.runtime.gateway._build_model_and_options', lambda _: ('test/no-call', None))
    with tempfile.TemporaryDirectory(prefix='eyes-conflict-') as short:
        private = Path(short)
        endpoint = private / 'acp.sock'
        endpoint.write_text('another owner')
        gateway = GatewayServer(ClawConfig(data_dir=str(tmp_path / 'data'), socket_path=str(tmp_path / 'gateway.sock'),
            pid_file=str(tmp_path / 'gateway.pid'), enabled_tools=(), eyes_enabled=True, acp_socket_path=str(endpoint)))
        with pytest.raises(ValueError, match='exists'):
            await gateway.start()
        assert endpoint.read_text() == 'another owner'
        assert gateway._observers is None and gateway._acp_server is None
        await gateway.stop()
