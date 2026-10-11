"""Independent RPC compaction review waits for actual callback/result delivery."""
from __future__ import annotations

import asyncio
import json

import pytest
from xdog.ai.types import AssistantMessage, TextContent
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.modes.rpc.extensions import RpcExtensions
from xdog.coding.modes.rpc.navigation import RpcNavigation

from packages.coding.tests.test_rpc_compaction import history, wait
from packages.coding.tests.test_rpc_extensions import close, session


@pytest.mark.parametrize('ending', ['apply', 'deny', 'fail', 'withdraw', 'old-client', 'delivery-fail'])
async def test_rpc_worker_compaction_review_is_independent_and_deferred_until_delivery(tmp_path, ending):
    owner, _ = session(tmp_path, extra=('compactionrequest',))
    original = history(owner)
    calls, events = [], []
    def stream(model, context, options):
        calls.append(context)
        return EventStream.empty(AssistantMessage(content=(TextContent(text='RPC reviewed summary'),)))
    owner.agent.set_stream_fn(stream)
    delivery, release = asyncio.Event(), asyncio.Event()
    async def send(value):
        delivery.set()
        await release.wait()
        if ending == 'delivery-fail':
            raise OSError('Injected command-result delivery failure')
        events.append(value)
    nav = RpcNavigation(owner, events.append)
    controller = RpcExtensions(owner, events.append, navigation=nav, send=send)
    controller.negotiate({'extension_compaction': int(ending != 'old-client')})
    assert not controller.navigation_enabled and not controller.dialogs_enabled
    try:
        controller.start({'request_id': 'owned', 'session_id': owner.session_id, 'action': 'command',
            'name': 'ext-compactionrequest-request', 'args': json.dumps({'fail': ending == 'fail'})},
            turn_running=False)
        if ending != 'old-client':
            await wait(lambda: any(row['type'] == 'extension_dialog' for row in events))
            event = next(row for row in events if row['type'] == 'extension_dialog')
            assert event['dialog']['operation'] == 'compact'
            answer = {'id': event['dialog']['id'], 'request_id': event['request_id'],
                      'session_id': event['session_id'], 'scope': event['scope'], 'value': ending != 'deny'}
            with pytest.raises(ValueError):
                controller.answer({**answer, 'scope': 'foreign'})
            controller.answer(answer)
        await wait(delivery.is_set)
        assert not calls and owner.messages == original
        if ending == 'withdraw':
            controller.negotiate({'extension_compaction': 0})
            controller.negotiate({'extension_compaction': 1})
        release.set()
        await asyncio.gather(controller.task, return_exceptions=True)
        await asyncio.sleep(0)
        if nav.task is not None:
            await nav.task
        if ending == 'apply':
            assert len(calls) == 1 and not calls[0].tools and owner.messages != original
            assert events[-1]['type'] == 'session_result' and events[-1]['action'] == 'compact'
            assert events[-1]['success']
            reply = next(row for row in events if row['type'] == 'extension_result')
            queued = next(row for row in events if row['type'] == 'extension_navigation_queued')
            assert events.index(reply) < events.index(queued)
        else:
            assert not calls and owner.messages == original and owner.session_data.compaction is None
        assert owner.permissions.mode == 'deny'
    finally:
        release.set()
        await controller.aclose()
        await nav.aclose()
        await close(owner)


async def test_withdrawal_cancels_only_compaction_dispatched_by_this_extension_owner(tmp_path):
    from packages.coding.tests.test_rpc_compaction import summary_stream
    owner, _ = session(tmp_path, extra=('compactionrequest',))
    original = history(owner)
    entered, release, stopped = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls, events = [], []
    owner.agent.set_stream_fn(summary_stream(entered, release, stopped, calls))
    nav = RpcNavigation(owner, events.append)
    controller = RpcExtensions(owner, events.append, navigation=nav)
    controller.negotiate({'extension_editor': 1, 'extension_navigation': 1, 'extension_dialogs': 1})
    controller.negotiate({'extension_compaction': 1})
    assert controller.editor_enabled and controller.navigation_enabled and controller.dialogs_enabled
    try:
        controller.start({'request_id': 'owned', 'session_id': owner.session_id, 'action': 'command',
                          'name': 'ext-compactionrequest-request', 'args': '{}'}, turn_running=False)
        await wait(lambda: any(row['type'] == 'extension_dialog' for row in events))
        event = next(row for row in events if row['type'] == 'extension_dialog')
        controller.answer({'id': event['dialog']['id'], 'request_id': event['request_id'],
                           'session_id': event['session_id'], 'scope': event['scope'], 'value': True})
        await wait(entered.is_set)
        controller.negotiate({'extension_compaction': 0})
        await nav.task
        assert stopped.is_set() and owner.messages == original
        assert events[-1]['canceled'] and not events[-1]['success']
        entered.clear()
        stopped.clear()
        nav.start({'action': 'compact', 'request_id': 'manual', 'expected_session': owner.session_id},
                  turn_running=False, extension_running=False)
        await wait(entered.is_set)
        controller.negotiate({'extension_compaction': 1})
        controller.negotiate({'extension_compaction': 0})
        await asyncio.sleep(.02)
        assert nav.compacting and not stopped.is_set()  # Unrelated native user operation is not revoked.
        release.set()
        await nav.task
        assert events[-1]['success'] and owner.messages != original
    finally:
        release.set()
        await controller.aclose()
        await nav.aclose()
        await close(owner)


@pytest.mark.parametrize('approve', [False, True])
def test_actual_cli_extension_compaction_review_delivery_and_native_receipt(tmp_path, approve):
    import shutil
    from pathlib import Path

    from xdog.ai.types import UserMessage
    from xdog.coding.core.session_manager import SessionManager

    from packages.coding.tests.test_rpc_children_cli import Peer
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/compactionrequest',
                    root / 'compactionrequest')
    home = tmp_path / 'home'
    home.mkdir()
    (home / 'settings.json').write_text(json.dumps({'extensions': ['compactionrequest'], 'extension_dir': str(root)}))
    peer = Peer(tmp_path)
    try:
        store = SessionManager(home / 'sessions')
        target = store.create_session(model='rpc-fixture', working_dir=tmp_path)
        for index in range(6):
            target.messages.extend([UserMessage(content=f'old work {index} ' + 'x' * 2000),
                                    AssistantMessage(content=(TextContent(text=f'answer {index}'),))])
        store.save_session(target)
        peer.send({'type': 'session_control', 'action': 'resume', 'request_id': 'load',
                   'expected_session': peer.ready['session_id'], 'session_revision': peer.ready['session_revision'],
                   'target_session': target.session_id})
        loaded = peer.read('session_result')
        assert loaded['success']
        peer.send({'type': 'rpc_hello', 'extension_compaction': 1})
        capability = peer.read('rpc_capabilities')
        assert capability['extension_compaction'] == 1 and capability['extension_navigation'] == 0
        peer.send({'type': 'extension_control', 'request_id': 'owned', 'session_id': target.session_id,
                   'session_revision': loaded['session_revision'], 'action': 'command',
                   'name': 'ext-compactionrequest-request', 'args': '{}'})
        event = peer.read('extension_dialog')
        assert event['dialog']['operation'] == 'compact' and not peer.marker.exists()
        peer.send({'type': 'extension_dialog_response', 'id': event['dialog']['id'],
                   'request_id': event['request_id'], 'session_id': event['session_id'],
                   'scope': event['scope'], 'value': approve})
        reply = peer.read('extension_result')
        assert reply['success']
        assert json.loads(reply['output'].removeprefix('COMPACTION-QUEUED '))['queued'] is approve
        if approve:
            queued = peer.read('extension_navigation_queued')
            completed = peer.read('session_result')
            assert peer.events.index(reply) < peer.events.index(queued) < peer.events.index(completed)
            assert completed['action'] == 'compact' and completed['success']
            assert completed['request_id'] == queued['navigation_request_id']
            assert len(store.load_session(target.session_id).messages) < len(target.messages)
        else:
            assert not peer.marker.exists() and store.load_session(target.session_id).compaction is None
        peer.send({'type': 'quit'})
        peer.read('quit_ack')
        assert peer.process.wait(timeout=6) == 0
    finally:
        try:
            if peer.process.poll() is None and not peer.process.stdin.closed:
                peer.send({'type': 'quit'})
                peer.process.wait(timeout=6)
        finally:
            peer.close()
