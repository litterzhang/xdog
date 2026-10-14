"""Client-rendered custom frame protocol uses the existing painted broker completion gates."""
from __future__ import annotations

import asyncio

import pytest
from xdog.coding.modes.rpc.extensions import RpcExtensions

from packages.coding.tests.test_rpc_extensions import close, session


async def wait(predicate):
    async with asyncio.timeout(6):
        while not predicate():
            await asyncio.sleep(0.003)


def frame_input(dialog, generation, sequence, *, kind='resize', painted=-1, width=40, height=5, **options):
    return {'type': 'extension_custom_input', 'session_id': dialog['session_id'],
            'request_id': dialog['request_id'], 'scope': dialog['scope'], 'id': dialog['dialog']['id'],
            'custom_generation': generation, 'sequence': sequence,
            'event': {'kind': kind, 'width': width, 'height': height, 'painted': painted, 'generation': 1,
                      **({'key': 'enter', 'ctrl': False, 'alt': False, 'shift': False} if kind == 'key' else {}),
                      **options}}


@pytest.mark.parametrize('value', [True, False, None, '1', 2])
async def test_custom_capability_requires_exact_integer_and_preserves_dialog_flags(tmp_path, value):
    owner, _ = session(tmp_path)
    controller = RpcExtensions(owner, lambda _: None)
    try:
        controller.negotiate({'extension_dialogs': 1})
        with pytest.raises(ValueError):
            controller.negotiate({'extension_custom_views': value})
        assert not controller.custom_enabled and controller.dialogs_enabled
        controller.negotiate({'extension_custom_views': 1})
        assert controller.custom_enabled and controller.dialogs_enabled
    finally:
        await controller.aclose()
        await close(owner)
