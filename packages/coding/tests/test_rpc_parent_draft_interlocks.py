"""Parent snapshot work cannot overlap host mutation via alternate RPC commands."""
from __future__ import annotations

import asyncio
import json
import queue
import threading
from unittest.mock import patch

import pytest
from xdog.coding.modes.rpc.rpc_mode import run_rpc_mode
from xdog.tui.child_draft_store import ChildDraftStore

from packages.coding.tests.test_rpc_extensions import close, session


@pytest.mark.parametrize('action', ['set_model', 'set_thinking', 'compact'])
async def test_actual_rpc_reader_host_mutation_refuses_while_parent_disk_work_owns_session(
    tmp_path, monkeypatch, action,
):
    owner, calls = session(tmp_path)
    entered, release = threading.Event(), threading.Event()
    mutations, events, pending = [], [], queue.Queue()
    original = ChildDraftStore.catalog
    def held(self):
        entered.set()
        assert release.wait(4)
        return original(self)
    monkeypatch.setattr(ChildDraftStore, 'catalog', held)
    monkeypatch.setattr(owner, 'set_model', lambda *_: mutations.append('set_model'))
    monkeypatch.setattr(owner, 'set_thinking_level', lambda *_: mutations.append('set_thinking'))
    async def compact(*_args, **_kwargs):
        mutations.append('compact')
    monkeypatch.setattr(owner, 'compact', compact)
    pending.put({'type': 'rpc_hello', 'parent_saved_drafts': 1})
    class Input:
        def readline(self, size=-1):
            packet = pending.get(timeout=6)
            if packet['type'] == action:
                assert entered.wait(3)
            return json.dumps(packet) + '\n'
    class Output:
        def write(self, text):
            for line in text.splitlines():
                if not line:
                    continue
                event = json.loads(line)
                events.append(event)
                if event['type'] == 'parent_draft_capabilities':
                    scope = {key: event[key] for key in ('session_id', 'session_revision',
                        'parent_draft_owner', 'parent_draft_generation')}
                    pending.put({'type': 'parent_draft_control', **scope, 'request_id': 'held', 'action': 'catalog'})
                    pending.put({'type': action, 'model': 'other', 'level': 'high'})
                elif event['type'] in {'ok', 'error'}:
                    release.set()
                elif event['type'] == 'parent_draft_result':
                    pending.put({'type': 'quit'})
            return len(text)
        def flush(self):
            pass
    try:
        with patch('xdog.coding.modes.rpc.rpc_mode.sys.stdin', Input()), patch(
            'xdog.coding.modes.rpc.rpc_mode.sys.stdout', Output(),
        ):
            async with asyncio.timeout(8):
                assert await run_rpc_mode(owner) == 0
        assert not mutations and not calls
        assert any(row['type'] == 'error' and 'running' in row['message'] for row in events)
        assert any(row['type'] == 'parent_draft_result' and row['success'] for row in events)
        assert owner.permissions.mode == 'deny' and not owner.agent.messages
    finally:
        release.set()
        await close(owner)
