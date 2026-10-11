"""Original command worker read-only RPC catalogs, independent from mutation/editor."""
from __future__ import annotations

import asyncio
import json

import pytest
from xdog.coding.modes.rpc.extensions import RpcExtensions
from xdog.coding.modes.rpc.navigation import RpcNavigation

from packages.coding.tests.test_rpc_extensions import close, session


@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('scope', ['sessions', 'tree'])
async def test_actual_worker_rpc_catalog_scope_and_independent_negotiation(tmp_path, enabled, scope):
    owner, calls = session(tmp_path, extra=('navigator',))
    owner._persist()
    target = owner.session_manager.create_session(model='offline/model', working_dir=tmp_path)
    target.name = 'CATALOG TARGET'
    owner.session_manager.save_session(target)
    events = []
    navigation = RpcNavigation(owner, events.append)
    controller = RpcExtensions(owner, events.append, navigation=navigation)
    controller.negotiate({'extension_session_catalog': int(enabled)})
    before = owner.session_id, owner.agent.messages, owner.permissions.mode
    try:
        controller.start({'request_id': 'catalog', 'session_id': owner.session_id, 'action': 'command',
            'name': 'ext-navigator-catalog', 'args': json.dumps({'scope': scope})}, turn_running=False)
        await controller.task
        output = next(row for row in events if row['type'] == 'extension_result')
        assert output['success'], output
        if enabled:
            assert output['output'].startswith('CATALOG-PAGE-')
            rows = owner.extensions.entries.for_extension('navigator')
            value = next(row['data'] for row in rows if row['kind'] == 'catalog')
            assert value['owner_session'] == owner.session_id and value['scope'] == scope
            if scope == 'sessions':
                assert any(row['session_id'] == target.session_id for row in value['entries'])
            assert len(value['revision']) == 64
        else:
            assert output['output'] == 'CATALOG-UNAVAILABLE'
        assert not controller.editor_enabled and not controller.navigation_enabled and not controller.name_enabled
        assert not any(row['type'] == 'extension_dialog' for row in events)
        assert before == (owner.session_id, owner.agent.messages, owner.permissions.mode) and not calls
    finally:
        await controller.aclose()
        await navigation.aclose()
        await close(owner)


async def test_catalog_withdrawal_while_disk_read_held_revokes_delivery_and_settles_worker(tmp_path, monkeypatch):
    import threading

    import xdog.coding.core.navigation as native
    owner, calls = session(tmp_path, extra=('navigator',))
    owner._persist()
    entered, release = threading.Event(), threading.Event()
    original = native.navigation_entries
    def held(*args):
        entered.set()
        assert release.wait(3)
        return original(*args)
    monkeypatch.setattr(native, 'navigation_entries', held)
    events = []
    navigation = RpcNavigation(owner, events.append)
    controller = RpcExtensions(owner, events.append, navigation=navigation)
    controller.negotiate({'extension_session_catalog': 1})
    try:
        controller.start({'request_id': 'held', 'session_id': owner.session_id, 'action': 'command',
            'name': 'ext-navigator-catalog', 'args': 'sessions'}, turn_running=False)
        assert await asyncio.to_thread(entered.wait, 2)
        controller.negotiate({'extension_session_catalog': 0})
        await asyncio.sleep(.01)
        assert not controller.task.done()
        release.set()
        await controller.task
        result = next(row for row in events if row['type'] == 'extension_result')
        assert result['success'] and result['output'] == 'CATALOG-UNAVAILABLE'
        assert not owner.extensions.entries.for_extension('navigator') and not calls
    finally:
        release.set()
        await controller.aclose()
        await navigation.aclose()
        await close(owner)


def test_actual_cli_catalog_capability_is_read_only_and_separate_from_editor_navigation(tmp_path):
    import shutil
    from pathlib import Path

    from packages.coding.tests.test_rpc_children_cli import Peer
    home = tmp_path / 'home'
    home.mkdir()
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/navigator', root / 'navigator')
    (home / 'settings.json').write_text(json.dumps({'extensions': ['navigator'], 'extension_dir': str(root)}))
    peer = Peer(tmp_path)
    try:
        peer.send({'type': 'rpc_hello', 'extension_session_catalog': 1})
        caps = peer.read('rpc_capabilities')
        assert caps['extension_session_catalog'] == 1 and caps['extension_navigation'] == caps['extension_editor'] == 0
        peer.send({'type': 'extension_control', 'request_id': 'catalog', 'session_id': peer.ready['session_id'],
                   'action': 'command', 'name': 'ext-navigator-catalog', 'args': 'sessions'})
        result = peer.read('extension_result')
        assert result['success'] and result['output'].startswith('CATALOG-PAGE-')
        assert not peer.marker.exists()
        peer.send({'type': 'quit'})
        peer.read('quit_ack')
        assert peer.process.wait(timeout=6) == 0
    finally:
        peer.close()


async def test_rpc_worker_catalog_pagination_revision_and_missing_tree_refusal(tmp_path):
    owner, calls = session(tmp_path, extra=('navigator',))
    owner._persist()
    for index in range(52):
        target = owner.session_manager.create_session(model='offline/model', working_dir=tmp_path)
        target.name = f'TARGET {index}'
        owner.session_manager.save_session(target)
    events = []
    navigation = RpcNavigation(owner, events.append)
    controller = RpcExtensions(owner, events.append, navigation=navigation)
    controller.negotiate({'extension_session_catalog': 1})
    async def command(rid, options):
        controller.start({'request_id': rid, 'session_id': owner.session_id, 'action': 'command',
            'name': 'ext-navigator-catalog', 'args': json.dumps(options)}, turn_running=False)
        await controller.task
        await asyncio.sleep(0)
        result = next(row for row in events if row['type'] == 'extension_result' and row['request_id'] == rid)
        assert result['success'], result
        return result
    def last_page():
        return [row['data'] for row in owner.extensions.entries.for_extension('navigator')
                if row['kind'] == 'catalog'][-1]
    try:
        await command('first', {'scope': 'sessions'})
        first = last_page()
        assert first['total'] == 53 and len(first['entries']) == 50 and first['has_more']
        await command('second', {'scope': 'sessions', 'offset': first['next_offset'], 'revision': first['revision']})
        second = last_page()
        assert len(second['entries']) == 3 and not second['has_more']
        assert not ({row['id'] for row in first['entries']} & {row['id'] for row in second['entries']})
        target = owner.session_manager.create_session(model='offline/model', working_dir=tmp_path)
        owner.session_manager.save_session(target)
        stale = await command('stale', {'scope': 'sessions', 'offset': 50, 'revision': first['revision']})
        assert stale['output'] == 'CATALOG-UNAVAILABLE'
        missing = await command('missing', {'scope': 'tree', 'session_id': 'missing-owned-target'})
        assert missing['output'] == 'CATALOG-UNAVAILABLE'
        assert not calls and not owner.agent.messages and owner.permissions.mode == 'deny'
        assert not controller.navigation_enabled and not controller.editor_enabled
    finally:
        await controller.aclose()
        await navigation.aclose()
        await close(owner)
