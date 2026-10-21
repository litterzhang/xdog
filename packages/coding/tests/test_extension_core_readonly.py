"""Core/child hosts keep metadata, native queues and compaction hooks without SDK control adapters."""
from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.agent.core import CustomAgentMessage
from xdog.agent.extensions.compaction import CompactionVeto
from xdog.ai.types import AssistantMessage, TextContent, UserMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.permissions import PermissionManager
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.settings_manager import SettingsManager

from packages.agent.tests.test_context_messages import row
from packages.coding.tests.test_child_agents import create as coding_parent

FIXTURES = Path(__file__).parents[2] / 'agent/tests/fixtures/extensions'
HANDLERS = ('editor_write_handler', 'name_request_handler', 'queued_input_handler',
            'active_tools_request_handler', 'compaction_request_handler', 'context_message_handler')


@pytest.mark.parametrize('host', ['coding', 'claw'])
async def test_real_child_readonly_worker_keeps_live_pending_boolean_and_native_queue_journal(tmp_path, host):
    root = tmp_path / 'extensions'
    shutil.copytree(FIXTURES / 'readonlyhost', root / 'readonlyhost')
    server = None
    calls = []
    if host == 'coding':
        settings = {'extensions': ['readonlyhost'], 'extension_dir': str(root)}
        (tmp_path / 'settings.json').write_text(json.dumps(settings))
        parent, calls = coding_parent(tmp_path, block=True)
    else:
        from packages.claw.tests.test_child_agents import stream_fn
        config = ClawConfig(model='test/model', data_dir=str(tmp_path / 'data'),
            socket_path=str(tmp_path / 'gateway.sock'), pid_file=str(tmp_path / 'gateway.pid'),
            groups=(GroupDef(id='main', is_main=True, workspace=str(tmp_path / 'workspace')),),
            extensions=('readonlyhost',), extension_dir=str(root))
        server = GatewayServer(config)
        with patch('xdog.claw.core.runtime.gateway._build_model_and_options',
                   return_value=('test/model', stream_fn(calls, block=True))):
            await server.start()
        parent = server._orchestrator._runtimes['main'].get_or_create_session(allow_reset=False)
    try:
        await parent.initialize_extensions()
        identifier = await parent.children.runtime.start('NATIVE CHILD /quit')
        async with asyncio.timeout(5):
            while not calls:
                await asyncio.sleep(.003)
        runtime = parent.children.extensions.owners[identifier]
        assert all(not hasattr(runtime, name) for name in HANDLERS)
        async def probe():
            return json.loads(await runtime.command('ext-readonlyhost-probe', ''))
        first = await probe()
        assert first['pending'] is False and first['tools']['owner_id'] == identifier
        assert first['model']['session_id'] == parent.extensions.context()['session_id']
        await parent.children.runtime.control('follow-up', identifier, 'NATIVE RETAINED QUEUE')
        queued = await probe()
        assert queued['pending'] is True and queued['session'] is None
        assert queued['sessions'] is None and queued['tree'] is None
        snapshot = (await parent.children.runtime.control('queue', identifier, '{"action":"list"}'))['queue']
        assert len(snapshot['inputs']) == 1 and snapshot['inputs'][0]['content'] == 'NATIVE RETAINED QUEUE'
        reader = runtime.pending_messages
        await parent.children.runtime.control('cancel', identifier)
        assert runtime.pending_messages is None and await reader() is None
        assert not parent.children.extensions._pending_readers
        saved = parent.children.store.load()[0]
        assert saved['undelivered_inputs'][0]['content'] == 'NATIVE RETAINED QUEUE'
        assert saved['status'] == 'canceled' and not parent.agent.messages and len(calls) == 1
    finally:
        if server is not None:
            await server.stop()
        else:
            await parent.children.runtime.aclose()
            await parent.extensions.aclose()
            parent.dispose()


def session(tmp_path, names=(), settings=None):
    root = tmp_path / 'extensions'
    for name in names:
        shutil.copytree(FIXTURES / name, root / name)
    config = tmp_path / 'settings.json'
    config.write_text(json.dumps({'extensions': list(names), 'extension_dir': str(root),
                                 'extension_settings': settings or {}}))
    store = SessionManager(tmp_path / 'sessions')
    calls = []
    def stream(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage(content=(TextContent(text='RETAINED NATIVE RESPONSE'),)))
    parent = AgentSession(agent=Agent(stream, config=AgentConfig(model='offline/model', context_window=4000)),
        session_data=store.create_session(working_dir=tmp_path, model='offline/model'), session_manager=store,
        settings=SettingsManager(global_path=config), working_dir=tmp_path, tool_registry=None, bash=None,
        permissions=PermissionManager('deny'))
    return parent, calls


@pytest.mark.parametrize('with_image', [False, True])
async def test_historical_context_admission_survives_without_extension_continuation_adapter(tmp_path, with_image):
    parent, calls = session(tmp_path)
    value = row(session_id=parent.session_id)
    if with_image:
        from xdog.ai.types import Model

        from packages.coding.tests.extension_session_helpers import PNG
        parent.model_info = Model(id='offline/model', input=('text', 'image'))
        value['content'].append({'type': 'image', 'data': PNG, 'mime_type': 'image/png'})
    try:
        assert parent.context_messages.deliver is None
        assert all(not hasattr(parent.extensions, name) for name in HANDLERS)
        # Seed an old stored record through the existing codec/store, not a new extension request.
        await parent.context_messages.enqueue(value, lambda: True)
        await parent.send_message('Native explicit user prompt')
        records = [message for message in parent.messages if isinstance(message, CustomAgentMessage)]
        assert len(records) == 1 and records[0].data['id'] == value['id']
        saved = parent.session_manager.load_session(parent.session_id)
        assert any(isinstance(message, CustomAgentMessage) and message.data == value for message in saved.messages)
        assert len(calls) == 1 and 'EXPLICIT CUSTOM CONTENT' in str(calls[0][1].messages)
        assert 'NOT MODEL INPUT' not in str(calls[0][1].messages)
        assert (await parent.context_messages.capture())[1] == ()
        assert parent.permissions.mode == 'deny'
    finally:
        await parent.children.runtime.aclose()
        await parent.extensions.aclose()
        parent.dispose()


@pytest.mark.parametrize('mode', ['override', 'cancel'])
async def test_native_compact_still_runs_real_before_after_hooks_without_compaction_request_binding(tmp_path, mode):
    parent, calls = session(tmp_path, ('compactor',), {'compactor': {'mode': mode}})
    history = []
    for index in range(6):
        history.extend([UserMessage(content=f'original user {index} ' + 'x' * 2000),
                        AssistantMessage(content=(TextContent(text=f'original answer {index}'),))])
    parent.agent.replace_messages(history)
    parent._persist()
    try:
        if mode == 'cancel':
            with pytest.raises(CompactionVeto):
                await parent.compact('Keep the real lifecycle')
            assert parent.messages == history
        else:
            await parent.compact('Keep the real lifecycle')
            assert 'CUSTOM-COMPACTION-SUMMARY' in str(parent.messages[0])
            assert parent.session_data.compaction['source'] == 'compactor'
        value = json.loads(await parent.extension_command('ext-compactor-status'))
        assert value['before'] == 1 and value['after'] == (1 if mode == 'override' else 0)
        assert value['focus'] == 'Keep the real lifecycle' and value['no_borrowed_authority']
        assert not calls and parent.permissions.mode == 'deny'
        assert all(not hasattr(parent.extensions, name) for name in HANDLERS)
    finally:
        await parent.children.runtime.aclose()
        await parent.extensions.aclose()
        parent.dispose()
