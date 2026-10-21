"""Installed Eyes supplies gateway capability; only accepted managed work recovers."""
from __future__ import annotations

import asyncio
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pytest
from xdog.ai.types import AssistantMessage, TextContent
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.config import ClawConfig, GroupDef, load_config, save_config
from xdog.claw.core.runtime.gateway import GatewayServer
from xdog.eyes.hosting.manager import ManagedObservers
from xdog.eyes.managed_scheduler import SchedulerClocks


def test_no_eyes_enable_configuration_round_trips(tmp_path):
    assert not hasattr(ClawConfig(), "eyes_enabled")
    with pytest.raises(TypeError, match="eyes_enabled"):
        ClawConfig(eyes_enabled=True)
    path = tmp_path / "claw.yaml"
    save_config(ClawConfig(), path)
    assert "eyes_enabled" not in path.read_text()
    assert not hasattr(load_config(path), "eyes_enabled")


async def test_gateway_restores_accepted_expired_once_without_tui_or_replay(tmp_path, monkeypatch):
    calls, clock = [], [2_000_000_000.0]
    class ClockedObservers(ManagedObservers):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, clocks=SchedulerClocks(lambda: clock[0], lambda: clock[0]), **kwargs)
    monkeypatch.setattr('xdog.eyes.hosting.manager.ManagedObservers', ClockedObservers)
    def stream(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage(content=(TextContent(text='Restored report'),)))
    monkeypatch.setattr('xdog.claw.core.runtime.gateway._build_model_and_options', lambda _: ('test/gateway', stream))
    with tempfile.TemporaryDirectory(prefix='eyes-gw-') as short:
        data = Path(short) / 'data'
        config = ClawConfig(data_dir=str(data), model='test/gateway', socket_path=str(Path(short) / 'gateway.sock'),
            pid_file=str(Path(short) / 'gateway.pid'), enabled_tools=('eyes',),
            groups=(GroupDef(id='main', workspace=str(tmp_path)),))
        server = GatewayServer(config)
        await server.start()
        try:
            assert server._application_capabilities.get("eyes") is not None and server._acp_server is not None
            assert not calls  # Receiver/observer setup never loads a model.
            now = clock[0]
            manager = server._application_capabilities.get('eyes')
            created = await manager.create('main', 'restored', {'type': 'scheduler', 'schedule': {
                'at': datetime.fromtimestamp(now + 10, UTC).isoformat()}}, 'Report later')
        finally:
            await server.stop()
        assert not server._acp_server and server._application_capabilities.get("eyes") is None
        assert not Path(config.socket_path).exists()
        clock[0] += 100
        server = GatewayServer(config)
        await server.start()
        try:
            async with asyncio.timeout(5):
                while True:
                    row = await server._application_capabilities.get("eyes").status('main', created['id'])
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


@pytest.mark.parametrize('default_goal', [False, True])
async def test_eyes_absent_preserves_default_goal_transport_without_observers(tmp_path, monkeypatch, default_goal):
    from xdog.agent.capabilities.runtime import discover
    monkeypatch.setattr('xdog.agent.capabilities.runtime.discover',
                        lambda scope: tuple(item for item in discover(scope)
                                            if item.name != 'eyes' and (default_goal or item.name != 'goal')))
    monkeypatch.setattr('xdog.claw.core.runtime.gateway._build_model_and_options', lambda _: ('test/no-call', None))
    config = ClawConfig(data_dir=str(tmp_path / 'data'), socket_path=str(tmp_path / 'gateway.sock'),
                        pid_file=str(tmp_path / 'gateway.pid'), enabled_tools=())
    gateway = GatewayServer(config)
    await gateway.start()
    try:
        assert (gateway._application_capabilities is None
                or gateway._application_capabilities.get("eyes") is None)
        assert (gateway._acp_server is not None) is default_goal
        assert (tmp_path / 'data' / 'acp').exists() is default_goal
        if default_goal:
            goal = gateway._orchestrator._runtimes['main'].capabilities.get('goal')
            assert goal is not None and await goal.service.current() is None
        assert not (tmp_path / 'data' / 'observers').exists()
    finally:
        await gateway.stop()


async def test_private_eye_endpoint_conflict_retains_other_owner(tmp_path, monkeypatch):
    monkeypatch.setattr('xdog.claw.core.runtime.gateway._build_model_and_options', lambda _: ('test/no-call', None))
    with tempfile.TemporaryDirectory(prefix='eyes-conflict-') as short:
        private = Path(short)
        endpoint = private / 'acp.sock'
        endpoint.write_text('another owner')
        gateway = GatewayServer(ClawConfig(data_dir=str(tmp_path / 'data'), socket_path=str(tmp_path / 'gateway.sock'),
            pid_file=str(tmp_path / 'gateway.pid'), enabled_tools=(), acp_socket_path=str(endpoint)))
        with pytest.raises(ValueError, match='exists'):
            await gateway.start()
        assert endpoint.read_text() == 'another owner'
        assert (gateway._application_capabilities is None
                or gateway._application_capabilities.get("eyes") is None)
        assert gateway._acp_server is None
        await gateway.stop()


async def test_installed_eyes_opens_capability_without_creating_work(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr('xdog.claw.core.runtime.gateway._build_model_and_options',
                        lambda _: ('test/no-call', lambda *args: calls.append(args)))
    with tempfile.TemporaryDirectory(prefix='eyes-auto-') as short:
        root = Path(short)
        gateway = GatewayServer(ClawConfig(data_dir=str(root / 'data'),
            socket_path=str(root / 'native.sock'), pid_file=str(root / 'pid'),
            groups=(GroupDef(id='main', workspace=str(tmp_path)),)))
        try:
            await gateway.start()
            assert gateway._application_capabilities.get("eyes") is not None and gateway._acp_server is not None
            assert gateway._acp_server.path == root / 'data/acp/agent.sock'
            assert await gateway._application_capabilities.get("eyes").list('main') == ()
            manager = gateway._application_capabilities.get('eyes')
            assert not manager._tasks and not manager._files
            assert not calls
            assert 'eyes' in {tool.name for tool in gateway._orchestrator._runtimes['main'].tools}
        finally:
            await gateway.stop()
        assert not (root / 'data/acp/agent.sock').exists()


async def test_installed_eyes_without_transport_dependency_fails_before_resources(tmp_path, monkeypatch):
    from importlib.util import find_spec
    monkeypatch.setattr('xdog.claw.core.runtime.gateway.find_spec',
                        lambda name: None if name == 'acp' else find_spec(name))
    root = tmp_path / 'data'
    gateway = GatewayServer(ClawConfig(data_dir=str(root)))
    with pytest.raises(RuntimeError, match=r'xdog-claw\[acp\]'):
        await gateway.start()
    assert not root.exists()
