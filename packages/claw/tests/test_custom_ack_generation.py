"""Claw ACK correlation and the real nested-parent completion under delayed frame delivery."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import pytest
from xdog.ai.types import AssistantMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer

from packages.claw.tests.test_extension_nested_ui import (
    test_claw_nested_stack_connection_leases_restore_same_parent_and_draft as nested_session,
)


async def test_socket_echoes_only_fully_validated_bounded_exact_integer_generation(tmp_path):
    calls = []
    def stream(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())
    config = ClawConfig(model='offline/model', data_dir=str(tmp_path / 'data'),
        socket_path=str(tmp_path / 'socket'), pid_file=str(tmp_path / 'pid'),
        groups=(GroupDef(id='main', is_main=True, workspace=str(tmp_path)),))
    server = GatewayServer(config)
    with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=('offline/model', stream)):
        await server.start()
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    async def exchange(value):
        writer.write((json.dumps(value) + '\n').encode())
        await writer.drain()
        async with asyncio.timeout(3):
            return json.loads(await reader.readline())
    try:
        assert (await exchange({'type': 'ping', 'extension_custom': 1}))['extension_custom'] == 1
        for child in (False, True):
            kind = 'child_extension_custom_request' if child else 'extension_custom_request'
            owner = {'type': kind, 'id': 'foreign', 'group_id': 'main', 'session_id': 'foreign',
                     **({'child_id': 'a' * 12, 'child_owner': 'b' * 32} if child else {})}
            event = {'kind': 'resize', 'width': 80, 'height': 27, 'painted': -1}
            for generation in (0, 1, 2**53 - 1):
                ack = await exchange({**owner, 'event': {**event, 'generation': generation}})
                assert ack['accepted'] is False  # Correlation never grants a foreign owner authority.
                assert type(ack['generation']) is int and ack['generation'] == generation
            for generation in (None, True, False, '1', 1.0, -1, 2**53):
                ack = await exchange({**owner, 'event': {**event, 'generation': generation}})
                assert ack['accepted'] is False and 'generation' not in ack
            ack = await exchange({**owner, 'event': event})
            assert ack['accepted'] is False and 'generation' not in ack  # Legacy request retains legacy failure.
            ack = await exchange({**owner, 'event': {**event, 'generation': 1, 'unknown': True}})
            assert ack['accepted'] is False and 'generation' not in ack  # No partial validation echo.
        assert not calls
    finally:
        writer.close()
        await writer.wait_closed()
        await asyncio.wait_for(server.stop(), 10)


@pytest.mark.parametrize('mode', ['', 'full'])
@pytest.mark.parametrize('latency', [0, .002, .007])
@pytest.mark.parametrize('repeat', range(2))
async def test_real_nested_resume_one_enter_with_concurrent_frame_delivery(
        tmp_path, monkeypatch, mode, latency, repeat):
    original = GatewayServer._write_response
    acknowledgements = []
    async def delayed_write(server, writer, data):
        if data.get('type') == 'extension_custom_frame' and latency:
            await asyncio.sleep(latency)  # Bounded socket scheduling; never bypass a broker/freshness check.
        if data.get('type') == 'extension_custom_ack':
            acknowledgements.append(dict(data))
        await original(server, writer, data)
    monkeypatch.setattr(GatewayServer, '_write_response', delayed_write)
    # Retains the original single Enter, eight-second bound, real worker and
    # original fullscreen/focus/draft/cursor assertions, without adding retries.
    await nested_session(tmp_path, False, mode)
    assert acknowledgements and all(type(row.get('generation')) is int for row in acknowledgements)
