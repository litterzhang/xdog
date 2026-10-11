"""Reviewed host work cannot proceed after command-result failure or cancellation."""
from __future__ import annotations

import asyncio
import json

import pytest
from xdog.coding.modes.rpc.extensions import RpcExtensions
from xdog.coding.modes.rpc.navigation import RpcNavigation

from packages.coding.tests.test_rpc_extensions import close, session


@pytest.mark.parametrize('operation', ['navigate', 'rename'])
async def test_result_emission_failure_discards_reviewed_native_operation(tmp_path, operation):
    fixture = 'navigator' if operation == 'navigate' else 'namer'
    owner, calls = session(tmp_path, extra=(fixture,))
    events = []
    def emit(row):
        if row['type'] == 'extension_result':
            raise OSError('Closed command-result sink')
        events.append(row)
    navigation = RpcNavigation(owner, events.append)
    controller = RpcExtensions(owner, emit, navigation=navigation)
    controller.negotiate({'extension_navigation': 1} if operation == 'navigate' else {'extension_session_name': 1})
    before = owner.session_id, owner.session_data.name, owner.agent.messages
    try:
        controller.start({'request_id': 'reviewed', 'session_id': owner.session_id, 'action': 'command',
                          'name': f'ext-{fixture}-{operation}',
                          'args': json.dumps({'action': 'new'} if operation == 'navigate'
                                             else {'name': 'NOT APPLIED'})},
                         turn_running=False)
        async with asyncio.timeout(5):
            while not any(row['type'] == 'extension_dialog' for row in events):
                await asyncio.sleep(.002)
        question = next(row for row in events if row['type'] == 'extension_dialog')
        controller.answer({'id': question['dialog']['id'], 'request_id': question['request_id'],
                           'session_id': question['session_id'], 'scope': question['scope'], 'value': True})
        with pytest.raises(OSError):
            await controller.task
        await asyncio.sleep(0)
        assert navigation.task is None
        assert not any(row['type'] == 'extension_navigation_queued' for row in events)
        assert controller.deferred_navigation is None and not calls
        assert before == (owner.session_id, owner.session_data.name, owner.agent.messages)
        assert owner.permissions.mode == 'deny'
    finally:
        await controller.aclose()
        await navigation.aclose()
        await close(owner)


@pytest.mark.parametrize('operation', ['navigate', 'rename', 'browse'])
@pytest.mark.parametrize('ending', ['delivered', 'write-failed', 'cancel'])
async def test_async_command_result_delivery_holds_native_admission_until_receipt(tmp_path, operation, ending):
    fixture = 'namer' if operation == 'rename' else 'navigator'
    owner, calls = session(tmp_path, extra=(fixture,))
    events = []
    writing, release = asyncio.Event(), asyncio.Event()
    async def send(row):
        assert row['type'] == 'extension_result' and row['success']
        writing.set()
        await release.wait()
        if ending == 'write-failed':
            raise OSError('Private output error')
        events.append(row)
    navigation = RpcNavigation(owner, events.append)
    controller = RpcExtensions(owner, events.append, navigation=navigation, send=send)
    capability = {'navigate': 'extension_navigation', 'rename': 'extension_session_name',
                  'browse': 'extension_session_browser'}[operation]
    controller.negotiate({capability: 1})
    original_id, original_messages = owner.session_id, owner.agent.messages
    options = (json.dumps({'name': 'REVIEWED'}) if operation == 'rename' else
               json.dumps({'action': 'new'}) if operation == 'navigate' else 'sessions')
    try:
        controller.start({'request_id': 'operation', 'session_id': original_id, 'action': 'command',
                          'name': f'ext-{fixture}-{operation}', 'args': options}, turn_running=False)
        if operation != 'browse':
            async with asyncio.timeout(5):
                while not any(row['type'] == 'extension_dialog' for row in events):
                    await asyncio.sleep(.002)
            question = next(row for row in events if row['type'] == 'extension_dialog')
            controller.answer({'id': question['dialog']['id'], 'request_id': question['request_id'],
                'session_id': question['session_id'], 'scope': question['scope'], 'value': True})
        await asyncio.wait_for(writing.wait(), 5)
        assert controller.busy and navigation.task is None
        assert not any(row['type'] == 'extension_browser_requested' for row in events)
        assert owner.session_id == original_id and owner.session_data.name == ''
        assert owner.agent.messages is original_messages
        if ending == 'cancel':
            controller.cancel({'request_id': 'operation', 'session_id': original_id})
        release.set()
        if ending == 'delivered':
            await controller.task
        else:
            with pytest.raises(OSError if ending == 'write-failed' else asyncio.CancelledError):
                await controller.task
        await asyncio.sleep(0)
        if ending == 'delivered':
            if operation == 'browse':
                assert navigation.task is None
                assert sum(row['type'] == 'extension_browser_requested' for row in events) == 1
            else:
                assert navigation.task is not None
                await navigation.task
                assert any(row['type'] == 'session_result' and row['success'] for row in events)
                assert (owner.session_data.name == 'REVIEWED' if operation == 'rename'
                        else owner.session_id != original_id)
        else:
            assert navigation.task is None and controller.deferred_navigation is None
            assert controller.deferred_browser is None and owner.session_id == original_id
            assert owner.session_data.name == '' and owner.agent.messages is original_messages
            assert not any(row['type'] in {'extension_browser_requested', 'extension_navigation_queued'}
                           for row in events)
        assert not calls and owner.permissions.mode == 'deny'
    finally:
        release.set()
        await controller.aclose()
        await navigation.aclose()
        await close(owner)
