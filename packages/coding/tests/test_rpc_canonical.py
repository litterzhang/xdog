"""Explicit canonical RPC exports retain typed images, never grant replay authority."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import threading
from dataclasses import replace

import pytest
from xdog.agent.core import CustomAgentMessage
from xdog.ai.types import AssistantMessage, ImageContent, TextContent, ThinkingContent, ToolCall, UserMessage
from xdog.coding.core.canonical_source import project
from xdog.coding.core.messages import messages_to_dicts

from packages.coding.tests.test_rpc_transcripts import close, request, setup


def decode(events, identifier='read'):
    packets = [event for event in events if event.get('request_id') == identifier
               and event['type'].startswith('source_')]
    assert packets[0]['type'] == 'source_begin' and packets[-1]['type'] == 'source_end', packets
    chunks = packets[1:-1]
    assert all(packet['format'] == 'canonical' for packet in packets)
    assert [packet['index'] for packet in chunks] == list(range(len(chunks)))
    data = b''.join(base64.b64decode(packet['data'], validate=True) for packet in chunks)
    assert len(data) == packets[0]['bytes'] and hashlib.sha256(data).hexdigest() == packets[0]['sha256']
    assert packets[-1]['chunks'] == len(chunks)
    return [json.loads(line) for line in data.splitlines()]


@pytest.mark.parametrize('scope', ['live', 'saved', 'branch', 'timeline'])
async def test_typed_canonical_image_suffix_metadata_provenance_and_redaction(tmp_path, scope):
    owner, manager, calls, nav, sources, events = setup(tmp_path)
    binary = base64.b64encode(bytes(range(256)) * 8192).decode()
    history = [UserMessage(content=(TextContent(text='hello token=PRIVATE-CREDENTIAL'),
        ImageContent(data=binary, mime_type='image/png'))),
        AssistantMessage(content=(TextContent(text='ANSWER', text_signature='PRIVATE-SIGNATURE'),
            ThinkingContent(thinking='PUBLIC-REASONING', thinking_signature='PRIVATE-SIGNATURE'),
            ThinkingContent(thinking='PRIVATE-REDACTED', redacted=True),
            ToolCall(id='call', name='tool', arguments={'nested': {'api_key': 'PRIVATE-CREDENTIAL'}},
                     thought_signature='PRIVATE-SIGNATURE')), response_id='PRIVATE-RESPONSE'),
        CustomAgentMessage(type='entry', data={'signature': 'PRIVATE-SIGNATURE', 'safe': 'CUSTOM'})]
    target = manager.create_session(model='offline/model', working_dir=tmp_path)
    target.messages = history
    target.branches = [{'branch_id': 'archive', 'label': 'Archive', 'messages': messages_to_dicts(history)}]
    manager.save_session(target)
    if scope == 'live':
        owner.agent.replace_messages(history)
        owner.agent._state = replace(owner.agent.state,
            stream_message=AssistantMessage(content=(TextContent(text='PARTIAL'),)))
    old = owner.agent.state.messages
    try:
        sources.negotiate({'transcript_sources': 0, 'transcript_canonical': 1})
        sources.start(request(owner, nav, format='canonical', scope='saved' if scope == 'branch' else scope,
            **({} if scope == 'live' else {'target_session': target.session_id}),
            **({'branch_id': 'archive'} if scope == 'branch' else {})))
        await sources.task
        rows = decode(events)
        assert rows[0]['replayable'] is False and rows[0]['format'] == 'xdog.canonical-transcript.v1'
        serialized = json.dumps(rows)
        assert binary in serialized and 'PUBLIC-REASONING' in serialized and 'CUSTOM' in serialized
        assert all(secret not in serialized for secret in (
            'PRIVATE-CREDENTIAL', 'PRIVATE-SIGNATURE', 'PRIVATE-REDACTED', 'PRIVATE-RESPONSE'))
        if scope == 'timeline':
            assert [row['id'] for row in rows if row['kind'] == 'source'] == ['archive', '@head']
        if scope == 'live':
            assert rows[-1]['kind'] == 'partial' and 'PARTIAL' in serialized
        assert owner.agent.state.messages is old and owner.permissions.mode == 'deny' and not calls
    finally:
        await close(owner, nav, sources)


@pytest.mark.parametrize('value', [None, False, True, 0, '1'])
async def test_display_capability_cannot_authorize_canonical_binary(tmp_path, value):
    owner, _, _, nav, sources, _ = setup(tmp_path)
    try:
        sources.negotiate({'transcript_canonical': value})
        with pytest.raises(ValueError):
            sources.start(request(owner, nav, format='canonical'))
    finally:
        await close(owner, nav, sources)


@pytest.mark.parametrize('change', ['withdraw', 'cancel', 'revision', 'close'])
async def test_canonical_held_spool_is_settled_and_revoked_without_partial_commit(tmp_path, monkeypatch, change):
    import xdog.coding.modes.rpc.transcripts as module
    owner, _, _, nav, sources, events = setup(tmp_path)
    entered, release, spools = threading.Event(), threading.Event(), []
    real = module.prepare
    def held(item, canceled):
        spool = real(item, canceled)
        spools.append(spool)
        entered.set()
        assert release.wait(4)
        return spool
    monkeypatch.setattr(module, 'prepare', held)
    closing = None
    try:
        sources.negotiate({'transcript_canonical': 1})
        sources.start(request(owner, nav, format='canonical'))
        assert await asyncio.to_thread(entered.wait, 2)
        if change == 'withdraw':
            sources.negotiate({'transcript_canonical': 0})
        elif change == 'cancel':
            sources.cancel(request(owner, nav))
        elif change == 'revision':
            nav.refresh_revision()
        else:
            closing = asyncio.create_task(sources.aclose())
            await asyncio.sleep(0.02)
            assert not closing.done()
        release.set()
        await (closing if closing else sources.task)
        assert not any(event['type'] in {'source_begin', 'source_end'} for event in events)
        assert spools and all(spool.file.closed for spool in spools)
    finally:
        release.set()
        await close(owner, nav, sources)


def test_metadata_cannot_impersonate_typed_image_to_bypass_secret_redaction():
    row = project((CustomAgentMessage(data={'type': 'image', 'data': 'token=PRIVATE',
                                          'nested': {'encrypted': 'PRIVATE'}}),))[0]
    assert 'PRIVATE' not in json.dumps(row)
    with pytest.raises(ValueError):
        project((UserMessage(content=(ThinkingContent(thinking='INVALID'),)),))
    with pytest.raises(ValueError):
        project((CustomAgentMessage(data={'nonfinite': float('nan')}),))


@pytest.mark.parametrize('scope', ['saved', 'branch', 'timeline'])
def test_actual_cli_canonical_binary_framing_redaction_and_no_history_activation(tmp_path, scope):
    from packages.coding.tests.test_rpc_transcript_cli import Peer
    peer = Peer(tmp_path)
    binary = base64.b64encode(bytes(range(256)) * 8192).decode()
    peer.target.messages.append(UserMessage(content=(ImageContent(data=binary, mime_type='image/png'),)))
    peer.target.messages.append(AssistantMessage(content=(ThinkingContent(
        thinking='PUBLIC-CLI-REASONING', thinking_signature='PRIVATE-SIGNATURE'),)))
    peer.target.branches[0]['messages'] = messages_to_dicts(peer.target.messages)
    peer.manager.save_session(peer.target)
    try:
        peer.initial()
        assert peer.ready['capabilities']['transcript_canonical'] == 1
        peer.send({'type': 'rpc_hello', 'transcript_sources': 0, 'transcript_canonical': 1})
        peer.collect()
        peer.send(peer.command('saved' if scope == 'branch' else scope, format='canonical',
                               **({'branch_id': 'archived'} if scope == 'branch' else {})))
        events = []
        while True:
            packet = peer.next()
            events.append(packet)
            if packet['type'] in {'source_end', 'source_error'}:
                break
        rows = decode(events)
        serialized = json.dumps(rows)
        assert binary in serialized and 'CLI-END-NEEDLE' in serialized and 'PUBLIC-CLI-REASONING' in serialized
        assert 'PRIVATE-SIGNATURE' not in serialized
        peer.send({'type': 'status'})
        while True:
            status = peer.next()
            if status['type'] == 'status':
                break
        assert status['session_id'] == peer.ready['session_id'] and status['messages'] == 0
        assert status['permission_mode'] == 'deny' and not peer.marker.exists()
        peer.send({'type': 'quit'})
        assert peer.process.wait(timeout=4) == 0, peer.process.stderr.read()
    finally:
        peer.close()


@pytest.mark.parametrize('failure', ['capacity', 'missing', 'branch-content', 'head-content', 'nonfinite'])
async def test_canonical_invalid_saved_or_oversized_source_emits_no_begin_and_recovers(tmp_path, monkeypatch, failure):
    owner, manager, _, nav, sources, events = setup(tmp_path)
    target = manager.create_session(model='offline/model', working_dir=tmp_path)
    target.messages = [UserMessage(content='large' * 200)]
    target.branches = [{'branch_id': 'archive', 'messages': [{'role': 'user', 'content': [
        {'type': 'unknown-binary', 'data': 'PRIVATE-INVALID'}]}]}]
    manager.save_session(target)
    try:
        sources.negotiate({'transcript_canonical': 1})
        if failure == 'head-content':
            path = manager._session_path(target.session_id)
            raw = json.loads(path.read_text())
            raw['messages'] = target.branches[0]['messages']
            path.write_text(json.dumps(raw))
        if failure == 'capacity':
            monkeypatch.setattr('xdog.coding.modes.rpc.transcripts.MAX_SOURCE_BYTES', 10)
        if failure == 'nonfinite':
            owner.agent.replace_messages([CustomAgentMessage(data={'nonfinite': float('nan')})])
        sources.start(request(owner, nav, format='canonical',
            **({'scope': 'saved', 'target_session': 'missing' if failure == 'missing' else target.session_id,
                **({'branch_id': 'archive'} if failure == 'branch-content' else {})}
               if failure != 'nonfinite' else {})))
        await sources.task
        assert events[-1]['type'] == 'source_error'
        assert not any(event['type'] in {'source_begin', 'source_end'} for event in events)
        monkeypatch.undo()
        owner.agent.replace_messages([UserMessage(content='RECOVERED')])
        sources.start(request(owner, nav, 'fresh', format='canonical'))
        await sources.task
        assert 'RECOVERED' in json.dumps(decode(events, 'fresh'))
    finally:
        await close(owner, nav, sources)


@pytest.mark.parametrize('fault', ['none', 'format', 'checksum', 'order', 'owner', 'capacity'])
async def test_canonical_explicit_typed_receiver_checks_format_owner_digest_and_order(tmp_path, fault):
    from xdog.tui.source_receiver import SourceReceiver
    owner, _, _, nav, sources, events = setup(tmp_path)
    results = []
    receiver = SourceReceiver(results.append, decode=lambda file: [json.loads(line) for line in file],
                              max_bytes=0 if fault == 'capacity' else 128 * 1024 * 1024)
    receiver.expect('read', '', owner.session_id, target_session_id=owner.session_id,
                    session_revision=nav.revision, format='canonical')
    try:
        sources.negotiate({'transcript_canonical': 1})
        sources.start(request(owner, nav, format='canonical'))
        await sources.task
        for event in events:
            if not event['type'].startswith('source_'):
                continue
            event = dict(event)
            if event['type'] == 'source_chunk':
                if fault == 'format':
                    event.pop('format')
                elif fault == 'order':
                    event['index'] = 7
                elif fault == 'owner':
                    event['session_revision'] = 'FOREIGN'
            elif event['type'] == 'source_begin' and fault == 'checksum':
                event['sha256'] = '0' * 64
            receiver.feed(event)
        assert results[-1]['type'] == ('source_ready' if fault == 'none' else 'source_failed')
        if fault == 'none':
            assert results[-1]['records'][0]['format'] == 'xdog.canonical-transcript.v1'
        assert receiver._file is None
    finally:
        receiver.close()
        await close(owner, nav, sources)


def test_display_receiver_cannot_accidentally_decode_canonical_binary():
    from xdog.tui.source_receiver import SourceReceiver
    receiver = SourceReceiver(lambda _: None)
    try:
        with pytest.raises(ValueError):
            receiver.expect('read', '', 'owner', format='canonical')
    finally:
        receiver.close()
