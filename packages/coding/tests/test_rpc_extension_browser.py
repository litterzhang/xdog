"""Command-scoped deferred browser requests, never session activation or editor replacement."""
from __future__ import annotations

import asyncio
import json

import pytest
from xdog.coding.modes.rpc.extensions import RpcExtensions
from xdog.coding.modes.rpc.navigation import RpcNavigation

from packages.coding.tests.test_rpc_extensions import close, session


@pytest.mark.parametrize('mode', ['sessions', 'tree', 'fail', 'old-client'])
async def test_actual_worker_browser_request_only_after_successful_command(tmp_path, mode):
    owner, calls = session(tmp_path, extra=('navigator',))
    events = []
    navigation = RpcNavigation(owner, events.append)
    controller = RpcExtensions(owner, events.append, navigation=navigation)
    controller.negotiate({'extension_session_browser': int(mode != 'old-client'), 'extension_session_catalog': 1})
    before = owner.session_id, owner.agent.messages, owner.permissions.mode
    try:
        controller.start({'request_id': 'browse', 'session_id': owner.session_id, 'action': 'command',
                          'name': 'ext-navigator-browse', 'args': mode}, turn_running=False)
        await controller.task
        await asyncio.sleep(0)
        opened = [row for row in events if row['type'] == 'extension_browser_requested']
        result = next(row for row in events if row['type'] == 'extension_result')
        if mode in {'sessions', 'tree'}:
            assert result['success'] and result['output'] == 'NAVIGATION-QUEUED-True'
            assert len(opened) == 1 and opened[0]['browser_scope'] == mode
            assert opened[0]['session_id'] == owner.session_id
            assert opened[0]['session_revision'] == navigation.revision
            assert events.index(result) < events.index(opened[0])
        else:
            assert not opened
            assert (not result['success'] if mode == 'fail' else result['output'] == 'NAVIGATION-QUEUED-False')
        assert not controller.editor_enabled and not controller.navigation_enabled and not controller.name_enabled
        assert navigation.task is None and not calls
        assert before == (owner.session_id, owner.agent.messages, owner.permissions.mode)
        assert owner.extensions.catalog_handler is None
    finally:
        await controller.aclose()
        await navigation.aclose()
        await close(owner)


@pytest.mark.parametrize('change', ['withdraw', 'reenable', 'revision', 'cancel'])
async def test_deferred_browser_rechecks_original_command_owner_before_delivery(tmp_path, change, monkeypatch):
    owner, calls = session(tmp_path, extra=('navigator',))
    events = []
    navigation = RpcNavigation(owner, events.append)
    controller = RpcExtensions(owner, events.append, navigation=navigation)
    controller.negotiate({'extension_session_browser': 1})
    # Hold actual command after worker callback has queued its request, before the
    # host declares callback success. No test directly populates deferred_browser.
    original = owner.extension_command
    entered, release = asyncio.Event(), asyncio.Event()
    async def held(*args):
        result = await original(*args)
        entered.set()
        await release.wait()
        return result
    monkeypatch.setattr(owner, 'extension_command', held)
    try:
        controller.start({'request_id': 'held', 'session_id': owner.session_id, 'action': 'command',
                          'name': 'ext-navigator-browse', 'args': 'tree'}, turn_running=False)
        await asyncio.wait_for(entered.wait(), 5)
        assert controller.deferred_browser is not None
        assert not any(row['type'] == 'extension_browser_requested' for row in events)
        if change in {'withdraw', 'reenable'}:
            controller.negotiate({'extension_session_browser': 0})
            if change == 'reenable':
                controller.negotiate({'extension_session_browser': 1})
        elif change == 'revision':
            navigation.refresh_revision()
        else:
            controller.cancel({'request_id': 'held', 'session_id': owner.session_id})
        release.set()
        await controller.task
        await asyncio.sleep(0)
        assert not any(row['type'] == 'extension_browser_requested' for row in events)
        assert navigation.task is None and not calls and not owner.agent.messages
    finally:
        release.set()
        await controller.aclose()
        await navigation.aclose()
        await close(owner)


@pytest.mark.parametrize('browser_scope', ['sessions', 'tree'])
def test_actual_cli_browser_request_drives_client_read_without_session_activation(tmp_path, browser_scope):
    import shutil
    from pathlib import Path

    from packages.coding.tests.test_rpc_children_cli import Peer
    home = tmp_path / 'home'
    home.mkdir()
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/navigator', root / 'navigator')
    (home / 'settings.json').write_text(json.dumps(
        {'extensions': ['navigator'], 'extension_dir': str(root)}))
    peer = Peer(tmp_path)
    try:
        peer.send({'type': 'rpc_hello', 'extension_session_browser': 1})
        caps = peer.read('rpc_capabilities')
        assert caps['extension_session_browser'] == 1
        assert caps['extension_navigation'] == caps['extension_editor'] == 0
        peer.send({'type': 'extension_control', 'request_id': 'browse', 'session_id': peer.ready['session_id'],
                   'action': 'command', 'name': 'ext-navigator-browse', 'args': browser_scope})
        result = peer.read('extension_result')
        opened = peer.read('extension_browser_requested')
        assert result['success'] and opened['request_id'] == 'browse'
        assert opened['browser_scope'] == browser_scope and opened['browser_generation'] == caps['browser_generation']
        assert peer.events.index(result) < peer.events.index(opened)
        # The remote client fetches metadata to populate its browser; no implicit
        # session_control new/resume/goto/branch command accompanies the event.
        peer.send({'type': 'session_control', 'request_id': 'browser-read',
                   'action': 'list' if browser_scope == 'sessions' else 'tree',
                   'expected_session': opened['session_id'], 'session_revision': opened['session_revision']})
        page = peer.read('session_result')
        assert page['success'] and page['catalog']['owner_session'] == opened['session_id']
        peer.send({'type': 'status'})
        status = peer.read('status')
        assert status['session_id'] == peer.ready['session_id'] and status['messages'] == 0
        assert status['permission_mode'] == 'deny' and not peer.marker.exists()
        peer.send({'type': 'quit'})
        peer.read('quit_ack')
        assert peer.process.wait(timeout=6) == 0
    finally:
        peer.close()


async def test_failed_command_result_delivery_cannot_emit_a_browser_request(tmp_path):
    owner, calls = session(tmp_path, extra=('navigator',))
    events = []
    def emit(row):
        if row['type'] == 'extension_result':
            raise OSError('Closed client output')
        events.append(row)
    navigation = RpcNavigation(owner, emit)
    controller = RpcExtensions(owner, emit, navigation=navigation)
    controller.negotiate({'extension_session_browser': 1})
    try:
        controller.start({'request_id': 'browse', 'session_id': owner.session_id, 'action': 'command',
                          'name': 'ext-navigator-browse', 'args': 'sessions'}, turn_running=False)
        with pytest.raises(OSError):
            await controller.task
        await asyncio.sleep(0)
        assert controller.deferred_browser is None
        assert not any(row['type'] == 'extension_browser_requested' for row in events)
        assert not calls and navigation.task is None
    finally:
        await controller.aclose()
        await navigation.aclose()
        await close(owner)
