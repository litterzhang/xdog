"""Socket message grants are one-shot, owned, result-gated and default-off."""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

import pytest
from xdog.agent.extensions.message_request import host_message
from xdog.ai.types import AssistantMessage, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream

from packages.claw.tests.test_extension_message_ui import message_server


@asynccontextmanager
async def peer(config):
    reader, writer = await asyncio.open_unix_connection(config.socket_path)
    async def send(value):
        writer.write((json.dumps(value) + '\n').encode())
        await writer.drain()
    async def receive(kind):
        async with asyncio.timeout(10):
            while True:
                line = await reader.readline()
                assert line, 'Connection ended without an owned result'
                value = json.loads(line)
                if value['type'] == kind:
                    return value
    try:
        yield send, receive, writer
    finally:
        writer.close()
        await writer.wait_closed()


async def handshake(send, receive, **capabilities):
    await send({'type': 'ping', 'group_id': 'main', 'extension_session': 1, **capabilities})
    return await receive('pong')


async def request_review(send, receive, session, *, identifier='command', args=None):
    await send({'type': 'extension_control', 'request_id': identifier, 'group_id': 'main',
                'session_id': session, 'action': 'command', 'name': 'ext-messagerequest-request',
                'args': json.dumps(args or {})})
    return await receive('extension_dialog')


async def accepted_grant(send, receive, session, *, identifier='command'):
    dialog = await request_review(send, receive, session, identifier=identifier)
    assert dialog['dialog']['host_message'] == host_message('messagerequest', '/reset literal requested text')
    await send({'type': 'extension_dialog_answer', 'id': dialog['dialog']['id'], 'value': True})
    accepted = await receive('extension_dialog_accepted')
    assert accepted['id'] == dialog['dialog']['id'] and accepted['operation'] == 'message'
    result = await receive('extension_result')
    assert result['success'] is True and result['message_queued'] is True
    grant = result['message_delivery']
    return {'type': 'message', 'group_id': 'main', 'session_id': session, 'run_id': 'requested',
            'extension_message_token': grant['token'], 'extension_message_generation': grant['generation']}


@pytest.mark.parametrize('version', [None, False, True, '1', 2, 0])
async def test_message_capability_is_independently_default_off(tmp_path, version):
    async with message_server(tmp_path) as (_, _, owner, config, calls):
        async with peer(config) as (send, receive, _writer):
            caps = await handshake(send, receive, extension_compaction=1, session_compaction=1,
                                   **({'extension_message': version} if version is not None else {}))
            assert caps['extension_message'] == 0 and caps['extension_compaction'] == 1
            await send({'type': 'extension_control', 'request_id': 'off', 'group_id': 'main',
                        'session_id': owner.meta.session_id, 'action': 'command',
                        'name': 'ext-messagerequest-request'})
            result = await receive('extension_result')
            assert result['success'] and not result['message_queued'] and 'message_delivery' not in result
            assert '"queued": false' in result['output'] and not calls
            caps = await handshake(send, receive, extension_message=1)
            assert caps['extension_message'] == caps['extension_compaction'] == 1
            unchanged = await handshake(send, receive, extension_navigation=0)
            assert unchanged['extension_message_generation'] == caps['extension_message_generation']
            assert unchanged['extension_message'] == 1


@pytest.mark.parametrize('ending', ['apply', 'withdraw', 'session', 'source', 'prior-turn', 'unload',
    'foreign', 'images', 'content', 'origin', 'generation', 'generation-bool', 'expired', 'busy'])
async def test_canonical_token_dispatch_revalidates_owner_and_is_never_replayed(tmp_path, monkeypatch, ending):
    async with message_server(tmp_path) as (server, runtime, owner, config, calls):
        async with peer(config) as (send, receive, _writer):
            await handshake(send, receive, extension_message=1)
            request = await accepted_grant(send, receive, owner.meta.session_id)
            assert not calls
            reserved = False
            if ending == 'withdraw':
                await handshake(send, receive, extension_message=0)
                await handshake(send, receive, extension_message=1)
            elif ending == 'session':
                request['session_id'] = 'foreign-session'
            elif ending == 'source':
                runtime._source_revision += 1
            elif ending == 'prior-turn':
                owner._source_generation += 1
            elif ending == 'unload':
                await owner.extensions.manage('unload', 'messagerequest')
            elif ending == 'images':
                request['image_refs'] = []
            elif ending == 'content':
                request['content'] = 'UNREVIEWED OVERRIDE'
            elif ending == 'origin':
                request['origin'] = 'user'
            elif ending == 'generation':
                request['extension_message_generation'] += 1
            elif ending == 'generation-bool':
                request['extension_message_generation'] = True
            elif ending == 'expired':
                import time
                from types import SimpleNamespace
                monkeypatch.setattr('xdog.claw.core.runtime.gateway.time', SimpleNamespace(
                    monotonic=lambda: time.monotonic() + 301, time_ns=time.time_ns, time=time.time))
            elif ending == 'busy':
                reserved = server._orchestrator.try_reserve_group('main')
                assert reserved
            elif ending == 'foreign':
                async with peer(config) as (other_send, other_receive, _other):
                    await handshake(other_send, other_receive, extension_message=1)
                    await other_send(request)
                    assert (await other_receive('error'))['run_id'] == 'requested'
                    assert not calls
                # A foreign client cannot steal/consume the original connection's grant.
            try:
                await send(request)
                if ending in {'apply', 'foreign'}:
                    await receive('response')
                    assert len(calls) == 1
                    assert 'requested by extension messagerequest' in str(calls[0].messages)
                else:
                    await receive('busy' if ending == 'busy' else 'error')
                    assert not calls
                await send({**request, 'run_id': 'replay'})
                assert (await receive('error'))['run_id'] == 'replay'
                assert len(calls) == (1 if ending in {'apply', 'foreign'} else 0)
            finally:
                if reserved:
                    server._orchestrator.release_group_reservation('main')


@pytest.mark.parametrize('failure', ['drain', 'cancel', 'withdraw'])
async def test_complete_result_transport_must_settle_before_message_dispatch(tmp_path, monkeypatch, failure):
    async with message_server(tmp_path) as (_, _, owner, config, calls):
        held, release, settled = asyncio.Event(), asyncio.Event(), asyncio.Event()
        result_writers = set()
        original_write, original_drain = asyncio.StreamWriter.write, asyncio.StreamWriter.drain
        def write(writer, data):
            if b'"message_delivery"' in data and b'"extension_result"' in data:
                result_writers.add(writer)
            return original_write(writer, data)
        async def drain(writer):
            if writer in result_writers:
                result_writers.discard(writer)
                held.set()
                try:
                    await release.wait()
                finally:
                    settled.set()
                if failure == 'drain':
                    raise ConnectionResetError('Injected result drain failure after bytes reached reader')
            return await original_drain(writer)
        monkeypatch.setattr(asyncio.StreamWriter, 'write', write)
        monkeypatch.setattr(asyncio.StreamWriter, 'drain', drain)
        async with peer(config) as (send, receive, _writer):
            await handshake(send, receive, extension_message=1)
            request = await accepted_grant(send, receive, owner.meta.session_id)
            await asyncio.wait_for(held.wait(), 5)
            assert not calls
            if failure == 'cancel':
                await send({'type': 'extension_control_cancel', 'request_id': 'command', 'group_id': 'main',
                            'expected_session': owner.meta.session_id})
                await asyncio.wait_for(settled.wait(), 5)
            elif failure == 'withdraw':
                await send({'type': 'ping', 'extension_message': 0})
            # The reader remains responsive while complete-result drain is pending.
            await send(request)
            release.set()
            assert (await receive('error'))['run_id'] == 'requested'
            assert not calls


async def test_headless_and_real_tool_callbacks_cannot_borrow_command_message_owner(tmp_path):
    calls = []
    def stream(model, context, options):
        calls.append(context)
        if len(calls) == 1:
            tool = next(tool for tool in context.tools if tool.name.startswith('ext_messagerequest_'))
            return EventStream.empty(AssistantMessage(content=(ToolCall(id='tool', name=tool.name, arguments={}),),
                                                       stop_reason='toolUse'))
        return EventStream.empty(AssistantMessage(content=(TextContent(text='TOOL-DONE'),)))
    async with message_server(tmp_path, stream, allow_tools=True) as (server, _, owner, config, _calls):
        result = await server._orchestrator.manage_extensions('main', 'command', 'ext-messagerequest-request', '',
            owner.meta.session_id, message_alive=lambda: True, message_ready=lambda *_: pytest.fail('headless grant'))
        assert not result['message_queued']
        assert '"queued": false' in result['output']
        async with peer(config) as (send, receive, _writer):
            await handshake(send, receive, extension_message=1)
            await send({'type': 'message', 'group_id': 'main', 'session_id': owner.meta.session_id,
                        'run_id': 'ordinary', 'content': 'Call permitted extension tool'})
            await receive('response')
            assert len(calls) == 2 and 'ILLEGAL-MESSAGE-False' in str(owner.agent.messages)
            assert not owner.extension_dialogs.has_pending


@pytest.mark.parametrize('requested', [False, True])
async def test_withdrawal_does_not_cancel_admitted_or_unrelated_turn(tmp_path, requested):
    from xdog.ai.types import DoneEvent, StartEvent
    entered, release, stopped = asyncio.Event(), asyncio.Event(), asyncio.Event()
    def stream(model, context, options):
        final = AssistantMessage(content=(TextContent(text='DONE'),))
        async def generate():
            try:
                entered.set()
                yield StartEvent(partial=AssistantMessage())
                await release.wait()
                yield DoneEvent(message=final)
            finally:
                stopped.set()
        future = asyncio.get_running_loop().create_future()
        future.set_result(final)
        return EventStream.from_async_generator(generate(), result_future=future)
    async with message_server(tmp_path, stream) as (_, _, owner, config, _calls):
        async with peer(config) as (send, receive, _writer):
            await handshake(send, receive, extension_message=1)
            if requested:
                message = await accepted_grant(send, receive, owner.meta.session_id)
            else:
                message = {'type': 'message', 'group_id': 'main', 'session_id': owner.meta.session_id,
                           'run_id': 'manual', 'content': 'unrelated normal turn'}
            try:
                await send(message)
                await receive('run_ack')
                await asyncio.wait_for(entered.wait(), 5)
                await handshake(send, receive, extension_message=0)
                await handshake(send, receive, extension_message=1)
                assert not stopped.is_set() and owner.agent.is_streaming
                release.set()
                await receive('response')
                assert stopped.is_set()
            finally:
                release.set()
