"""Invalid mixed capability batches do not partially grant or revoke UI authority."""
from __future__ import annotations

import pytest
from xdog.coding.modes.rpc.extensions import CAPABILITIES, RpcExtensions

from packages.coding.tests.test_rpc_custom_views import frame_input, wait
from packages.coding.tests.test_rpc_extensions import close, packet, response, session


@pytest.mark.parametrize('invalid', list(CAPABILITIES))
async def test_invalid_extension_batch_has_no_grant_generation_or_receipt_side_effects(tmp_path, invalid):
    owner, calls = session(tmp_path)
    events = []
    controller = RpcExtensions(owner, events.append)
    try:
        before = dict(vars(controller))
        batch = dict.fromkeys(CAPABILITIES, 1)
        batch[invalid] = True
        with pytest.raises(ValueError):
            controller.negotiate(batch)
        assert vars(controller) == before
        assert not events and not calls and not owner.messages
    finally:
        await controller.aclose()
        await close(owner)


async def test_invalid_withdrawal_batch_preserves_actual_live_custom_frame_and_broker(tmp_path):
    owner, calls = session(tmp_path, extra=('custom',))
    events = []
    controller = RpcExtensions(owner, events.append)
    try:
        controller.negotiate({'extension_custom_views': 1})
        controller.start(packet(owner, 'view', 'ext-custom-view'), turn_running=False)
        await wait(lambda: any(event['type'] == 'extension_dialog' for event in events))
        dialog = next(event for event in events if event['type'] == 'extension_dialog')
        controller.custom_input(frame_input(dialog, dialog['custom_generation'], 1))
        await wait(lambda: controller.custom_frames)
        frame = dict(controller.custom_frames)
        generation, count = controller.custom_generation, len(events)
        with pytest.raises(ValueError):
            controller.negotiate({'extension_custom_views': 0, 'extension_contributions': True})
        assert controller.custom_enabled and controller.custom_generation == generation
        assert controller.custom_frames == frame and len(events) == count
        assert owner.extension_dialogs.contains(dialog['dialog']['id'])
        controller.answer(response(dialog, None))
        await controller.task
        assert not calls and owner.permissions.mode == 'deny'
    finally:
        await controller.aclose()
        await close(owner)


@pytest.mark.parametrize('invalid', ['child_controls', 'child_custom_views', 'transient_events'])
def test_actual_cli_cross_controller_invalid_batch_cannot_grant_custom_views(tmp_path, invalid):
    import json
    import shutil
    from pathlib import Path

    from packages.coding.tests.test_rpc_children_cli import Peer
    home = tmp_path / 'home'
    home.mkdir()
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/custom', root / 'custom')
    (home / 'settings.json').write_text(json.dumps({'extensions': ['custom'], 'extension_dir': str(root)}))
    peer = Peer(tmp_path)
    try:
        peer.send({'type': 'rpc_hello', 'extension_custom_views': 1, invalid: True})
        assert peer.read('error')['action'] == 'rpc_hello'
        peer.send({'type': 'extension_control', 'request_id': 'probe', 'session_id': peer.ready['session_id'],
                   'action': 'command', 'name': 'ext-custom-view', 'args': ''})
        result = peer.read('extension_result')
        assert result['success'] and json.loads(result['output'])['value'] is None
        assert not any(event['type'] in {'extension_dialog', 'extension_custom_capabilities'} for event in peer.events)
        assert not peer.marker.exists()
        peer.send({'type': 'quit'})
        assert peer.process.wait(timeout=5) == 0
    finally:
        peer.close()


def test_actual_cli_invalid_cross_controller_withdrawal_preserves_live_view_and_frame(tmp_path):
    import json
    import shutil
    from pathlib import Path

    from packages.coding.tests.test_rpc_children_cli import Peer
    home = tmp_path / 'home'
    home.mkdir()
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/custom', root / 'custom')
    (home / 'settings.json').write_text(json.dumps({'extensions': ['custom'], 'extension_dir': str(root)}))
    peer = Peer(tmp_path)
    try:
        peer.send({'type': 'rpc_hello', 'extension_custom_views': 1})
        peer.read('extension_custom_capabilities')
        peer.send({'type': 'extension_control', 'request_id': 'view', 'session_id': peer.ready['session_id'],
                   'action': 'command', 'name': 'ext-custom-view', 'args': ''})
        dialog = peer.read('extension_dialog')
        generation = dialog['custom_generation']
        peer.send(frame_input(dialog, generation, 1))
        first = peer.read('extension_custom_frame')
        peer.send({'type': 'rpc_hello', 'extension_custom_views': 0, 'child_overlay_controls': True})
        assert peer.read('error')['action'] == 'rpc_hello'
        peer.send(frame_input(dialog, generation, 2, kind='key', painted=first['revision']))
        result = peer.read('extension_result')
        assert result['success']
        assert json.loads(result['output'])['value'] == {'selected': False, 'count': 0, 'mode': None}
        peer.send({'type': 'status'})
        status = peer.read('status')
        assert status['messages'] == 0 and status['permission_mode'] == 'deny' and not peer.marker.exists()
        peer.send({'type': 'quit'})
        assert peer.process.wait(timeout=5) == 0
    finally:
        peer.close()


@pytest.mark.parametrize('capability', ['extension_command_catalog', 'extension_session_catalog',
                                       'extension_session_name', 'extension_session_browser', 'extension_active_tools'])
async def test_independent_host_capability_does_not_withdraw_existing_editor_navigation_dialogs(tmp_path, capability):
    from xdog.coding.modes.rpc.navigation import RpcNavigation
    owner, calls = session(tmp_path)
    events = []
    navigation = RpcNavigation(owner, events.append)
    controller = RpcExtensions(owner, events.append, navigation=navigation)
    try:
        controller.negotiate({'extension_editor': 1, 'extension_navigation': 1,
                              'extension_dialogs': 1, 'extension_forms': 1, 'extension_multi_forms': 1})
        controller.editor_revisions['original-extension'] = 'original-receipt'
        controller.negotiate({capability: 1})
        assert controller.editor_enabled and controller.navigation_enabled and controller.dialogs_enabled
        assert controller.forms_enabled and controller.multi_forms_enabled
        assert controller.editor_revisions == {'original-extension': 'original-receipt'}
        controller.negotiate({capability: 0})
        assert controller.editor_enabled and controller.navigation_enabled and controller.dialogs_enabled
        assert controller.forms_enabled and controller.multi_forms_enabled
        assert controller.editor_revisions == {'original-extension': 'original-receipt'}
        assert not calls
    finally:
        await controller.aclose()
        await navigation.aclose()
        await close(owner)


async def test_independent_host_families_preserve_each_other_and_legacy_batch_withdrawal(tmp_path):
    from xdog.coding.modes.rpc.navigation import RpcNavigation
    owner, calls = session(tmp_path)
    events = []
    navigation = RpcNavigation(owner, events.append)
    controller = RpcExtensions(owner, events.append, navigation=navigation)
    try:
        controller.negotiate({'extension_session_name': 1})
        controller.negotiate({'extension_session_catalog': 1})
        controller.negotiate({'extension_command_catalog': 1})
        assert controller.name_enabled and controller.catalog_enabled and controller.menu_enabled
        controller.negotiate({'extension_session_catalog': 0})
        assert controller.name_enabled and not controller.catalog_enabled and controller.menu_enabled
        controller.negotiate({'extension_editor': 1, 'extension_navigation': 1})
        controller.negotiate({'extension_editor': 0})
        assert not controller.editor_enabled and not controller.navigation_enabled  # Old batch semantics retained.
        assert controller.name_enabled and controller.menu_enabled
        before = (controller.name_enabled, controller.menu_generation, controller.menu_enabled,
                  controller.editor_enabled, controller.catalog_generation)
        with pytest.raises(ValueError):
            controller.negotiate({'extension_command_catalog': 0, 'extension_session_name': True})
        assert before == (controller.name_enabled, controller.menu_generation, controller.menu_enabled,
                          controller.editor_enabled, controller.catalog_generation)
        assert not calls
    finally:
        await controller.aclose()
        await navigation.aclose()
        await close(owner)


async def test_host_generations_change_only_on_effective_grant_transitions(tmp_path):
    from xdog.coding.modes.rpc.navigation import RpcNavigation
    owner, calls = session(tmp_path)
    navigation = RpcNavigation(owner, lambda _: None)
    controller = RpcExtensions(owner, lambda _: None, navigation=navigation)
    try:
        flags = {'extension_editor': 1, 'extension_navigation': 1, 'extension_session_name': 1}
        controller.negotiate(flags)
        before = controller.editor_generation, controller.navigation_generation, controller.name_generation
        controller.negotiate(flags)
        assert before == (controller.editor_generation, controller.navigation_generation, controller.name_generation)
        controller.negotiate({'extension_command_catalog': 1})
        assert before == (controller.editor_generation, controller.navigation_generation, controller.name_generation)
        controller.negotiate({'extension_session_name': 0})
        assert controller.name_generation == before[2] + 1
        assert (controller.editor_generation, controller.navigation_generation) == before[:2]
        controller.negotiate({'extension_editor': 0})
        assert controller.editor_generation == before[0] + 1
        assert controller.navigation_generation == before[1] + 1  # Preserved legacy omitted-field withdrawal.
        assert not calls
    finally:
        await controller.aclose()
        await navigation.aclose()
        await close(owner)
