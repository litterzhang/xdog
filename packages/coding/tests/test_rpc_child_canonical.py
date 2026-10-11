"""Child binary sources require their own negotiated capability and actual owner/epoch."""
from __future__ import annotations

import asyncio
import base64
import json
import os
import selectors
import threading
import time
from dataclasses import replace

import pytest
from xdog.ai.types import AssistantMessage, ImageContent, TextContent, ThinkingContent, UserMessage
from xdog.tui.source_receiver import SourceReceiver

from packages.coding.tests.test_rpc_canonical import decode
from packages.coding.tests.test_rpc_children import close, control, packet, setup, wait


async def start_child(children, events):
    await control(children, 'start', task='hold child transcript', access='read-only')
    started = next(event for event in events if event['type'] == 'child_result')
    assert started['success']
    child = started['child_id']
    await wait(lambda: bool(children.session.children.supervisor.capture_source(child).messages))
    return child


@pytest.mark.parametrize('fault', ['none', 'display-withdraw', 'parent-withdraw', 'foreign-epoch'])
async def test_actual_child_capture_canonical_image_partial_and_independent_format_gate(tmp_path, monkeypatch, fault):
    session, adapter, nav, sources, children, events = setup(tmp_path)
    try:
        child = await start_child(children, events)
        captured = session.children.supervisor.capture_source(child)
        binary = base64.b64encode(bytes(range(256)) * 8192).decode()
        captured = replace(captured, messages=(UserMessage(content=(TextContent(text='token=PRIVATE-CREDENTIAL'),
            ImageContent(data=binary, mime_type='image/png'))),), partial=AssistantMessage(content=(
                TextContent(text='ACTUAL-PARTIAL'), ThinkingContent(thinking='PUBLIC',
                    thinking_signature='PRIVATE-SIGNATURE'))))
        original = session.children.supervisor.capture_source
        monkeypatch.setattr(session.children.supervisor, 'capture_source',
                            lambda identifier: captured if identifier == child else original(identifier))
        with pytest.raises(ValueError):
            children.source(packet(children, 'without', child_id=child, format='canonical'))
        sources.negotiate({'transcript_canonical': 1})  # Parent authorization is not child authorization.
        with pytest.raises(ValueError):
            children.source(packet(children, 'parent-only', child_id=child, format='canonical'))
        children.negotiate({'child_canonical': 1})
        if fault == 'display-withdraw':
            children.negotiate({'child_sources': 0})
        if fault == 'parent-withdraw':
            sources.negotiate({'transcript_canonical': 0})
        if fault == 'foreign-epoch':
            with pytest.raises(ValueError):
                children.source(packet(children, 'foreign', child_id=child, format='canonical', child_epoch='foreign'))
        children.source(packet(children, 'read', child_id=child, format='canonical'))
        await sources.task
        rows = decode(events)
        serialized = json.dumps(rows)
        assert binary in serialized and 'ACTUAL-PARTIAL' in serialized
        assert 'PRIVATE-SIGNATURE' not in serialized and 'PRIVATE-CREDENTIAL' not in serialized
        assert rows[1]['id'] == child and rows[1]['child']['access'] == 'read-only'
        assert len(adapter.contexts) == 1 and session.messages == [] and session.permissions.mode == 'deny'
    finally:
        await close(session, nav, sources, children)


@pytest.mark.parametrize('change', ['canonical-withdraw', 'controls-withdraw', 'cancel', 'revision', 'close-child',
                                    'parent-withdraw', 'display-withdraw'])
async def test_held_child_canonical_spool_revocation_settles_without_end_or_fallback(tmp_path, monkeypatch, change):
    import xdog.coding.modes.rpc.transcripts as module
    session, _, nav, sources, children, events = setup(tmp_path)
    entered, release, spools = threading.Event(), threading.Event(), []
    real = module.prepare
    def held(request, canceled):
        spool = real(request, canceled)
        spools.append(spool)
        entered.set()
        assert release.wait(5)
        return spool
    try:
        child = await start_child(children, events)
        monkeypatch.setattr(module, 'prepare', held)
        children.negotiate({'child_canonical': 1})
        children.source(packet(children, 'read', child_id=child, format='canonical'))
        assert await asyncio.to_thread(entered.wait, 2)
        if change == 'canonical-withdraw':
            children.negotiate({'child_canonical': 0})
        elif change == 'controls-withdraw':
            children.negotiate({'child_controls': 0})
        elif change == 'cancel':
            for field in ('child_id', 'child_owner', 'child_epoch'):
                with pytest.raises(ValueError):
                    sources.cancel({**packet(children, 'read', child_id=child), field: 'foreign'})
            sources.cancel(packet(children, 'read', child_id=child))
        elif change == 'revision':
            nav.refresh_revision()
        elif change == 'parent-withdraw':
            sources.negotiate({'transcript_canonical': 0})
        elif change == 'display-withdraw':
            children.negotiate({'child_sources': 0})
        else:
            await control(children, 'cancel', 'cancel-work', child_id=child)
            await control(children, 'close', 'close-work', child_id=child)
        release.set()
        await sources.task
        if change in {'parent-withdraw', 'display-withdraw'}:
            assert decode(events)[1]['id'] == child
        else:
            assert not any(event['type'] in {'source_begin', 'source_end'} for event in events)
        assert spools and all(spool.file.closed for spool in spools)
    finally:
        release.set()
        await close(session, nav, sources, children)


@pytest.mark.parametrize('fault', ['child_id', 'child_owner', 'child_epoch', 'format'])
async def test_explicit_child_receiver_refuses_mixed_canonical_frame(tmp_path, fault):
    session, _, nav, sources, children, events = setup(tmp_path)
    results = []
    receiver = SourceReceiver(results.append, decode=lambda file: [json.loads(line) for line in file])
    try:
        child = await start_child(children, events)
        children.negotiate({'child_canonical': 1})
        receiver.expect('read', '', session.session_id, target_session_id=session.session_id,
                        session_revision=nav.revision, child_id=child, child_owner=children.lease,
                        child_epoch=session.children.token, format='canonical')
        children.source(packet(children, 'read', child_id=child, format='canonical'))
        await sources.task
        for event in events:
            if event['type'].startswith('source_'):
                event = dict(event)
                if event['type'] == 'source_chunk':
                    event[fault] = 'foreign'
                receiver.feed(event)
        assert results[-1]['type'] == 'source_failed' and receiver._file is None
    finally:
        receiver.close()
        await close(session, nav, sources, children)


def test_actual_cli_child_canonical_image_upload_and_export_never_become_parent_history(tmp_path, monkeypatch):
    import hashlib
    import time

    from packages.coding.tests.test_rpc_children_cli import Peer
    monkeypatch.setenv('XDOG_RPC_STREAM_PARTIAL', '1')
    peer = Peer(tmp_path, vision=True)
    png = base64.b64decode(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5ioAAAAASUVORK5CYII=')
    body = png + bytes(2 * 1024 * 1024)
    try:
        peer.send({'type': 'rpc_hello', 'child_images': 1, 'child_canonical': 1, 'child_sources': 0})
        peer.caps = peer.read('child_capabilities')
        assert peer.caps['child_canonical'] == 1 and peer.caps['child_sources'] == 0
        for index, offset in enumerate(range(0, len(body), 65536)):
            final = offset + 65536 >= len(body)
            peer.send({'type': 'image_chunk', 'session_id': peer.caps['session_id'], 'id': 'child-image',
                'index': index, 'mime_type': 'image/png',
                'data': base64.b64encode(body[offset:offset + 65536]).decode(),
                'final': final, **({'sha256': hashlib.sha256(body).hexdigest()} if final else {})})
            assert peer.read('image_upload_result')['status'] in {'partial', 'ready'}
        peer.send(peer.packet('start', action='start', task='hold token=PRIVATE-CREDENTIAL',
                              image_refs=['child-image']))
        started = peer.read('child_result')
        assert started['success']
        deadline = time.monotonic() + 5
        while not peer.marker.with_suffix('.partial').exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert peer.marker.with_suffix('.partial').exists()
        peer.send(peer.packet('read', type='child_source_read', child_id=started['child_id'], format='canonical'))
        peer.read('source_end')
        rows = decode(peer.events)
        serialized = json.dumps(rows)
        assert base64.b64encode(body).decode() in serialized and 'PRIVATE-CREDENTIAL' not in serialized
        assert rows[1]['id'] == started['child_id'] and rows[-1]['kind'] == 'partial'
        peer.send({'type': 'status'})
        status = peer.read('status')
        assert status['messages'] == 0 and status['permission_mode'] == 'deny'
        peer.send({'type': 'quit'})
        assert peer.process.wait(timeout=5) == 0
        assert peer.marker.with_suffix('.settled').exists()
    finally:
        peer.close()


@pytest.mark.parametrize('value', [True, False, '1', None, 2])
async def test_child_binary_capability_rejects_noninteger_values_without_grant(tmp_path, value):
    session, _, nav, sources, children, events = setup(tmp_path)
    try:
        with pytest.raises(ValueError):
            children.negotiate({'child_canonical': value})
        assert not children.flags['child_canonical']
        assert not session.messages
    finally:
        await close(session, nav, sources, children)


@pytest.mark.parametrize('stop', ['quit', 'eof', 'closed-output'])
def test_actual_cli_child_binary_backpressure_does_not_block_reader_or_owned_child_cleanup(tmp_path, monkeypatch, stop):
    import hashlib

    from packages.coding.tests.test_rpc_transcript_cli import Peer
    monkeypatch.setenv('XDOG_RPC_VISION', '1')
    peer = Peer(tmp_path)
    png = base64.b64decode(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5ioAAAAASUVORK5CYII=')
    body = png + bytes(2 * 1024 * 1024)
    def read(kind, **expected):
        return _read_owned_receipt(peer.process.stdout, kind, time.monotonic() + 6, **expected)
    try:
        # Use the same bounded byte reader from startup: TextIO.initial() can
        # prefetch later receipts and hide them from the selector's fd readiness.
        ready = read('ready')
        assert ready['capabilities']['transcript_sources'] == 1
        peer.send({'type': 'rpc_hello', 'transcript_sources': 1})
        assert read('transcript_capabilities')['transcript_sources'] == 1
        peer.send({'type': 'rpc_hello', 'child_controls': 1, 'child_images': 1, 'child_canonical': 1})
        # One shared deadline covers stale disabled replies AND missing receipts.
        caps = read('child_capabilities', child_canonical=1)
        for index, offset in enumerate(range(0, len(body), 65536)):
            final = offset + 65536 >= len(body)
            peer.send({'type': 'image_chunk', 'session_id': caps['session_id'], 'id': 'child-image',
                'index': index, 'mime_type': 'image/png',
                'data': base64.b64encode(body[offset:offset + 65536]).decode(), 'final': final,
                **({'sha256': hashlib.sha256(body).hexdigest()} if final else {})})
            assert read('image_upload_result')['status'] in {'partial', 'ready'}
        envelope = {name: caps[name] for name in (
            'session_id', 'session_revision', 'child_owner', 'child_epoch', 'child_return_owner')}
        peer.send({'type': 'child_control', 'request_id': 'start', **envelope,
                   'action': 'start', 'task': 'hold', 'image_refs': ['child-image']})
        child = read('child_result')
        assert child['success']
        deadline = time.monotonic() + 5
        while not peer.marker.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert peer.marker.exists()
        peer.send({'type': 'child_source_read', 'request_id': 'source', **envelope,
                   'child_id': child['child_id'], 'format': 'canonical'})
        read('source_begin')  # Authoritative admitted stream; now deliberately stop draining the pipe.
        time.sleep(0.1)
        assert peer.process.poll() is None
        if stop == 'quit':
            peer.send({'type': 'quit'})
        elif stop == 'eof':
            peer.process.stdin.close()
        else:
            peer.process.stdout.close()
        assert peer.process.wait(timeout=5) == (1 if stop == 'closed-output' else 0), peer.process.stderr.read()
        assert peer.marker.with_suffix('.settled').exists()
    finally:
        peer.close()


def _read_owned_receipt(stream, kind, deadline, **expected):
    """Bound startup/negotiation reads without prefetching undrained source data."""
    line = bytearray()
    with selectors.DefaultSelector() as selector:
        selector.register(stream.fileno(), selectors.EVENT_READ)
        while True:
            remaining = deadline - time.monotonic()
            assert remaining > 0, f'No owned {kind} receipt before deadline'
            if not selector.select(remaining):
                continue
            byte = os.read(stream.fileno(), 1)
            assert byte, 'Owned RPC peer ended before receipt'
            line.extend(byte)
            assert len(line) <= 1024 * 1024, 'Owned RPC receipt exceeds bounded line size'
            if byte != b'\n':
                continue
            packet = json.loads(line)
            line.clear()
            if packet['type'] == kind and all(packet.get(key) == value for key, value in expected.items()):
                return packet


def test_owned_receipt_deadline_covers_stale_disabled_capability_and_missing_reply():
    # An open pipe with no negotiated receipt must fail, not wait for peer EOF.
    reader, writer = os.pipe()
    try:
        with os.fdopen(reader, 'rb') as stream:
            os.write(writer, b'{"type":"child_capabilities","child_canonical":0}\n')
            start = time.monotonic()
            with pytest.raises(AssertionError, match='before deadline'):
                _read_owned_receipt(stream, 'child_capabilities', start + 0.1, child_canonical=1)
            assert time.monotonic() - start < 2
    finally:
        os.close(writer)


def test_owned_receipt_skips_stale_capability_and_does_not_prefetch_source():
    reader, writer = os.pipe()
    try:
        with os.fdopen(reader, 'rb') as stream:
            os.write(writer, b'{"type":"child_capabilities","child_canonical":0}\n'
                     b'{"type":"child_capabilities","child_canonical":1}\n'
                     b'{"type":"source_begin"}\nSOURCE-BYTES')
            caps = _read_owned_receipt(stream, 'child_capabilities', time.monotonic() + 1, child_canonical=1)
            assert caps['child_canonical'] == 1
            assert _read_owned_receipt(stream, 'source_begin', time.monotonic() + 1)['type'] == 'source_begin'
            assert os.read(reader, len(b'SOURCE-BYTES')) == b'SOURCE-BYTES'
    finally:
        os.close(writer)
