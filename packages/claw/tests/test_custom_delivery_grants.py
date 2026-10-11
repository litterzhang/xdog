"""Adversarial real-socket canonical continuation grants; no user-message substitute."""
from __future__ import annotations

import asyncio
import json
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
from xdog.agent.core import CustomAgentMessage
from xdog.ai.types import AssistantMessage, TextContent, UserMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer
from xdog.claw.core.types import ContextContinuation

from packages.claw.tests.test_extension_message_gateway import handshake, peer


@asynccontextmanager
async def delivery_server(tmp_path):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/customdelivery',
                    root / 'customdelivery')
    calls = []
    def stream(model, context, options):
        calls.append(context)
        return EventStream.empty(AssistantMessage(content=(TextContent(text='GRANT-DONE'),)))
    config = ClawConfig(model='test/model', data_dir=str(tmp_path / 'data'),
        socket_path=str(tmp_path / 'gateway.sock'), pid_file=str(tmp_path / 'gateway.pid'),
        groups=tuple(GroupDef(id=key, is_main=key == 'main', workspace=str(tmp_path / key))
                     for key in ('main', 'foreign')),
        extensions=('customdelivery',), extension_dir=str(root))
    server = GatewayServer(config)
    with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=('test/model', stream)):
        await server.start()
    runtime = server._orchestrator._runtimes['main']
    owner = runtime.get_or_create_session(allow_reset=False)
    try:
        yield server, runtime, owner, config, calls
    finally:
        await server.stop()


async def negotiate(send, receive):
    return await handshake(send, receive, extension_context_messages=1, extension_context_delivery_modes=1)


async def review(send, receive, session, *, fail=False, approve=True):
    await send({'type': 'extension_control', 'request_id': 'command', 'group_id': 'main',
        'session_id': session, 'action': 'command', 'name': 'ext-customdelivery-request',
        'args': json.dumps({'delivery': 'steer', 'trigger': True, 'fail': fail})})
    dialog = await receive('extension_dialog')
    assert dialog['dialog']['operation'] == 'context_message'
    await send({'type': 'extension_dialog_answer', 'id': dialog['dialog']['id'], 'value': approve})
    return await receive('extension_result')


async def grant(send, receive, owner):
    result = await review(send, receive, owner.meta.session_id)
    assert result['success'] is True and result['context_pending'] is True
    receipt = await receive('extension_context_delivery_result')
    assert receipt['success'] is True and receipt['continuation'] is True
    assert receipt['request_id'] == result['request_id'] == 'command'
    token = await receive('extension_context_continuation')
    assert token['request_id'] == receipt['request_id']
    assert token['session_id'] == receipt['session_id'] == owner.meta.session_id
    assert sum(isinstance(item, CustomAgentMessage) for item in owner.agent.messages) == 1
    return {'type': 'message', 'group_id': 'main', 'session_id': owner.meta.session_id, 'run_id': 'continuation',
        'extension_context_token': token['token'], 'extension_context_generation': token['generation']}


@pytest.mark.parametrize('attack', ['valid', 'forged', 'foreign-connection', 'foreign-group', 'foreign-session',
    'source', 'withdraw', 'delivery-withdraw', 'generation', 'boolean-generation', 'content', 'images',
    'image_refs', 'origin', 'delivery', 'busy'])
async def test_canonical_grants_are_owned_one_use_and_cannot_carry_input(tmp_path, monkeypatch, attack):
    async with delivery_server(tmp_path) as (server, runtime, owner, config, calls):
        routed = []
        original = server._orchestrator.route_message
        async def route(message, **kwargs):
            routed.append(message)
            return await original(message, **kwargs)
        monkeypatch.setattr(server._orchestrator, 'route_message', route)
        async with peer(config) as (send, receive, _writer):
            await negotiate(send, receive)
            request = await grant(send, receive, owner)
            original_request = dict(request)
            assert not calls and not routed
            reserved = False
            if attack in {'forged', 'foreign-connection'}:
                if attack == 'forged':
                    await send({**request, 'extension_context_token': 'forged-token'})
                    assert (await receive('error'))['run_id'] == 'continuation'
                else:
                    async with peer(config) as (other_send, other_receive, _other):
                        await negotiate(other_send, other_receive)
                        await other_send(request)
                        assert (await other_receive('error'))['run_id'] == 'continuation'
                assert not calls and not routed
            elif attack == 'foreign-group':
                request['group_id'] = 'foreign'
            elif attack == 'foreign-session':
                request['session_id'] = 'foreign-session'
            elif attack == 'source':
                runtime._source_revision += 1
            elif attack in {'withdraw', 'delivery-withdraw'}:
                key = 'extension_context_messages' if attack == 'withdraw' else 'extension_context_delivery_modes'
                await handshake(send, receive, **{key: 0})
                await handshake(send, receive, **{key: 1})
            elif attack == 'generation':
                request['extension_context_generation'] += 1
            elif attack == 'boolean-generation':
                request['extension_context_generation'] = True
            elif attack in {'images', 'image_refs'}:
                request[attack] = []
            elif attack in {'content', 'origin', 'delivery'}:
                request[attack] = 'UNREVIEWED-OVERRIDE'
            elif attack == 'busy':
                reserved = server._orchestrator.try_reserve_group('main')
                assert reserved
            allowed = attack in {'valid', 'forged', 'foreign-connection'}
            try:
                await send(request)
                outcome = await receive('response' if allowed else 'busy' if reserved else 'error')
                if allowed:
                    assert outcome['content'] == 'GRANT-DONE'
                assert len(calls) == len(routed) == int(allowed)
                if allowed:
                    assert type(routed[0]) is ContextContinuation and routed[0].group_id == 'main'
                    assert 'CANONICAL-CUSTOM-DELIVERY' in str(calls[0].messages)
                    assert 'NEVER-MODEL-METADATA' not in str(calls[0].messages)
                await send({**original_request, 'run_id': 'replay'})
                assert (await receive('error'))['run_id'] == 'replay'
                assert len(calls) == len(routed) == int(allowed)
                assert not any(isinstance(item, UserMessage) for item in owner.agent.messages)
                disk = runtime.transcript_store.load_transcript(owner.meta.session_id)
                assert sum(row.get('role') == 'custom' for row in disk) == 1
                assert not any(row.get('role') == 'user' for row in disk)
                assert server._orchestrator._runtimes['foreign']._active_session is None
            finally:
                if reserved:
                    server._orchestrator.release_group_reservation('main')


@pytest.mark.parametrize('stage', ['command', 'commit'])
@pytest.mark.parametrize('transport_fail', [False, True])
async def test_grant_waits_for_complete_command_and_commit_receipt_drain(tmp_path, monkeypatch, stage, transport_fail):
    async with delivery_server(tmp_path) as (_, _, owner, config, calls):
        held, release = asyncio.Event(), asyncio.Event()
        marked = set()
        published, jobs = [], []
        write, drain = asyncio.StreamWriter.write, asyncio.StreamWriter.drain
        target = 'extension_result' if stage == 'command' else 'extension_context_delivery_result'
        def intercepted_write(writer, data):
            payload = json.loads(data)
            published.append(payload.get('type'))
            if payload.get('type') == target and payload.get('success') is True:
                marked.add(writer)
            return write(writer, data)
        async def intercepted_drain(writer):
            if writer in marked:
                marked.remove(writer)
                jobs.append(asyncio.current_task())
                held.set()
                await release.wait()
                if transport_fail:
                    raise ConnectionResetError('Injected canonical receipt drain failure')
            return await drain(writer)
        monkeypatch.setattr(asyncio.StreamWriter, 'write', intercepted_write)
        monkeypatch.setattr(asyncio.StreamWriter, 'drain', intercepted_drain)
        async with peer(config) as (send, receive, _writer):
            await negotiate(send, receive)
            try:
                result = await review(send, receive, owner.meta.session_id)
                assert result['success'] and result['context_pending']
                if stage == 'commit':
                    assert (await receive('extension_context_delivery_result'))['success']
                await asyncio.wait_for(held.wait(), 5)
                assert not calls
                count = sum(isinstance(item, CustomAgentMessage) for item in owner.agent.messages)
                assert count == int(stage == 'commit')
                assert 'extension_context_continuation' not in published
                release.set()
                if not transport_fail:
                    if stage == 'command':
                        assert (await receive('extension_context_delivery_result'))['success']
                    token = await receive('extension_context_continuation')
                    await send({'type': 'message', 'group_id': 'main', 'session_id': owner.meta.session_id,
                        'run_id': 'settled', 'extension_context_token': token['token'],
                        'extension_context_generation': token['generation']})
                    assert (await receive('response'))['content'] == 'GRANT-DONE'
                    assert len(calls) == 1
                    assert (published.index('extension_result')
                            < published.index('extension_context_delivery_result')
                            < published.index('extension_context_continuation'))
                else:
                    await asyncio.wait_for(asyncio.gather(*jobs, return_exceptions=True), 5)
                    await handshake(send, receive)
                    assert 'extension_context_continuation' not in published
                    assert not calls
                    count = sum(isinstance(item, CustomAgentMessage) for item in owner.agent.messages)
                    assert count == int(stage == 'commit')
            finally:
                release.set()


@pytest.mark.parametrize('ending', ['denied', 'callback-failed', 'default-off', 'legacy'])
async def test_no_grant_without_review_success_and_both_capabilities(tmp_path, monkeypatch, ending):
    async with delivery_server(tmp_path) as (_, _, owner, config, calls):
        published = []
        write = asyncio.StreamWriter.write
        def observed_write(writer, data):
            published.append(json.loads(data))
            return write(writer, data)
        monkeypatch.setattr(asyncio.StreamWriter, 'write', observed_write)
        async with peer(config) as (send, receive, _writer):
            if ending in {'denied', 'callback-failed'}:
                await negotiate(send, receive)
                result = await review(send, receive, owner.meta.session_id,
                    fail=ending == 'callback-failed', approve=ending != 'denied')
                assert result['success'] is (ending == 'denied')
            else:
                caps = {'extension_context_messages': 1} if ending == 'legacy' else {
                    'extension_context_delivery_modes': 1}
                await handshake(send, receive, **caps)
                await send({'type': 'extension_control', 'request_id': 'command', 'group_id': 'main',
                    'session_id': owner.meta.session_id, 'action': 'command', 'name': 'ext-customdelivery-request',
                    'args': json.dumps({'delivery': 'steer', 'trigger': True})})
                result = await receive('extension_result')
                assert result['success'] and 'false' in result['output']
                assert not any(event.get('type') == 'extension_dialog' for event in published)
            assert not result['context_pending']
            await handshake(send, receive)
            assert not any(event.get('type') in {'extension_context_continuation', 'extension_context_delivery_result'}
                           for event in published)
            assert not calls and not owner.agent.messages
            assert not owner.extension_dialogs.has_pending
