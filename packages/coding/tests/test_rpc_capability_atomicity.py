"""Invalid mixed capability batches do not partially grant or revoke UI authority."""
from __future__ import annotations

import pytest
from xdog.coding.modes.rpc.extensions import CAPABILITIES, RpcExtensions

from packages.coding.tests.test_rpc_extensions import close, session


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
