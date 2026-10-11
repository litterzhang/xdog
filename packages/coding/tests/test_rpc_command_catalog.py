"""Negotiated real client menu metadata, not a server guess or command execution grant."""
from __future__ import annotations

import asyncio
import json

import pytest
from xdog.agent.extensions.command_catalog import page
from xdog.coding.modes.rpc.extensions import RpcExtensions
from xdog.coding.modes.rpc.navigation import RpcNavigation

from packages.coding.tests.test_rpc_extensions import close, session


async def wait(predicate):
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(.002)


@pytest.mark.parametrize('enabled', [False, True])
async def test_actual_worker_reads_client_menu_pages_without_other_grants_or_execution(tmp_path, enabled):
    owner, calls = session(tmp_path, extra=('commandreader',))
    events = []
    navigation = RpcNavigation(owner, events.append)
    controller = RpcExtensions(owner, events.append, navigation=navigation)
    controller.negotiate({'extension_command_catalog': int(enabled)})
    # Deliberately not the server's native commands; only the client knows this menu.
    menu = {f'/client-item{index:02}': f'Client metadata {index}' for index in range(40)}
    try:
        controller.start({'request_id': 'menu', 'session_id': owner.session_id, 'action': 'command',
            'name': 'ext-commandreader-inspect', 'args': ''}, turn_running=False)
        answered = set()
        async with asyncio.timeout(8):
            while not controller.task.done():
                for event in list(events):
                    if event['type'] != 'extension_dialog' or event['dialog']['id'] in answered:
                        continue
                    dialog = event['dialog']
                    assert dialog['kind'] == 'session' and dialog['operation'] == 'commands'
                    value = page(menu, dialog, session_id=owner.session_id, owner='real-client')
                    packet = {'id': dialog['id'], 'request_id': event['request_id'],
                              'session_id': event['session_id'], 'scope': event['scope'], 'value': value}
                    with pytest.raises(ValueError, match='another session'):
                        controller.answer({**packet, 'value': {**value, 'session_id': 'foreign'}})
                    controller.answer(packet)
                    answered.add(dialog['id'])
                await asyncio.sleep(.002)
        await controller.task
        result = next(row for row in events if row['type'] == 'extension_result')
        assert result['success'], result
        if enabled:
            payload = json.loads(result['output'].removeprefix('COMMAND-MENU '))
            assert payload['count'] == 40 and payload['executed'] == 0
            assert set(payload['names']) == set(menu) and len(answered) == 3
        else:
            assert result['output'] == 'COMMAND-MENU-UNAVAILABLE' and not answered
        assert not calls and not owner.agent.messages and owner.permissions.mode == 'deny'
        assert not controller.editor_enabled and not controller.navigation_enabled and not controller.name_enabled
    finally:
        await controller.aclose()
        await navigation.aclose()
        await close(owner)


async def test_command_menu_withdrawal_refuses_late_client_reply(tmp_path):
    owner, calls = session(tmp_path, extra=('commandreader',))
    events = []
    navigation = RpcNavigation(owner, events.append)
    controller = RpcExtensions(owner, events.append, navigation=navigation)
    controller.negotiate({'extension_command_catalog': 1})
    try:
        controller.start({'request_id': 'menu', 'session_id': owner.session_id, 'action': 'command',
            'name': 'ext-commandreader-inspect', 'args': ''}, turn_running=False)
        await wait(lambda: any(row['type'] == 'extension_dialog' for row in events))
        event = next(row for row in events if row['type'] == 'extension_dialog')
        controller.negotiate({'extension_command_catalog': 0})
        await controller.task
        value = page({'/client': 'Real client command'}, event['dialog'], session_id=owner.session_id, owner='client')
        with pytest.raises(ValueError, match='owner'):
            controller.answer({'id': event['dialog']['id'], 'request_id': event['request_id'],
                'session_id': event['session_id'], 'scope': event['scope'], 'value': value})
        assert next(row for row in events if row['type'] == 'extension_result')['output'] == 'COMMAND-MENU-UNAVAILABLE'
        assert not calls
    finally:
        await controller.aclose()
        await navigation.aclose()
        await close(owner)


def test_actual_cli_command_menu_is_client_sourced_and_has_no_execution_grant(tmp_path):
    import shutil
    from pathlib import Path

    from packages.coding.tests.test_rpc_children_cli import Peer
    home = tmp_path / 'home'
    home.mkdir()
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/commandreader', root / 'commandreader')
    (home / 'settings.json').write_text(json.dumps({'extensions': ['commandreader'], 'extension_dir': str(root)}))
    peer = Peer(tmp_path)
    menu = {'/client-only': 'Only this client displays this command'}
    try:
        assert peer.ready['capabilities']['extension_command_catalog'] == 1
        peer.send({'type': 'rpc_hello', 'extension_command_catalog': 1})
        caps = peer.read('rpc_capabilities')
        assert caps['extension_command_catalog'] == 1 and caps['extension_navigation'] == caps['extension_editor'] == 0
        peer.send({'type': 'extension_control', 'request_id': 'menu', 'session_id': peer.ready['session_id'],
                   'action': 'command', 'name': 'ext-commandreader-inspect', 'args': ''})
        for _ in range(2):  # Original page and independent mutation-isolation reread.
            event = peer.read('extension_dialog')
            assert event['dialog']['operation'] == 'commands'
            value = page(menu, event['dialog'], session_id=event['session_id'], owner='actual-cli-client')
            peer.send({'type': 'extension_dialog_response', 'id': event['dialog']['id'],
                'request_id': event['request_id'], 'session_id': event['session_id'], 'scope': event['scope'],
                'value': value})
        result = peer.read('extension_result')
        assert result['success'], result
        payload = json.loads(result['output'].removeprefix('COMMAND-MENU '))
        assert payload['names'] == ['/client-only'] and payload['executed'] == 0
        assert not peer.marker.exists()
        peer.send({'type': 'quit'})
        peer.read('quit_ack')
        assert peer.process.wait(timeout=6) == 0
    finally:
        peer.close()
