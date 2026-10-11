"""Real canonical compaction must not stop the JSONL reader or release its owner early."""
from __future__ import annotations

import asyncio
import json
import queue
from unittest.mock import patch

import pytest
from xdog.agent.messages import messages_to_dicts
from xdog.ai.types import AssistantMessage, DoneEvent, TextContent, UserMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.modes.rpc.rpc_mode import run_rpc_mode

from packages.coding.tests.test_rpc_navigation import close, setup


async def wait(predicate, timeout=6):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(.003)


class Connection:
    def __init__(self):
        self.lines = queue.Queue()
        self.events = []

    def send(self, value):
        self.lines.put('' if value is None else json.dumps(value) + '\n')

    def readline(self, size=-1):
        return self.lines.get(timeout=10)

    def write(self, value):
        self.events.extend(json.loads(line) for line in value.splitlines() if line)
        return len(value)

    def flush(self):
        pass


def history(owner):
    messages = []
    for index in range(6):
        messages.extend([UserMessage(content=f'original turn {index} ' + 'x' * 2000),
                         AssistantMessage(content=(TextContent(text=f'original answer {index}'),))])
    owner.agent.replace_messages(messages)
    owner._persist()
    return messages


def summary_stream(entered, release, stopped, calls):
    def stream(model, context, options):
        calls.append(context)
        message = AssistantMessage(content=(TextContent(text='owned summary'),))
        result = asyncio.get_running_loop().create_future()
        result.set_result(message)
        async def generate():
            entered.set()
            try:
                await release.wait()
                yield DoneEvent(message=message)
            finally:
                stopped.set()
        return EventStream.from_async_generator(generate(), result_future=result)
    return stream


async def test_real_compaction_keeps_rpc_status_reader_responsive(tmp_path):
    owner, store, _ = setup(tmp_path)
    original = history(owner)
    entered, release, stopped = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls = []
    owner.agent.set_stream_fn(summary_stream(entered, release, stopped, calls))
    io = Connection()
    with patch('xdog.coding.modes.rpc.rpc_mode.sys.stdin', io), patch(
        'xdog.coding.modes.rpc.rpc_mode.sys.stdout', io,
    ):
        task = asyncio.create_task(run_rpc_mode(owner))
        try:
            await wait(lambda: any(row['type'] == 'ready' for row in io.events))
            io.send({'type': 'compact'})
            await wait(entered.is_set)
            io.send({'type': 'status'})
            await wait(lambda: any(row['type'] == 'status' for row in io.events), timeout=1)
            status = next(row for row in io.events if row['type'] == 'status')
            assert status['compaction_running'] and status['messages'] == len(original)
            assert owner.messages == original and owner._compacting
            release.set()
            await wait(lambda: any(row['type'] == 'ok' and row.get('action') == 'compact' for row in io.events))
            assert owner.session_data.compaction['recovery_branch_id']
            assert messages_to_dicts(store.load_session(owner.session_id).messages) == messages_to_dicts(owner.messages)
            assert len(owner.messages) < len(original) and len(calls) == 1 and not calls[0].tools
            assert stopped.is_set() and not owner._compacting
            io.send({'type': 'quit'})
            assert await asyncio.wait_for(task, 6) == 0
        finally:
            release.set()
            if not task.done():
                io.send({'type': 'quit'})
            await asyncio.wait_for(task, 6)
            io.send(None)
            await close(owner)


@pytest.mark.parametrize('ending', ['abort', 'quit', 'eof', 'scoped-cancel'])
async def test_real_compaction_rpc_endings_cancel_summary_without_early_owner_release(tmp_path, ending):
    owner, store, _ = setup(tmp_path)
    original = history(owner)
    original_bytes = store._session_path(owner.session_id).read_bytes()
    entered, release, stopped = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls = []
    owner.agent.set_stream_fn(summary_stream(entered, release, stopped, calls))
    io = Connection()
    with patch('xdog.coding.modes.rpc.rpc_mode.sys.stdin', io), patch(
        'xdog.coding.modes.rpc.rpc_mode.sys.stdout', io,
    ):
        task = asyncio.create_task(run_rpc_mode(owner))
        try:
            await wait(lambda: any(row['type'] == 'ready' for row in io.events))
            ready = next(row for row in io.events if row['type'] == 'ready')
            assert ready['capabilities']['session_compaction'] == 1
            io.send({'type': 'session_control', 'action': 'compact', 'request_id': 'owned',
                     'expected_session': owner.session_id, 'session_revision': ready['session_revision'],
                     'instructions': 'Keep FOCUS-MARKER'})
            await wait(entered.is_set)
            assert 'FOCUS-MARKER' in str(calls[0].messages)
            io.send({'type': 'session_control_cancel', 'request_id': 'foreign', 'expected_session': owner.session_id})
            await wait(lambda: any(row.get('action') == 'session_control_cancel' for row in io.events))
            assert not stopped.is_set() and owner._compacting
            for command in ({'type': 'prompt', 'message': 'must not run'},
                            {'type': 'set_model', 'model': 'foreign'},
                            {'type': 'compact'},
                            {'type': 'session_control', 'action': 'new', 'request_id': 'other',
                             'expected_session': owner.session_id}):
                io.send(command)
            io.send({'type': 'status'})
            await wait(lambda: any(row['type'] == 'status' for row in io.events))
            assert next(row for row in io.events if row['type'] == 'status')['compaction_running']
            assert sum(row['type'] == 'error' for row in io.events) >= 5
            assert len(calls) == 1 and owner.messages == original
            if ending == 'scoped-cancel':
                io.send({'type': 'session_control_cancel', 'request_id': 'owned',
                         'expected_session': owner.session_id})
            elif ending == 'eof':
                io.send(None)
            else:
                io.send({'type': ending})
            await wait(stopped.is_set)
            if ending in {'abort', 'scoped-cancel'}:
                await wait(lambda: any(row['type'] == 'session_result' and row.get('request_id') == 'owned'
                                      for row in io.events))
                receipt = next(row for row in io.events if row['type'] == 'session_result'
                               and row.get('request_id') == 'owned')
                assert receipt['canceled'] and not receipt['success'] and not receipt.get('state_changed')
                owner.agent.set_stream_fn(lambda *args: EventStream.empty(AssistantMessage(
                    content=(TextContent(text='fresh after cancellation'),))))
                io.send({'type': 'prompt', 'message': 'fresh'})
                await wait(lambda: any(row['type'] == 'message_end' for row in io.events))
                assert 'fresh after cancellation' in str(io.events)
                io.send({'type': 'quit'})
            assert await asyncio.wait_for(task, 6) == 0
            if ending in {'quit', 'eof'}:
                assert owner.messages == original
                assert store._session_path(owner.session_id).read_bytes() == original_bytes
            assert not owner._compacting and owner.permissions.mode == 'deny'
        finally:
            release.set()
            if not task.done():
                io.send({'type': 'quit'})
            await asyncio.wait_for(task, 6)
            io.send(None)
            await close(owner)


@pytest.mark.parametrize('phase', ['before-write', 'after-write', 'failure'])
async def test_compaction_keeps_rpc_owner_until_actual_commit_settles(tmp_path, monkeypatch, phase):
    import threading
    owner, store, _ = setup(tmp_path)
    original = history(owner)
    original_bytes = store._session_path(owner.session_id).read_bytes()
    entered, release = threading.Event(), threading.Event()
    owner.agent.set_stream_fn(lambda *args: EventStream.empty(AssistantMessage(
        content=(TextContent(text='owned committed summary'),))))
    save = store.save_session
    def blocked(data, *args, **kwargs):
        if phase == 'after-write':
            save(data, *args, **kwargs)
        entered.set()
        assert release.wait(8), 'Test commit gate was not released'
        if phase == 'failure':
            raise OSError('Injected compaction persistence failure')
        if phase != 'after-write':
            save(data, *args, **kwargs)
    monkeypatch.setattr(store, 'save_session', blocked)
    io = Connection()
    with patch('xdog.coding.modes.rpc.rpc_mode.sys.stdin', io), patch(
        'xdog.coding.modes.rpc.rpc_mode.sys.stdout', io,
    ):
        task = asyncio.create_task(run_rpc_mode(owner))
        try:
            await wait(lambda: any(row['type'] == 'ready' for row in io.events))
            io.send({'type': 'compact', 'request_id': 'legacy'})
            await wait(entered.is_set)
            io.send({'type': 'abort'})
            await wait(lambda: any(row['type'] == 'ok' and row.get('action') == 'abort' for row in io.events))
            io.send({'type': 'status'})
            await wait(lambda: any(row['type'] == 'status' for row in io.events))
            assert next(row for row in io.events if row['type'] == 'status')['compaction_running']
            assert not any(row.get('action') == 'compact' for row in io.events)
            io.send({'type': 'prompt', 'message': 'must remain excluded during commit'})
            await wait(lambda: any(row['type'] == 'error' and 'running' in row.get('message', '') for row in io.events))
            assert owner.messages == original and owner._compacting
            release.set()
            await wait(lambda: any(row.get('action') == 'compact' for row in io.events))
            receipt = next(row for row in io.events if row.get('action') == 'compact')
            assert receipt['canceled']
            if phase == 'failure':
                assert owner.messages == original and not receipt.get('state_changed')
                assert store._session_path(owner.session_id).read_bytes() == original_bytes
            else:
                assert receipt['state_changed'] and receipt['receipt_canceled']
                assert owner.messages != original and owner.session_data.compaction['recovery_branch_id']
                saved = store.load_session(owner.session_id)
                assert messages_to_dicts(saved.messages) == messages_to_dicts(owner.messages)
            assert not owner._compacting and owner.permissions.mode == 'deny'
            io.send({'type': 'quit'})
            assert await asyncio.wait_for(task, 6) == 0
        finally:
            release.set()
            if not task.done():
                io.send({'type': 'quit'})
            await asyncio.wait_for(task, 6)
            io.send(None)
            await close(owner)


@pytest.mark.parametrize('bad', [{'instructions': None}, {'instructions': 'x' * 16385},
                                 {'instructions': '\x00'}, {'target_session': 'foreign'}, {'branch_id': 'foreign'},
                                 {'index': 0}, {'expected_session': 'foreign'}, {'request_id': ''}])
async def test_compaction_owner_and_instruction_preflight_never_mutates_on_invalid_request(tmp_path, bad):
    from xdog.coding.modes.rpc.navigation import RpcNavigation
    owner, store, adapter = setup(tmp_path)
    before = store._session_path(owner.session_id).read_bytes()
    nav = RpcNavigation(owner, lambda value: None)
    try:
        with pytest.raises(ValueError):
            nav.start({'action': 'compact', 'request_id': 'owned', 'expected_session': owner.session_id,
                       **bad}, turn_running=False, extension_running=False)
        assert not nav.busy and not nav.used and not adapter.calls and not owner._compacting
        assert store._session_path(owner.session_id).read_bytes() == before
    finally:
        await nav.aclose()
        await close(owner)


async def test_compaction_cancel_before_admission_reports_no_effect_and_stale_owner_cannot_retry(tmp_path):
    from xdog.coding.modes.rpc.navigation import RpcNavigation
    owner, store, adapter = setup(tmp_path)
    events = []
    nav = RpcNavigation(owner, events.append)
    packet = {'action': 'compact', 'request_id': 'owned', 'expected_session': owner.session_id}
    try:
        nav.start(packet, turn_running=False, extension_running=False)
        nav.cancel(packet)
        await nav.task
        assert events[-1]['canceled'] and not events[-1]['success'] and not events[-1].get('state_changed')
        with pytest.raises(ValueError):
            nav.start(packet, turn_running=False, extension_running=False)
        nav.start({**packet, 'request_id': 'empty'}, turn_running=False, extension_running=False)
        await nav.task  # The short history is a real canonical no-op, not a summary/model request.
        assert events[-1]['success'] and nav.enforced and not adapter.calls
        with pytest.raises(ValueError, match='revision'):
            nav.start({**packet, 'request_id': 'old'}, turn_running=False, extension_running=False)
    finally:
        await nav.aclose()
        await close(owner)


@pytest.mark.parametrize('ending', ['abort', 'quit', 'eof', 'success'])
def test_actual_cli_rpc_compaction_is_owned_responsive_and_cleans_summary_stream(tmp_path, ending):
    import time

    from xdog.coding.core.session_manager import SessionManager

    from packages.coding.tests.test_rpc_children_cli import Peer
    peer = Peer(tmp_path)
    try:
        store = SessionManager(peer.home / 'sessions')
        target = store.create_session(model='rpc-fixture', working_dir=tmp_path)
        messages = []
        for index in range(6):
            messages.extend([UserMessage(content=f'CLI original {index} ' + 'x' * 2000),
                             AssistantMessage(content=(TextContent(text=f'CLI answer {index}'),))])
        target.messages = messages
        store.save_session(target)
        peer.send({'type': 'session_control', 'action': 'resume', 'request_id': 'load',
                   'expected_session': peer.ready['session_id'], 'session_revision': peer.ready['session_revision'],
                   'target_session': target.session_id})
        loaded = peer.read('session_result')
        assert loaded['success']
        before = store._session_path(target.session_id).read_bytes()
        peer.send({'type': 'compact', 'request_id': 'cli-compact', 'session_id': target.session_id,
                   'session_revision': loaded['session_revision'],
                   'instructions': 'Keep selected context' if ending == 'success' else 'hold compaction'})
        deadline = time.monotonic() + 6
        while not peer.marker.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        assert peer.marker.exists()
        if ending == 'success':
            result = peer.read('ok')
            assert result['action'] == 'compact' and result['session_revision'] != loaded['session_revision']
            saved = store.load_session(target.session_id)
            assert saved.compaction['recovery_branch_id'] and len(saved.messages) < len(messages)
            peer.send({'type': 'quit'})
            peer.read('quit_ack')
        else:
            peer.send({'type': 'status'})
            assert peer.read('status')['compaction_running']
            if ending == 'abort':
                peer.send({'type': 'abort'})
                assert peer.read('ok')['action'] == 'abort'
                canceled = peer.read('error')
                assert canceled['action'] == 'compact' and canceled['canceled']
                assert not canceled.get('state_changed')
                peer.send({'type': 'quit'})
                peer.read('quit_ack')
            elif ending == 'quit':
                peer.send({'type': 'quit'})
                peer.read('quit_ack')
            else:
                peer.process.stdin.close()
        assert peer.process.wait(timeout=6) == 0
        if ending != 'success':
            assert peer.marker.with_suffix('.settled').is_file()
            assert store._session_path(target.session_id).read_bytes() == before
    finally:
        try:
            if peer.process.poll() is None and not peer.process.stdin.closed:
                peer.send({'type': 'quit'})
                peer.process.wait(timeout=6)
        finally:
            peer.close()
