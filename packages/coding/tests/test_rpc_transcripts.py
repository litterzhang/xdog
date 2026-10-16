"""Owned canonical/saved/timeline source replay through the real RPC and shared UI receiver."""
from __future__ import annotations

import asyncio
import threading
from dataclasses import replace

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.ai.types import (
    AssistantMessage,
    ImageContent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.permissions import PermissionManager
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.settings_manager import SettingsManager
from xdog.coding.modes.rpc.navigation import RpcNavigation
from xdog.coding.modes.rpc.transcripts import RpcTranscripts
from xdog.tui.source_receiver import SourceReceiver


def setup(tmp_path):
    calls = []
    def stream(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())
    manager = SessionManager(tmp_path / 'sessions')
    owner = AgentSession(agent=Agent(stream, config=AgentConfig(model='offline/model')),
        session_data=manager.create_session(model='offline/model', working_dir=tmp_path), session_manager=manager,
        settings=SettingsManager(global_path=tmp_path / 'settings.json'), working_dir=tmp_path,
        tool_registry=None, bash=None, permissions=PermissionManager('deny'))
    owner.agent.replace_messages([UserMessage(content='LIVE-ONLY')])
    owner._persist()
    events = []
    nav = RpcNavigation(owner, events.append)
    sources = RpcTranscripts(owner, nav, events.append)
    sources.negotiate({'transcript_sources': 1})
    return owner, manager, calls, nav, sources, events


def request(owner, nav, key='read', **fields):
    return {'type': 'transcript_read', 'request_id': key, 'session_id': owner.session_id,
            'session_revision': nav.revision, **fields}


async def close(owner, nav, sources):
    await sources.aclose()
    await nav.aclose()
    await owner.children.supervisor.aclose()
    await owner.extensions.aclose()
    owner.dispose()


def receive(events, identifier, owner, revision, target, branch=''):
    received = []
    reader = SourceReceiver(received.append)
    reader.expect(identifier, '', owner, target_session_id=target, branch_id=branch, session_revision=revision)
    try:
        for event in events:
            if event['type'] in {'source_begin', 'source_chunk', 'source_end', 'source_error'}:
                reader.feed(event)
        settled = [event for event in received if event['type'] == 'source_ready']
        assert len(settled) == 1, received
        return settled[0]['records']
    finally:
        reader.close()


@pytest.mark.parametrize('failure', ['missing-session', 'missing-branch', 'corrupt', 'capacity'])
async def test_saved_source_refuses_without_live_fallback_or_partial_commit_then_recovers(
    tmp_path, monkeypatch, failure,
):
    owner, manager, calls, nav, sources, events = setup(tmp_path)
    target = manager.create_session(model='offline/model', working_dir=tmp_path)
    target.messages = [UserMessage(content='x' * 1000)]
    manager.save_session(target)
    if failure == 'corrupt':
        manager._session_path(target.session_id).write_text('not json')
    if failure == 'capacity':
        monkeypatch.setattr('xdog.coding.modes.rpc.transcripts.MAX_SOURCE_BYTES', 10)
    try:
        sources.start(request(owner, nav, scope='saved',
            target_session='missing' if failure == 'missing-session' else target.session_id,
            branch_id='missing' if failure == 'missing-branch' else ''))
        await sources.task
        assert events[-1]['type'] == 'source_error' and events[-1]['request_id'] == 'read'
        assert not any(event['type'] == 'source_end' for event in events)
        assert 'LIVE-ONLY' not in str(events) and not calls
        monkeypatch.undo()
        sources.start(request(owner, nav, 'recover'))
        await sources.task
        assert receive(events, 'recover', owner.session_id, nav.revision, owner.session_id)[0].body == 'LIVE-ONLY'
    finally:
        await close(owner, nav, sources)


@pytest.mark.parametrize('change', ['cancel', 'withdraw', 'revision', 'navigate', 'replace', 'close'])
async def test_preparation_is_owned_settled_and_canceled_or_obsolete_reads_cannot_complete(
    tmp_path, monkeypatch, change,
):
    import xdog.coding.modes.rpc.transcripts as module

    owner, _, calls, nav, sources, events = setup(tmp_path)
    entered, release = threading.Event(), threading.Event()
    prepared = []
    method = module.prepare
    def held(item, canceled):
        result = method(item, canceled)
        prepared.append(result)
        if item.identifier == 'read':
            entered.set()
            assert release.wait(4)
        return result
    monkeypatch.setattr(module, 'prepare', held)
    closing = None
    try:
        sources.start(request(owner, nav))
        assert await asyncio.to_thread(entered.wait, 3)
        if change == 'cancel':
            sources.cancel(request(owner, nav))
        elif change == 'withdraw':
            sources.negotiate({'transcript_sources': 0})
        elif change == 'revision':
            nav.refresh_revision()
        elif change == 'navigate':
            nav.start({'action': 'goto', 'request_id': 'navigate', 'expected_session': owner.session_id,
                       'session_revision': nav.revision, 'index': 0}, turn_running=False, extension_running=False)
            await nav.task
            assert events[-1]['type'] == 'session_result' and events[-1]['success']
        elif change == 'replace':
            sources.start(request(owner, nav, 'replacement'))
            assert len(prepared) == 1  # No second spool until the existing owner settles.
        else:
            closing = asyncio.create_task(sources.aclose())
            await asyncio.sleep(0.02)
            assert not closing.done()
        release.set()
        await (closing if closing is not None else sources.task)
        assert not any(event['type'] == 'source_end' and event['request_id'] == 'read' for event in events)
        assert all(spool.file.closed for spool in prepared)
        assert sources.active is None and not calls
        if change == 'replace':
            assert receive(events, 'replacement', owner.session_id, nav.revision, owner.session_id)
    finally:
        release.set()
        await close(owner, nav, sources)


@pytest.mark.parametrize('value', [None, 0, True, '1', 2])
async def test_independent_exact_capability_and_owner_identity_are_required(tmp_path, value):
    owner, _, calls, nav, sources, _ = setup(tmp_path)
    try:
        sources.negotiate({'transcript_sources': value, 'extension_dialogs': 1})
        with pytest.raises(ValueError, match='Negotiate'):
            sources.start(request(owner, nav))
        sources.negotiate({'transcript_sources': 1})
        for changes in ({'session_id': 'foreign'}, {'session_revision': 'foreign'}, {'request_id': ''},
                        {'scope': 'live', 'target_session': 'foreign'}, {'scope': 'timeline', 'branch_id': 'branch'},
                        {'scope': 'saved', 'target_session': '../outside'}, {'scope': []}):
            with pytest.raises(ValueError):
                sources.start(request(owner, nav, **changes))
        sources.start(request(owner, nav))
        await sources.task
        with pytest.raises(ValueError, match='reused'):
            sources.start(request(owner, nav))
        assert not calls
    finally:
        await close(owner, nav, sources)


@pytest.mark.parametrize('role', ['assistant', 'user', 'tool'])
async def test_invalid_binary_assistant_part_is_refused_not_rendered_as_persistence_repr(tmp_path, role):
    owner, _, _, nav, sources, events = setup(tmp_path)
    part = ThinkingContent(thinking='text', thinking_signature='PRIVATE-INVALID-SIGNATURE')
    message = (AssistantMessage(content=(ImageContent(data='PRIVATE-INVALID-BINARY', mime_type='image/png'),))
               if role == 'assistant' else UserMessage(content=(part,)) if role == 'user' else
               ToolResultMessage(tool_call_id='call', tool_name='tool', content=(part,)))
    owner.agent._state = replace(owner.agent.state, stream_message=message)
    try:
        sources.start(request(owner, nav))
        await sources.task
        assert events[-1]['type'] == 'source_error'
        assert not any(event['type'] in {'source_begin', 'source_end'} for event in events)
    finally:
        await close(owner, nav, sources)


@pytest.mark.parametrize('fault', ['lease', 'checksum', 'order', 'scope', 'capacity'])
async def test_shared_receiver_refuses_mixed_or_incomplete_rpc_sources_without_partial_selection(tmp_path, fault):
    owner, _, _, nav, sources, events = setup(tmp_path)
    received = []
    receiver = SourceReceiver(received.append, max_bytes=0 if fault == 'capacity' else 128 * 1024 * 1024)
    receiver.expect('read', '', owner.session_id, target_session_id=owner.session_id, session_revision=nav.revision)
    try:
        sources.start(request(owner, nav))
        await sources.task
        packets = [dict(event) for event in events if event['type'].startswith('source_')]
        for packet in packets:
            if fault == 'capacity':
                pass
            elif packet['type'] == 'source_chunk':
                if fault == 'lease':
                    packet['session_revision'] = 'foreign'
                elif fault == 'order':
                    packet['index'] = 1
                elif fault == 'scope':
                    packet['target_session_id'] = 'foreign'
            elif fault == 'checksum' and packet['type'] == 'source_begin':
                packet['sha256'] = '0' * 64
            receiver.feed(packet)
        assert not any(event['type'] == 'source_ready' for event in received)
        assert received[-1]['type'] == 'source_failed' and receiver._file is None
    finally:
        receiver.close()
        await close(owner, nav, sources)


async def test_live_source_captures_admitted_messages_not_later_appended_turns(tmp_path, monkeypatch):
    import xdog.coding.modes.rpc.transcripts as module

    owner, _, _, nav, sources, events = setup(tmp_path)
    entered, release = threading.Event(), threading.Event()
    prepare = module.prepare
    def held(item, canceled):
        entered.set()
        assert release.wait(3)
        return prepare(item, canceled)
    monkeypatch.setattr(module, 'prepare', held)
    try:
        sources.start(request(owner, nav))
        assert await asyncio.to_thread(entered.wait, 2)
        owner.agent.append_message(UserMessage(content='LATER-ADMISSION'))
        release.set()
        await sources.task
        captured = receive(events, 'read', owner.session_id, nav.revision, owner.session_id)
        assert 'LATER-ADMISSION' not in str(captured) and 'LIVE-ONLY' in str(captured)
        sources.start(request(owner, nav, 'fresh'))
        await sources.task
        assert 'LATER-ADMISSION' in str(receive(events, 'fresh', owner.session_id, nav.revision, owner.session_id))
    finally:
        release.set()
        await close(owner, nav, sources)


async def test_real_rpc_reader_accepts_status_and_permission_response_while_source_preparation_is_held(
    tmp_path, monkeypatch,
):
    import json
    import queue

    import xdog.coding.modes.rpc.rpc_mode as rpc
    import xdog.coding.modes.rpc.transcripts as module
    from xdog.agent import AgentContext, BeforeToolCallContext

    owner, _, calls, nav, unused, _ = setup(tmp_path)
    owner.permissions = PermissionManager('ask')
    entered, release = threading.Event(), threading.Event()
    pending, events = queue.Queue(), []
    state = {}
    prepare = module.prepare
    def held(item, canceled):
        result = prepare(item, canceled)
        entered.set()
        assert release.wait(4)
        return result
    async def prompt(session, message, emit, *, batch_stream=False):
        assert batch_stream  # Actual runtime owns the batch projection; this stub only tests reader liveness.
        decision = await session.permissions.before_tool_call(BeforeToolCallContext(
            assistant_message=AssistantMessage(), tool_call=ToolCall(id='owned', name='bash', arguments={}),
            args={'command': 'pytest'}, context=AgentContext()))
        emit({'type': 'test_permission_settled', 'allowed': decision is None})
    class Input:
        def readline(self, size=-1):
            return json.dumps(pending.get(timeout=5)) + '\n'
    class Output:
        def write(self, text):
            for line in text.splitlines():
                event = json.loads(line)
                events.append(event)
                if event['type'] == 'ready':
                    state.update(event)
                    pending.put({'type': 'rpc_hello', 'transcript_sources': 1})
                elif event['type'] == 'transcript_capabilities':
                    pending.put({'type': 'transcript_read', 'request_id': 'held',
                                 'session_id': state['session_id'], 'session_revision': state['session_revision']})
                elif event['type'] == 'status':
                    assert entered.is_set() and not release.is_set() and event['transcript_running']
                    pending.put({'type': 'prompt', 'message': 'owned approval'})
                elif event['type'] == 'permission_request':
                    assert not release.is_set()
                    pending.put({'type': 'permission_response', 'id': event['id'], 'decision': 'allow_once'})
                elif event['type'] == 'test_permission_settled':
                    assert event['allowed'] and not release.is_set()
                    release.set()
                elif event['type'] == 'source_end':
                    pending.put({'type': 'quit'})
            return len(text)
        def flush(self):
            pass
    async def request_status():
        assert await asyncio.to_thread(entered.wait, 3)
        pending.put({'type': 'status'})
    monkeypatch.setattr(module, 'prepare', held)
    monkeypatch.setattr(rpc, '_handle_prompt', prompt)
    monkeypatch.setattr(rpc.sys, 'stdin', Input())
    monkeypatch.setattr(rpc.sys, 'stdout', Output())
    watcher = asyncio.create_task(request_status())
    try:
        async with asyncio.timeout(6):
            assert await rpc.run_rpc_mode(owner) == 0
        await watcher
        assert any(event['type'] == 'test_permission_settled' for event in events) and not calls
        assert owner.permissions.mode == 'ask'
    finally:
        release.set()
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)
        await close(owner, nav, unused)


async def test_source_only_hello_does_not_withdraw_existing_extension_navigation_capability(tmp_path, monkeypatch):
    import json
    import queue

    import xdog.coding.modes.rpc.rpc_mode as rpc
    from xdog.coding.modes.rpc.extensions import RpcExtensions

    owner, _, _, nav, sources, _ = setup(tmp_path)
    pending, controls = queue.Queue(), []
    negotiate = RpcExtensions.negotiate
    def observe(controller, packet):
        controls.append(controller)
        negotiate(controller, packet)
    class Input:
        def readline(self, size=-1):
            return json.dumps(pending.get(timeout=3)) + '\n'
    class Output:
        def write(self, text):
            for line in text.splitlines():
                event = json.loads(line)
                if event['type'] == 'ready':
                    pending.put({'type': 'rpc_hello', 'extension_navigation': 1})
                elif event['type'] == 'rpc_capabilities':
                    assert event['extension_navigation'] == 1
                    pending.put({'type': 'rpc_hello', 'transcript_sources': 1})
                elif event['type'] == 'transcript_capabilities' and event['transcript_sources']:
                    assert len(controls) == 1 and controls[0].navigation_enabled
                    pending.put({'type': 'quit'})
            return len(text)
        def flush(self):
            pass
    monkeypatch.setattr(RpcExtensions, 'negotiate', observe)
    monkeypatch.setattr(rpc.sys, 'stdin', Input())
    monkeypatch.setattr(rpc.sys, 'stdout', Output())
    try:
        async with asyncio.timeout(4):
            assert await rpc.run_rpc_mode(owner) == 0
        assert len(controls) == 1
    finally:
        await close(owner, nav, sources)


async def test_saved_source_symlink_is_not_followed_and_does_not_fall_back_to_current_head(tmp_path):
    owner, manager, _, nav, sources, events = setup(tmp_path)
    path = manager._session_path('linked')
    private = tmp_path / 'private.json'
    private.write_text('{"session_id":"linked","messages":[{"role":"user","content":"PRIVATE-OUTSIDE"}]}')
    path.symlink_to(private)
    try:
        sources.start(request(owner, nav, scope='saved', target_session='linked'))
        await sources.task
        assert events[-1]['type'] == 'source_error'
        assert not any(event['type'] in {'source_begin', 'source_chunk', 'source_end'} for event in events)
    finally:
        await close(owner, nav, sources)
