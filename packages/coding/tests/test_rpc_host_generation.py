"""A consumed broker reply cannot survive capability withdrawal/re-enable before dispatch."""
from __future__ import annotations

import asyncio
import json
import threading

import pytest
from xdog.coding.modes.rpc.extensions import RpcExtensions
from xdog.coding.modes.rpc.navigation import RpcNavigation

from packages.coding.tests.test_rpc_extensions import close, session


@pytest.mark.parametrize('operation', ['editor', 'rename', 'navigate', 'compact'])
async def test_accepted_reply_cannot_cross_host_capability_generation(tmp_path, monkeypatch, operation):
    fixture, command, capability, args = {
        'compact': ('compactionrequest', 'request', 'extension_compaction', '{}'),
        'editor': ('editor', 'compose', 'extension_editor', ''),
        'rename': ('namer', 'rename', 'extension_session_name', json.dumps({'name': 'STALE NAME'})),
        'navigate': ('navigator', 'navigate', 'extension_navigation', json.dumps({'action': 'new'})),
    }[operation]
    owner, calls = session(tmp_path, extra=(fixture,))
    events = []
    navigation = RpcNavigation(owner, events.append)
    controller = RpcExtensions(owner, events.append, navigation=navigation)
    controller.negotiate({capability: 1})
    accepted, release = threading.Event(), threading.Event()
    original = owner.extension_dialogs.ask
    async def held(extension, dialog):
        value = await original(extension, dialog)
        if dialog.get('operation') in {'read', 'rename', 'navigate', 'compact'}:
            accepted.set()
            while not release.is_set():
                await asyncio.sleep(.002)
        return value
    monkeypatch.setattr(owner.extension_dialogs, 'ask', held)
    before = owner.session_id, owner.session_data.name, owner.agent.messages
    try:
        controller.start({'request_id': 'held', 'session_id': owner.session_id, 'action': 'command',
                          'name': f'ext-{fixture}-{command}', 'args': args}, turn_running=False)
        async with asyncio.timeout(5):
            while not any(row['type'] == 'extension_dialog' for row in events):
                await asyncio.sleep(.002)
        question = next(row for row in events if row['type'] == 'extension_dialog')
        value = ({'text': 'CLIENT DRAFT', 'cursor': 12, 'image_count': 0, 'revision': 'a' * 64}
                 if operation == 'editor' else True)
        controller.answer({'id': question['dialog']['id'], 'request_id': question['request_id'],
                           'session_id': question['session_id'], 'scope': question['scope'], 'value': value})
        assert await asyncio.to_thread(accepted.wait, 3)
        controller.negotiate({capability: 0})
        controller.negotiate({capability: 1})
        release.set()
        async with asyncio.timeout(5):
            while not controller.task.done():
                # Fail rather than waiting for the unsafe follow-up editor replace.
                assert not any(row['type'] == 'extension_dialog' and row['dialog'].get('operation') == 'replace'
                               for row in events)
                await asyncio.sleep(.002)
        await controller.task
        await asyncio.sleep(0)
        assert navigation.task is None and not controller.editor_revisions
        assert before == (owner.session_id, owner.session_data.name, owner.agent.messages)
        result = next(row for row in events if row['type'] == 'extension_result')
        assert result['success']
        assert ('UNAVAILABLE' in result['output'] if operation == 'editor' else
                'False' in result['output'] if operation == 'navigate' else
                '"queued": false' in result['output'] if operation == 'compact' else
                '"accepted": false' in result['output'])
        assert not calls and owner.permissions.mode == 'deny'
    finally:
        release.set()
        await controller.aclose()
        await navigation.aclose()
        await close(owner)
