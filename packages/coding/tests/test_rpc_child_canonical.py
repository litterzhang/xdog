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
