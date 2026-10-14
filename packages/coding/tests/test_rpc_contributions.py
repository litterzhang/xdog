"""Real worker declarative UI contributions: opt-in, scoped, bounded, no model input."""
from __future__ import annotations

import asyncio

import pytest
from xdog.coding.modes.rpc.extensions import RpcExtensions

from packages.coding.tests.test_rpc_extensions import close, session


@pytest.mark.parametrize('change', ['withdraw', 'reenable', 'session', 'close'])
async def test_queued_contributions_refuse_late_scope_or_generation_without_clearing_dialog_grants(tmp_path, change):
    owner, _ = session(tmp_path)
    events = []
    controller = RpcExtensions(owner, events.append)
    try:
        controller.negotiate({'extension_dialogs': 1, 'extension_forms': 1})
        controller.negotiate({'extension_contributions': 1})
        assert controller.dialogs_enabled and controller.forms_enabled
        async with controller.bind('owned'):
            controller._publish({'type': 'extension_notice', 'text': 'token=PRIVATE',
                                 'snapshot': {'ui': {'safe': {'lines': ['PUBLIC']}}}})
            if change == 'withdraw':
                controller.negotiate({'extension_contributions': 0})
            elif change == 'reenable':
                controller.negotiate({'extension_contributions': 0})
                controller.negotiate({'extension_contributions': 1})
            elif change == 'session':
                owner.session_data.session_id = 'changed'
            else:
                controller.closed = True
            await asyncio.sleep(0)
        assert not any(event['type'] == 'extension_contribution' for event in events)
        assert not any(event['type'] in {'extension_notice', 'extension_ui'} for event in events)
    finally:
        await controller.aclose()
        await close(owner)
