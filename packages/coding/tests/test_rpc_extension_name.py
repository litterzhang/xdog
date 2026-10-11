"""Actual worker rename consent queues original native metadata CAS after callback success."""
from __future__ import annotations

import asyncio
import json

import pytest
from xdog.coding.modes.rpc.extensions import RpcExtensions
from xdog.coding.modes.rpc.navigation import RpcNavigation

from packages.coding.tests.test_rpc_extensions import close, session


async def wait(predicate):
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(.002)


@pytest.mark.parametrize('ending', ['apply', 'deny', 'callback-fail', 'withdraw', 'name-changed', 'old-client'])
async def test_rpc_worker_name_review_and_native_cas_no_hidden_or_failed_mutation(tmp_path, ending):
    owner, calls = session(tmp_path, extra=('namer',))
    events = []
    navigation = RpcNavigation(owner, events.append)
    controller = RpcExtensions(owner, events.append, navigation=navigation)
    if ending != 'old-client':
        controller.negotiate({'extension_session_name': 1})
    else:
        controller.negotiate({'extension_navigation': 1, 'extension_editor': 1})
    before = owner.session_data.name
    original_messages = owner.agent.messages
    try:
        controller.start({'request_id': 'name', 'session_id': owner.session_id, 'action': 'command',
            'name': 'ext-namer-rename', 'args': json.dumps({'name': 'RPC REVIEWED NAME',
                                                          'fail': ending == 'callback-fail'})}, turn_running=False)
        if ending == 'old-client':
            await controller.task
            assert not any(row.get('type') == 'extension_dialog' for row in events)
            assert owner.session_data.name == before
            return
        await wait(lambda: any(row.get('type') == 'extension_dialog' for row in events))
        question = next(row for row in events if row.get('type') == 'extension_dialog')
        assert question['dialog']['operation'] == 'rename'
        assert question['dialog']['expected_name'] == before
        assert owner.session_data.name == before and not calls
        response = {'id': question['dialog']['id'], 'request_id': question['request_id'],
            'session_id': question['session_id'], 'scope': question['scope'], 'value': ending != 'deny'}
        with pytest.raises(ValueError, match='foreign'):
            controller.answer({**response, 'scope': 'foreign'})
        if ending == 'withdraw':
            controller.negotiate({'extension_session_name': 0})
        else:
            if ending == 'name-changed':
                owner.rename_session('OTHER OPERATOR NAME')
            controller.answer(response)
        await controller.task
        await asyncio.sleep(0)  # Actual deferred control done callback, not direct host naming in test.
        if navigation.task is not None:
            await navigation.task
        expected = ('RPC REVIEWED NAME' if ending == 'apply' else 'OTHER OPERATOR NAME'
                    if ending == 'name-changed' else before)
        assert owner.session_data.name == expected
        if ending == 'apply':
            assert owner.session_manager.load_session(owner.session_id).name == expected
            assert any(row.get('type') == 'session_result' and row['success'] for row in events)
            result = next(row for row in events if row.get('type') == 'extension_result')
            assert '"before": ""' in result['output'] and '"after": ""' in result['output']
        assert not calls and owner.agent.messages is original_messages and owner.permissions.mode == 'deny'
    finally:
        await controller.aclose()
        await navigation.aclose()
        await close(owner)


def test_actual_cli_independent_name_negotiation_review_and_native_persistence(tmp_path):
    import shutil
    from pathlib import Path

    from xdog.coding.core.session_manager import SessionManager

    from packages.coding.tests.test_rpc_children_cli import Peer
    home = tmp_path / 'home'
    home.mkdir()
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/namer', root / 'namer')
    (home / 'settings.json').write_text(json.dumps({'extensions': ['namer'], 'extension_dir': str(root)}))
    peer = Peer(tmp_path)
    try:
        assert peer.ready['capabilities']['extension_session_name'] == 1
        peer.send({'type': 'rpc_hello', 'extension_session_name': 1})
        caps = peer.read('rpc_capabilities')
        assert caps['extension_session_name'] == 1 and caps['extension_navigation'] == 0
        peer.send({'type': 'extension_control', 'request_id': 'rename', 'session_id': peer.ready['session_id'],
                   'action': 'command', 'name': 'ext-namer-rename', 'args': json.dumps({'name': 'CLI REVIEWED'})})
        question = peer.read('extension_dialog')
        assert question['dialog']['operation'] == 'rename' and not peer.marker.exists()
        peer.send({'type': 'extension_dialog_response', 'id': question['dialog']['id'],
                   'request_id': question['request_id'], 'session_id': question['session_id'],
                   'scope': question['scope'], 'value': True})
        result = peer.read('extension_result')
        assert result['success'] and 'NAME-QUEUED' in result['output']
        persisted = peer.read('session_result')
        assert persisted['success'] and persisted['action'] == 'rename'
        assert SessionManager(home / 'sessions').load_session(peer.ready['session_id']).name == 'CLI REVIEWED'
        assert not peer.marker.exists()
        peer.send({'type': 'quit'})
        peer.read('quit_ack')
        assert peer.process.wait(timeout=6) == 0
    finally:
        peer.close()


async def test_rpc_name_review_cancellation_cannot_queue_native_mutation(tmp_path):
    owner, calls = session(tmp_path, extra=('namer',))
    events = []
    navigation = RpcNavigation(owner, events.append)
    controller = RpcExtensions(owner, events.append, navigation=navigation)
    controller.negotiate({'extension_session_name': 1})
    try:
        controller.start({'request_id': 'cancel', 'session_id': owner.session_id, 'action': 'command',
            'name': 'ext-namer-rename', 'args': json.dumps({'name': 'NEVER APPLY'})}, turn_running=False)
        await wait(lambda: any(row.get('type') == 'extension_dialog' for row in events))
        question = next(row for row in events if row.get('type') == 'extension_dialog')
        controller.cancel({'request_id': 'cancel', 'session_id': owner.session_id})
        await controller.task
        await asyncio.sleep(0)
        with pytest.raises(ValueError, match='owner'):
            controller.answer({'id': question['dialog']['id'], 'request_id': question['request_id'],
                'session_id': question['session_id'], 'scope': question['scope'], 'value': True})
        assert navigation.task is None and owner.session_data.name == '' and not calls
    finally:
        await controller.aclose()
        await navigation.aclose()
        await close(owner)
