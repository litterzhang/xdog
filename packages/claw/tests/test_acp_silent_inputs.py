"""ACP is an origin, not an implicit user reply destination."""
from __future__ import annotations

import asyncio

import pytest
from acp import RequestError
from xdog.agent.acp.contracts import Peer
from xdog.agent.session_input import SessionInput
from xdog.ai.types import AssistantMessage, TextContent
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.core.inputs.models import InputScope, ReplyTarget, decode_envelope, encode_envelope
from xdog.claw.core.runtime.input_adapter import freeze_input
from xdog.claw.core.types import ExternalInput

from packages.claw.tests.test_acp_backend import enqueue_params, make_backend


def test_external_channel_label_never_infers_reply_target(tmp_path):
    value = ExternalInput(group_id='main', sender='fake-user', channel='weixin', content='',
        metadata={'input_origin': 'acp'},
        canonical=SessionInput('one', 'acp', 'human', 'Work only', (), {'reply_target': 'weixin'}))
    saved = freeze_input(value, tmp_path, message_id='one')
    restored = decode_envelope(encode_envelope(saved))
    assert restored.reply is None and restored.message.origin == 'acp' and not restored.human_authorized
    explicit = freeze_input(value, tmp_path, message_id='two', reply=ReplyTarget('weixin', 'original-user'))
    assert decode_envelope(encode_envelope(explicit)).reply == ReplyTarget('weixin', 'original-user')


async def test_silent_prompt_receipt_is_queryable_and_duplicate_ack_does_not_execute_again(tmp_path):
    app, backend, model = await make_backend(tmp_path)
    peer = Peer('original-peer', None)
    updates = []

    async def emit(update):
        updates.append(update)

    try:
        ref = await backend.load_session('group:main', tmp_path, peer, emit)
        result = await backend.prompt(ref, peer, 'Inspect persisted output', {}, emit)
        identifier = result.field_meta['xdog']['message_id']
        status = await backend.extension(peer, 'xdog/inbox_status', {
            'session_id': ref.id, 'cwd': str(tmp_path), 'message_id': identifier})
        row, = status['messages']
        assert row['status'] == 'succeeded' and row['response'] == 'answer 1'
        assert row['response_next_offset'] is None and updates == []
        record = await backend.inputs.store.get(InputScope('main', tmp_path), identifier)
        assert record.envelope.context['metadata']['input_origin'] == 'acp'
        assert record.envelope.context['channel'] == '' and record.envelope.reply is None
        assert await (await app.output_service()).store.records() == []
        # Querying is read-only, and a retransmitted enqueue ACK keeps its original ID.
        params = enqueue_params(tmp_path, 'enqueue-id')
        await backend.extension(peer, 'xdog/enqueue', params)
        record = await asyncio.wait_for(backend.inputs.wait(InputScope('main', tmp_path), 'enqueue-id'), 5)
        assert record.status == 'succeeded'
        ack = await backend.extension(peer, 'xdog/enqueue', params)
        assert ack['status'] == 'duplicate' and len(model.inputs) == 2
    finally:
        await backend.close()
        await app.stop()


async def test_selected_result_query_is_bounded_and_keeps_list_pages_small(tmp_path):
    answer = '\U0001f436"\\\n' * 20_000
    app, backend, _ = await make_backend(tmp_path, lambda *args: EventStream.empty(
        AssistantMessage(content=(TextContent(text=answer),))))
    peer = Peer('reader', None)
    params = enqueue_params(tmp_path)
    selection = {'session_id': 'group:main', 'cwd': str(tmp_path), 'message_id': params['message_id']}
    try:
        await backend.extension(peer, 'xdog/enqueue', params)
        await asyncio.wait_for(backend.inputs.wait(InputScope('main', tmp_path), params['message_id']), 5)
        offset, pieces, attempts = 0, [], set()
        while True:
            page = await backend.extension(peer, 'xdog/inbox_status', {**selection, 'response_offset': offset})
            row, = page['messages']
            pieces.append(row['response'])
            attempts.add(row['attempt_id'])
            assert len(row['response']) <= 16_384 and row['response_offset'] == offset
            offset = row['response_next_offset']
            if offset is None:
                break
        assert ''.join(pieces) == answer and len(attempts) == 1
        listing = await backend.extension(peer, 'xdog/inbox_status', {
            'session_id': 'group:main', 'cwd': str(tmp_path)})
        assert 'response' not in listing['messages'][0]
        for extra in ({'response_offset': True}, {'response_offset': -1}, {'response_offset': 1024 * 1024 + 1},
                      {'cursor': 1}):
            with pytest.raises(RequestError):
                await backend.extension(peer, 'xdog/inbox_status', {**selection, **extra})
        with pytest.raises(RequestError):
            await backend.extension(peer, 'xdog/inbox_status', {
                'session_id': 'group:main', 'cwd': str(tmp_path), 'response_offset': 0})
    finally:
        await backend.close()
        await app.stop()


async def test_prompt_execution_error_is_still_a_protocol_error_and_durable_receipt(tmp_path):
    def failure(*args):
        raise RuntimeError('scripted execution failure')

    app, backend, _ = await make_backend(tmp_path, failure)
    peer = Peer('sender', None)
    try:
        ref = await backend.load_session('group:main', tmp_path, peer, None)
        with pytest.raises(RequestError):
            await backend.prompt(ref, peer, 'Fail explicitly', {}, None)
        record, = await backend.inputs.store.records(InputScope('main', tmp_path))
        assert record.status == 'failed' and record.error and record.envelope.reply is None
        status = await backend.extension(peer, 'xdog/inbox_status', {
            'session_id': ref.id, 'cwd': str(tmp_path), 'message_id': record.envelope.message.id})
        assert status['messages'][0]['error'] and status['messages'][0]['status'] == 'failed'
        assert await (await app.output_service()).store.records() == []
    finally:
        await backend.close()
        await app.stop()
