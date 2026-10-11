"""Independent remote overlay control never bypasses nested input or painted proof."""
from __future__ import annotations

import json

import pytest
from xdog.coding.modes.rpc.extensions import RpcExtensions

from packages.coding.tests.test_rpc_custom_views import frame_input, wait
from packages.coding.tests.test_rpc_extensions import close, packet, session


async def test_actual_worker_overlay_controls_and_current_frame_input(tmp_path):
    owner, calls = session(tmp_path, extra=('overlaymock',))
    events = []
    controller = RpcExtensions(owner, events.append)
    try:
        controller.negotiate({'extension_custom_views': 1, 'extension_overlay_controls': 1})
        controller.start({**packet(owner, 'overlay', 'ext-overlaymock-view'), 'args': 'auto'}, turn_running=False)
        await wait(lambda: any(event['type'] == 'extension_dialog' for event in events))
        dialog = next(event for event in events if event['type'] == 'extension_dialog')
        generation = dialog['custom_generation']
        controller.custom_input(frame_input(dialog, generation, 1))
        await wait(lambda: controller.custom_frames)
        first = controller.custom_frames[dialog['dialog']['id']]
        with pytest.raises(ValueError, match='non-capturing'):
            controller.custom_input(frame_input(dialog, generation, 2, kind='key', painted=first['revision']))
        await wait(lambda: any(event['type'] == 'extension_custom_control' and event['state']['hidden']
                              for event in events))
        with pytest.raises(ValueError):
            controller.custom_input(frame_input(dialog, generation, 3, kind='key', painted=first['revision']))
        await wait(lambda: any(event['type'] == 'extension_custom_control' and event['state']['capturing']
                              for event in events))
        await wait(lambda: dialog['dialog']['id'] in controller.custom_frames)
        current = controller.custom_frames[dialog['dialog']['id']]
        # Control revocation requires a newer geometry generation even after a refresh.
        request = frame_input(dialog, generation, 4)
        request['event']['generation'] = current['generation'] + 1
        controller.custom_input(request)
        await wait(lambda: dialog['dialog']['id'] in controller.custom_frames
                   and controller.custom_frames[dialog['dialog']['id']]['revision'] > current['revision'])
        fresh = controller.custom_frames[dialog['dialog']['id']]
        request = frame_input(dialog, generation, 5, kind='key', painted=fresh['revision'])
        request['event']['generation'] = fresh['generation']
        controller.custom_input(request)
        await controller.task
        result = next(event for event in events if event['type'] == 'extension_result')
        assert result['success'] and json.loads(result['output'])['value']['inputs'] == ['enter']
        assert not owner.messages and not calls and owner.permissions.mode == 'deny'
    finally:
        await controller.aclose()
        await close(owner)


@pytest.mark.parametrize('allowed', [False, True])
async def test_overlay_capability_is_independent_and_withdrawal_cancels_owned_view(tmp_path, allowed):
    owner, calls = session(tmp_path, extra=('overlaymock',))
    events = []
    controller = RpcExtensions(owner, events.append)
    try:
        controller.negotiate({'extension_custom_views': 1})
        if allowed:
            controller.negotiate({'extension_overlay_controls': 1})
        controller.start(packet(owner, 'overlay', 'ext-overlaymock-view'), turn_running=False)
        if allowed:
            await wait(lambda: any(event['type'] == 'extension_dialog' for event in events))
            dialog = next(event for event in events if event['type'] == 'extension_dialog')
            controller.negotiate({'extension_overlay_controls': 0})
            await controller.task
            with pytest.raises(ValueError):
                controller.custom_input(frame_input(dialog, dialog['custom_generation'], 1))
        else:
            await controller.task
            assert not any(event['type'] == 'extension_dialog' for event in events)
        assert not owner.extension_dialogs.custom.views and not calls
    finally:
        await controller.aclose()
        await close(owner)


def test_actual_cli_overlay_hide_show_focus_requires_new_current_frame(tmp_path):
    import shutil
    from pathlib import Path

    from packages.coding.tests.test_rpc_children_cli import Peer
    home = tmp_path / 'home'
    home.mkdir()
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/overlaymock', root / 'overlaymock')
    (home / 'settings.json').write_text(json.dumps({'extensions': ['overlaymock'], 'extension_dir': str(root)}))
    peer = Peer(tmp_path)
    try:
        peer.send({'type': 'rpc_hello', 'extension_custom_views': 1, 'extension_overlay_controls': 1})
        assert peer.read('extension_overlay_capabilities')['extension_overlay_controls'] == 1
        peer.read('extension_custom_capabilities')
        peer.send({'type': 'extension_control', 'request_id': 'overlay', 'session_id': peer.ready['session_id'],
                   'action': 'command', 'name': 'ext-overlaymock-view', 'args': 'auto'})
        dialog = peer.read('extension_dialog')
        generation = dialog['custom_generation']
        peer.send(frame_input(dialog, generation, 1))
        first = peer.read('extension_custom_frame')
        peer.send(frame_input(dialog, generation, 2, kind='key', painted=first['revision']))
        assert peer.read('error')['action'] == 'extension_custom_input'
        hidden = peer.read('extension_custom_control')
        assert hidden['state']['hidden'] is True
        shown = peer.read('extension_custom_control')
        assert shown['state']['hidden'] is False and shown['state']['capturing'] is False
        focused = peer.read('extension_custom_control')
        assert focused['state']['capturing'] is True
        # Earlier background frames remain buffered. Filter by generation after
        # sending explicit new geometry, rather than selecting a stale refresh.
        request = frame_input(dialog, generation, 3)
        request['event']['generation'] = 2
        peer.send(request)
        fresh = peer.read('extension_custom_frame')
        while fresh['generation'] != 2:
            fresh = peer.read('extension_custom_frame')
        request = frame_input(dialog, generation, 4, kind='key', painted=fresh['revision'])
        request['event']['generation'] = 2
        peer.send(request)
        result = peer.read('extension_result')
        assert result['success'] and json.loads(result['output'])['value']['inputs'] == ['enter']
        peer.send({'type': 'status'})
        status = peer.read('status')
        assert status['messages'] == 0 and status['permission_mode'] == 'deny' and not peer.marker.exists()
        peer.send({'type': 'quit'})
        assert peer.process.wait(timeout=5) == 0
    finally:
        peer.close()


@pytest.mark.parametrize('value', [True, False, None, '1', 2])
async def test_overlay_controls_require_exact_integer_without_changing_existing_ui_flags(tmp_path, value):
    owner, _ = session(tmp_path)
    controller = RpcExtensions(owner, lambda _: None)
    try:
        controller.negotiate({'extension_custom_views': 1, 'extension_dialogs': 1})
        with pytest.raises(ValueError):
            controller.negotiate({'extension_overlay_controls': value})
        assert controller.custom_enabled and controller.dialogs_enabled and not controller.overlay_enabled
    finally:
        await controller.aclose()
        await close(owner)
