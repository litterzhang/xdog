"""Claw independent sampling controls on a private socket, no live model/service."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer

from packages.agent.tests.test_mcp_sampling_executor import Adapter
from packages.agent.tests.test_mcp_sampling_registration import config as peer_config


async def setup(tmp_path, adapter=None):
    servers, log = peer_config(tmp_path)
    adapter = adapter or Adapter()
    config = ClawConfig(model='explicit-model', data_dir=str(tmp_path / 'data'),
        socket_path=str(tmp_path / 'gateway.sock'), pid_file=str(tmp_path / 'gateway.pid'),
        groups=(GroupDef(id='main', is_main=True, workspace=str(tmp_path / 'workspace')),), mcp_servers=servers)
    server = GatewayServer(config)
    with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=('explicit-model', adapter)):
        await server.start()
    runtime = server._orchestrator._runtimes['main']
    session = runtime.get_or_create_session(allow_reset=False)
    return server, config, runtime, session, adapter, log


async def receive(reader, kind):
    async with asyncio.timeout(5):
        while True:
            packet = json.loads(await reader.readline())
            if packet['type'] == kind:
                return packet
