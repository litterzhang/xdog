"""Removed remote-draft/plugin-key protocols cannot access local drafts or run an Agent."""
from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from xdog.ai.types import AssistantMessage, Model
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer
from xdog.tui.child_draft_store import ChildDraftStore, encode_draft
from xdog.tui.child_drafts import ChildDraft


class OfflineAdapter:
    def __init__(self):
        self.calls = []
    def resolve_model(self, name):
        return Model(id=name, context_window=128000, input=('text',))
    def __call__(self, *args):
        self.calls.append(args)
        return EventStream.empty(AssistantMessage())


@pytest.fixture
async def gateway(tmp_path):
    adapter = OfflineAdapter()
    config = ClawConfig(model='offline/model', data_dir=str(tmp_path / 'data'),
        socket_path=str(tmp_path / 'gateway.sock'), pid_file=str(tmp_path / 'gateway.pid'),
        groups=(GroupDef(id='main', is_main=True, workspace=str(tmp_path / 'workspace')),))
    server = GatewayServer(config)
    with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=('offline/model', adapter)):
        await server.start()
    runtime = server._orchestrator._runtimes['main']
    session = runtime.get_or_create_session(allow_reset=False)
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    async def send(packet):
        writer.write((json.dumps(packet) + '\n').encode())
        await writer.drain()
        async with asyncio.timeout(3):
            return json.loads(await reader.readline())
    try:
        yield server, config, session, adapter, send
    finally:
        writer.close()
        await writer.wait_closed()
        await server.stop()


async def test_removed_capabilities_cannot_be_renegotiated(gateway):
    server, _, session, adapter, send = gateway
    removed = {'parent_saved_drafts': 1, 'parent_saved_images': 1, 'parent_saved_save': 1,
               'parent_saved_discard': 1, 'parent_saved_uploads': 1, 'terminal_input': 1,
               'terminal_child_input': 1, 'terminal_release_input': 1, 'extension_custom': 1,
               'extension_custom_rich': 1, 'extension_custom_overlay': 1, 'extension_forms': 1,
               'extension_multi_forms': 1, 'extension_custom_nested': 1, 'mcp_sampling': 1}
    pong = await send({'type': 'ping', 'group_id': 'main', **removed})
    assert pong['type'] == 'pong'
    assert not set(removed).intersection(pong)
    assert 'parent_draft_owner' not in pong and 'terminal_owner' not in pong
    assert not adapter.calls and not session.agent.messages
    assert not server._orchestrator.is_group_running('main')


@pytest.mark.parametrize('kind', ['parent_draft_control', 'terminal_input', 'terminal_input_cancel',
                                  'extension_custom_request', 'child_extension_custom_request', 'mcp_sampling_control'])
async def test_old_packets_refused_without_read_write_or_execution(gateway, kind):
    server, config, session, adapter, send = gateway
    root = Path(config.data_dir) / 'saved-parent-drafts'
    root.parent.chmod(0o700)
    store = ChildDraftStore(root)
    saved = store.save(encode_draft(ChildDraft('PRIVATE LOCAL DRAFT'), label='parent',
                                  scope=session.meta.session_id, action='parent', slot=''))
    before = {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()}
    for action in ('catalog', 'read', 'save_prepare', 'save_confirm', 'discard_confirm', 'upload_chunk'):
        result = await send({'type': kind, 'group_id': 'main', 'session_id': session.meta.session_id,
                             'request_id': 'legacy', 'action': action, 'data': 'PRIVATE INPUT',
                             'text': 'ATTACKER REPLACEMENT', 'snapshot_id': saved.id})
        assert result['type'] == 'error' and 'Unknown message type' in result['message']
        assert 'PRIVATE' not in json.dumps(result)
    assert {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()} == before
    assert store.load(saved.id).sha256 == saved.sha256
    assert not session.agent.messages and not adapter.calls
    assert not server._orchestrator.is_group_running('main')


def test_remote_and_terminal_protocol_modules_and_capture_api_removed():
    from xdog.claw.core.runtime.orchestrator import Orchestrator

    for module in ('xdog.claw.core.runtime.parent_draft_protocol', 'xdog.claw.core.runtime.terminal_protocol',
                   'xdog.tui.parent_draft_receiver', 'xdog.tui.components.remote_parent_drafts'):
        assert importlib.util.find_spec(module) is None
    for name in ('capture_terminal_input', 'capture_child_terminal', 'child_terminal_metadata'):
        assert not hasattr(Orchestrator, name)


@pytest.mark.parametrize('kind', ['extension_custom_request', 'child_extension_custom_request', 'mcp_sampling_control'])
async def test_retired_custom_request_never_echoes_or_grants_foreign_generation(gateway, kind):
    server, _, session, adapter, send = gateway
    for generation in (None, True, False, '1', 1.0, -1, 0, 1, 2**53 - 1, 2**53):
        result = await send({'type': kind, 'id': 'foreign', 'group_id': 'main',
                             'session_id': session.meta.session_id,
                             'event': {'kind': 'resize', 'width': 80, 'height': 27,
                                       'painted': -1, 'generation': generation},
                             'child_id': 'a' * 12, 'child_owner': 'b' * 32})
        assert result['type'] == 'error'
        assert 'generation' not in result and 'accepted' not in result
        assert not adapter.calls and not session.agent.messages
    assert not server._orchestrator.is_group_running('main')
