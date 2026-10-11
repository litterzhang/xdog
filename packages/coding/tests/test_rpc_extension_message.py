"""Extension message review dispatches the canonical producer only after delivery."""
from __future__ import annotations

import asyncio
import json

import pytest
from xdog.ai.types import AssistantMessage, TextContent
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.modes.rpc.extensions import RpcExtensions
from xdog.coding.modes.rpc.navigation import RpcNavigation

from packages.coding.tests.test_rpc_compaction import wait
from packages.coding.tests.test_rpc_extensions import close, session


@pytest.mark.parametrize('ending', ['apply', 'deny', 'fail', 'withdraw', 'old-client', 'delivery-fail'])
async def test_message_review_is_independent_and_deferred_until_callback_delivery(tmp_path, ending):
    owner, _ = session(tmp_path, extra=('messagerequest',))
    calls, events = [], []
    def stream(model, context, options):
        calls.append(context)
        return EventStream.empty(AssistantMessage(content=(TextContent(text='MESSAGE REPLY'),)))
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
    controller.negotiate({'extension_messages': int(ending != 'old-client')})
    assert not controller.navigation_enabled and not controller.dialogs_enabled
    try:
        controller.start({'request_id': 'owned', 'session_id': owner.session_id, 'action': 'command',
            'name': 'ext-messagerequest-request', 'args': json.dumps({'fail': ending == 'fail'})},
            turn_running=False)
        if ending != 'old-client':
            await wait(lambda: any(row['type'] == 'extension_dialog' for row in events))
            event = next(row for row in events if row['type'] == 'extension_dialog')
            assert event['dialog']['operation'] == 'message'
            assert event['dialog']['host_message'] == (
                '[User-approved message requested by extension messagerequest]\n\n/quit')
            answer = {'id': event['dialog']['id'], 'request_id': event['request_id'],
                      'session_id': event['session_id'], 'scope': event['scope'], 'value': ending != 'deny'}
            with pytest.raises(ValueError):
                controller.answer({**answer, 'scope': 'foreign'})
            controller.answer(answer)
        await wait(delivery.is_set)
        assert not calls and not owner.messages
        if ending == 'withdraw':
            controller.negotiate({'extension_messages': 0})
            controller.negotiate({'extension_messages': 1})
        release.set()
        await asyncio.gather(controller.task, return_exceptions=True)
        await asyncio.sleep(0)
        if controller.prompt_task is not None:
            await controller.prompt_task
        if ending == 'apply':
            assert len(calls) == 1
            assert owner.messages[0].content[0].text == (
                '[User-approved message requested by extension messagerequest]\n\n/quit')
            assert events[-1]['type'] == 'extension_message_result' and events[-1]['settled']
            reply = next(row for row in events if row['type'] == 'extension_result')
            queued = next(row for row in events if row['type'] == 'extension_message_queued')
            assert events.index(reply) < events.index(queued)
        else:
            assert not calls and not owner.messages
        assert owner.permissions.mode == 'deny' and controller.control is None
    finally:
        release.set()
        await controller.aclose()
        await nav.aclose()
        await close(owner)


@pytest.mark.parametrize('ending', ['withdraw', 'abort', 'close'])
async def test_owned_message_pre_turn_cancellation_settles_without_borrowed_command_authority(tmp_path, ending,
                                                                                          monkeypatch):
    owner, _ = session(tmp_path, extra=('messagerequest',))
    entered, stopped = asyncio.Event(), asyncio.Event()
    events = []
    async def pre_turn(message, *, images=(), input_origin="user"):
        assert input_origin == "extension"
        entered.set()
        assert controller.control is None and controller.prompting and controller.busy
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()
    monkeypatch.setattr(owner, 'send_message', pre_turn)
    nav = RpcNavigation(owner, events.append)
    controller = RpcExtensions(owner, events.append, navigation=nav)
    controller.negotiate({'extension_messages': 1})
    try:
        controller.start({'request_id': 'owned', 'session_id': owner.session_id, 'action': 'command',
                          'name': 'ext-messagerequest-request', 'args': '{}'}, turn_running=False)
        await wait(lambda: any(row['type'] == 'extension_dialog' for row in events))
        event = next(row for row in events if row['type'] == 'extension_dialog')
        controller.answer({'id': event['dialog']['id'], 'request_id': event['request_id'],
                           'session_id': event['session_id'], 'scope': event['scope'], 'value': True})
        await wait(entered.is_set)
        with pytest.raises(RuntimeError):
            controller.start({'request_id': 'other', 'session_id': owner.session_id, 'action': 'list'},
                             turn_running=False)
        if ending == 'withdraw':
            controller.negotiate({'extension_messages': 0})
        elif ending == 'abort':
            controller.abort_prompt()
        else:
            await controller.aclose()
        await asyncio.gather(controller.prompt_task, return_exceptions=True)
        assert stopped.is_set() and not controller.prompting and not owner.messages
    finally:
        await controller.aclose()
        await nav.aclose()
        await close(owner)


@pytest.mark.parametrize('approve', [False, True])
def test_actual_cli_reviewed_message_has_provenance_and_keeps_tool_permissions(tmp_path, approve):
    import shutil
    from pathlib import Path

    from xdog.coding.core.session_manager import SessionManager

    from packages.coding.tests.test_rpc_children_cli import Peer
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/messagerequest',
                    root / 'messagerequest')
    home = tmp_path / 'home'
    home.mkdir()
    (home / 'settings.json').write_text(json.dumps({'extensions': ['messagerequest'], 'extension_dir': str(root)}))
    peer = Peer(tmp_path, write=True)
    try:
        peer.send({'type': 'rpc_hello', 'extension_messages': 1})
        capability = peer.read('rpc_capabilities')
        assert capability['extension_messages'] == 1 and capability['extension_navigation'] == 0
        peer.send({'type': 'extension_control', 'request_id': 'owned', 'session_id': peer.ready['session_id'],
                   'session_revision': peer.ready['session_revision'], 'action': 'command',
                   'name': 'ext-messagerequest-request', 'args': json.dumps({'text': '/quit child-write'})})
        event = peer.read('extension_dialog')
        assert event['dialog']['operation'] == 'message' and not peer.marker.exists()
        peer.send({'type': 'extension_dialog_response', 'id': event['dialog']['id'],
                   'request_id': event['request_id'], 'session_id': event['session_id'],
                   'scope': event['scope'], 'value': approve})
        reply = peer.read('extension_result')
        assert reply['success']
        assert json.loads(reply['output'].removeprefix('MESSAGE-QUEUED '))['queued'] is approve
        if approve:
            queued = peer.read('extension_message_queued')
            permission = peer.read('permission_request')
            assert not (tmp_path / 'cli-child-target').exists()  # Message review isn't tool consent.
            peer.send({'type': 'permission_response', 'id': permission['id'], 'decision': 'deny'})
            ended = peer.read('message_end')
            settled = peer.read('extension_message_result')
            assert settled['settled'] and settled['prompt_request_id'] == queued['prompt_request_id']
            assert peer.events.index(reply) < peer.events.index(queued) < peer.events.index(ended)
            saved = SessionManager(home / 'sessions').load_session(peer.ready['session_id'])
            assert saved.messages[0].content[0].text == (
                '[User-approved message requested by extension messagerequest]\n\n/quit child-write')
        else:
            assert not peer.marker.exists()
        assert not (tmp_path / 'cli-child-target').exists()
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


@pytest.mark.parametrize('ending', ['abort', 'eof', 'withdraw'])
def test_actual_cli_extension_message_cleans_owned_producer_on_abort_eof_or_withdrawal(tmp_path, ending):
    import shutil
    import time
    from pathlib import Path

    from packages.coding.tests.test_rpc_children_cli import Peer
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/messagerequest',
                    root / 'messagerequest')
    home = tmp_path / 'home'
    home.mkdir()
    (home / 'settings.json').write_text(json.dumps({'extensions': ['messagerequest'], 'extension_dir': str(root)}))
    peer = Peer(tmp_path)
    try:
        peer.send({'type': 'rpc_hello', 'extension_messages': 1})
        peer.read('rpc_capabilities')
        peer.send({'type': 'extension_control', 'request_id': 'owned', 'session_id': peer.ready['session_id'],
                   'session_revision': peer.ready['session_revision'], 'action': 'command',
                   'name': 'ext-messagerequest-request', 'args': json.dumps({'text': 'hold'})})
        event = peer.read('extension_dialog')
        peer.send({'type': 'extension_dialog_response', 'id': event['dialog']['id'],
                   'request_id': event['request_id'], 'session_id': event['session_id'],
                   'scope': event['scope'], 'value': True})
        peer.read('extension_message_queued')
        deadline = time.monotonic() + 6
        while not peer.marker.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        assert peer.marker.exists()
        peer.send({'type': 'status'})
        assert peer.read('status')['prompt_running']
        if ending == 'eof':
            peer.process.stdin.close()
            assert peer.process.wait(timeout=6) == 0
        else:
            peer.send({'type': 'abort'} if ending == 'abort' else
                      {'type': 'rpc_hello', 'extension_messages': 0})
            settled = peer.read('extension_message_result')
            assert settled['settled'] and settled['canceled']
            peer.send({'type': 'quit'})
            peer.read('quit_ack')
            assert peer.process.wait(timeout=6) == 0
        assert peer.marker.with_suffix('.settled').read_text() == 'producer-settled'
    finally:
        try:
            if peer.process.poll() is None and not peer.process.stdin.closed:
                peer.send({'type': 'quit'})
                peer.process.wait(timeout=6)
        finally:
            peer.close()


def test_actual_cli_extension_message_does_not_consume_owned_uploaded_image(tmp_path):
    import shutil
    from pathlib import Path

    from packages.coding.tests.test_rpc_children_cli import Peer
    from packages.coding.tests.test_rpc_image_uploads import chunk
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/messagerequest',
                    root / 'messagerequest')
    home = tmp_path / 'home'
    home.mkdir()
    (home / 'settings.json').write_text(json.dumps({'extensions': ['messagerequest'], 'extension_dir': str(root)}))
    peer = Peer(tmp_path, vision=True)
    try:
        peer.send(chunk(owner=peer.ready['session_id'], key='unsent'))
        assert peer.read('image_upload_result')['status'] == 'ready'
        peer.send({'type': 'rpc_hello', 'extension_messages': 1})
        peer.read('rpc_capabilities')
        peer.send({'type': 'extension_control', 'request_id': 'owned', 'session_id': peer.ready['session_id'],
                   'session_revision': peer.ready['session_revision'], 'action': 'command',
                   'name': 'ext-messagerequest-request', 'args': '{}'})
        event = peer.read('extension_dialog')
        peer.send({'type': 'extension_dialog_response', 'id': event['dialog']['id'],
                   'request_id': event['request_id'], 'session_id': event['session_id'],
                   'scope': event['scope'], 'value': True})
        assert peer.read('message_end')['content'][0]['text'] == 'RPC-OFFLINE-ANSWER'
        assert peer.read('extension_message_result')['settled']
        peer.send({'type': 'prompt', 'request_id': 'manual', 'session_id': peer.ready['session_id'],
                   'message': 'Explicitly send my image now', 'image_refs': ['unsent']})
        assert peer.read('message_end')['content'][0]['text'] == 'RPC-IMAGES-1'
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


async def test_message_cancel_before_coroutine_admission_reports_single_settlement(tmp_path):
    owner, calls = session(tmp_path)
    events = []
    nav = RpcNavigation(owner, events.append)
    controller = RpcExtensions(owner, events.append, navigation=nav)
    controller.negotiate({'extension_messages': 1})
    try:
        controller._start_message({'action': 'message', 'message': 'Never admitted',
                                   'messages_generation': controller.messages_generation},
                                  (owner.session_id, 'request', 'scope'), nav.revision)
        controller.abort_prompt()
        await asyncio.gather(controller.prompt_task, return_exceptions=True)
        outcomes = [event for event in events if event['type'] == 'extension_message_result']
        assert len(outcomes) == 1 and outcomes[0]['canceled'] and outcomes[0]['settled']
        assert not calls and not owner.messages
    finally:
        await controller.aclose()
        await nav.aclose()
        await close(owner)
