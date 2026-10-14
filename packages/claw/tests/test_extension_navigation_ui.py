from __future__ import annotations

from pathlib import Path

import pytest
from xdog.claw.channels.tui.tui_app import ChatApp

FIXTURE = Path(__file__).parents[2] / "agent/tests/fixtures/extensions"


@pytest.mark.parametrize('phase', ['before', 'after'])
@pytest.mark.parametrize('reason', ['cancel', 'withdraw'])
def test_local_cancel_or_capability_withdrawal_revokes_actual_review_even_if_callback_succeeds(phase, reason):
    ui = ChatApp('/tmp/unused-revocation.sock')
    sent = []
    ui._navigate_session = lambda *args: sent.append(args)
    try:
        ui._gateway_session_id = 'session'
        ui._connection_ready = True
        ui._extension_navigation_supported = True
        ui._extension_cancel_supported = True
        ui._extension_pending = 'owner', 'session'
        ui._extension_editor_owner = 'owner', 'navigator'
        item = {'id': 'dialog', 'extension': 'navigator', 'kind': 'session', 'operation': 'navigate',
                'target': {'action': 'new', 'session_id': '', 'branch_id': '', 'index': -1}}
        assert ui._queue_extension_navigation(item, True)
        def receipt():
            ui._handle_response({'type': 'extension_dialog_accepted', 'id': 'dialog',
                'extension': 'navigator', 'operation': 'navigate', 'request_id': 'owner', 'group_id': ui.group_id})
        if phase == 'after':
            receipt()
            assert ui._extension_navigation is not None
        if reason == 'cancel':
            ui._cancel_extension_command()
        else:
            ui._extension_navigation_supported = False
        if phase == 'before':
            receipt()
        assert not ui._finish_extension_navigation('owner', True)
        assert not sent and ui._extension_navigation is None and ui._extension_navigation_review is None
    finally:
        ui._extension_pending = None
        ui._request_exit()
