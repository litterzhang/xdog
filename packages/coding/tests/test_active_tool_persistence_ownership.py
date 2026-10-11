"""Disk barriers exercise real command ownership, responsiveness and atomic receipt."""
from __future__ import annotations

import asyncio
import threading

import pytest
from xdog.agent import AgentTool
from xdog.coding.modes.rpc.extensions import RpcExtensions
from xdog.coding.modes.rpc.navigation import RpcNavigation

from packages.coding.tests.test_rpc_active_tools import wait
from packages.coding.tests.test_rpc_extensions import close, session


@pytest.mark.parametrize('ending', ['apply', 'cancel', 'withdraw', 'changed', 'failure', 'close', 'committed-cancel'])
async def test_rpc_disk_commit_retains_owner_and_never_blocks_reader(tmp_path, monkeypatch, ending):
    owner, calls = session(tmp_path, extra=('toolselector',))
    events = []
    navigation = RpcNavigation(owner, events.append)
    controller = RpcExtensions(owner, events.append, navigation=navigation)
    controller.negotiate({'extension_active_tools': 1})
    entered, release = threading.Event(), threading.Event()
    closing = None
    main_thread = threading.get_ident()
    try:
        await owner.initialize_extensions()
        owner._persist()
        path = owner.session_manager._session_path(owner.session_id)
        original_bytes = path.read_bytes()
        original_save = owner.session_manager.save_session
        def blocked_save(data, *, before_replace=None):
            assert threading.get_ident() != main_thread, 'Disk write blocked the RPC reader loop'
            def barrier():
                entered.set()
                assert release.wait(8), 'Test disk gate was not released'
                if ending == 'failure':
                    raise OSError('Injected storage failure')
                before_replace()
            if ending == 'committed-cancel':
                original_save(data, before_replace=before_replace)
                entered.set()
                assert release.wait(8), 'Post-commit gate was not released'
            else:
                original_save(data, before_replace=barrier)
        monkeypatch.setattr(owner.session_manager, 'save_session', blocked_save)
        packet = {'request_id': 'select', 'session_id': owner.session_id, 'action': 'command',
                  'name': 'ext-toolselector-select', 'args': '{"names":[]}'}
        controller.start(packet, turn_running=False)
        await wait(lambda: any(row['type'] == 'extension_dialog' for row in events))
        event = next(row for row in events if row['type'] == 'extension_dialog')
        controller.answer({'id': event['dialog']['id'], 'request_id': event['request_id'],
                           'session_id': event['session_id'], 'scope': event['scope'], 'value': True})
        await wait(entered.is_set)
        assert controller.busy
        assert any(row['type'] == 'extension_result' and row['success'] for row in events)
        assert not any(row['type'] == 'extension_active_tools_result' for row in events)
        assert owner.agent.active_tool_selection is None
        with pytest.raises(RuntimeError, match='idle owning'):
            controller.start({**packet, 'request_id': 'competing'}, turn_running=False)
        with pytest.raises(RuntimeError, match='owns'):
            navigation.start({'action': 'new', 'request_id': 'competing', 'expected_session': owner.session_id},
                             turn_running=False, extension_running=controller.busy)
        # These reader-side controls execute while real disk work is outstanding.
        if ending in {'cancel', 'committed-cancel'}:
            controller.cancel(packet)
        elif ending == 'withdraw':
            controller.negotiate({'extension_active_tools': 0})
        elif ending == 'changed':
            owner.agent.set_tools((*owner.agent.state.tools, AgentTool(name='new', parameters={})))
        elif ending == 'close':
            closing = asyncio.create_task(controller.aclose())
        await asyncio.sleep(.02)
        assert controller.busy
        if closing is not None:
            assert not closing.done()
        release.set()
        await asyncio.wait_for(asyncio.shield(controller.task), 6)
        if closing is not None:
            await closing
        applied = ending in {'apply', 'committed-cancel'}
        assert owner.agent.active_tool_selection == (() if applied else None)
        assert owner.session_manager.load_session(owner.session_id).active_tools == (() if applied else None)
        if not applied:
            assert path.read_bytes() == original_bytes
        if ending != 'close':
            receipt = next(row for row in events if row['type'] == 'extension_active_tools_result')
            assert receipt['applied'] is applied
        assert not list(path.parent.glob('.session-*.tmp'))
        assert not calls and owner.permissions.mode == 'deny'
    finally:
        release.set()
        if closing is not None:
            await closing
        await controller.aclose()
        await navigation.aclose()
        await close(owner)
