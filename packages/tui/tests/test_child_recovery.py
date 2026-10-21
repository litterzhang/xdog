"""Real sender/receiver protocol with owned spools; no model network or user files."""
from __future__ import annotations

import base64
import copy

import pytest
from xdog.agent.child_transfer import prepare_recovery, send_recovery
from xdog.agent.input_queue import QueuedInput
from xdog.ai.types import ImageContent
from xdog.tui.child_recovery import ChildRecoveryReceiver, decode_recovery

PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5ioAAAAASUVORK5CYII='
SCOPE = {'request_id': 'copy', 'session_id': 'session', 'child_id': '0123456789ab',
         'input_id': 'input', 'queue_revision': 'a' * 64}


async def frames(size=68):
    body = base64.b64decode(PNG) + b'\0' * (size - 68)
    row = QueuedInput('input', 'literal /quit image copy', 'follow_up',
                      images=(ImageContent(data=base64.b64encode(body).decode(), mime_type='image/png'),))
    payload = await prepare_recovery(row)
    output = []
    async def write(event):
        output.append(copy.deepcopy(event))
    await send_recovery(payload, SCOPE, write, lambda: True)
    return payload, output, body


@pytest.mark.parametrize('size', [68, 10 * 1024 * 1024])
async def test_complete_scoped_copy_checksums_and_private_spools_are_settled(size):
    payload, output, body = await frames(size)
    received = []
    receiver = ChildRecoveryReceiver(received.append)
    receiver.expect(SCOPE)
    try:
        for event in output:
            receiver.feed(event)
        assert received[-1]['type'] == 'child_recovery_ready'
        assert received[-1]['images'][0].data == body
        assert received[-1]['receipt'] == payload['receipt']
        assert decode_recovery(payload)[0].data == body
        assert not receiver.pending
        receiver.feed(output[-1])
        assert len(received) == 1  # A repeated terminal frame does not manufacture a fresh copy.
    finally:
        receiver.close()


@pytest.mark.parametrize('fault', ['scope', 'order', 'checksum', 'mime', 'size', 'end-first', 'duplicate-begin'])
async def test_invalid_image_copy_never_publishes_a_draft_and_closes_its_spools(fault):
    _, output, _ = await frames()
    received = []
    receiver = ChildRecoveryReceiver(received.append)
    receiver.expect(SCOPE)
    if fault == 'scope':
        output[1]['session_id'] = 'other'
    elif fault == 'order':
        output[1]['index'] = 1
    elif fault == 'checksum':
        output[1]['data'] = base64.b64encode(b'wrong').decode()
    elif fault == 'mime':
        output[0]['images'][0]['mime_type'] = 'image/jpeg'
        output[0]['receipt']['image_mime_types'] = ['image/jpeg']
    elif fault == 'size':
        output[0]['images'][0]['bytes'] = 0
    elif fault == 'end-first':
        output = [output[-1], *output]
    else:
        output.insert(1, copy.deepcopy(output[0]))
    try:
        for event in output:
            receiver.feed(event)
        assert [event['type'] for event in received] == ['child_recovery_failed']
        assert not receiver.pending
    finally:
        receiver.close()


async def test_owner_revocation_midstream_and_receiver_cancel_do_not_complete_a_copy():
    payload, _, _ = await frames(128 * 1024)
    received = []
    receiver = ChildRecoveryReceiver(received.append)
    receiver.expect(SCOPE)
    sent = []
    async def write(event):
        sent.append(event)
        receiver.feed(event)
    try:
        with pytest.raises(ValueError, match='owner/queue changed'):
            await send_recovery(payload, SCOPE, write, lambda: len(sent) < 2)
        assert not received
        spools = list(receiver.pending['copy'].files)
        receiver.cancel('copy')
        assert all(file.closed for file in spools)
    finally:
        receiver.close()
