"""Retired extension message/context grants cannot be renegotiated or replayed."""
from __future__ import annotations

import pytest

from packages.claw.tests.test_mcp_browser_owner import cold_gateway


@pytest.mark.parametrize('token', ['extension_message_token', 'extension_context_token',
                                 'extension_message_generation', 'extension_context_generation'])
async def test_retired_message_grant_never_becomes_user_input_or_creates_owner(tmp_path, token):
    async with cold_gateway(tmp_path) as (gateway, send, adapter):
        pong = await send({'type': 'ping', 'group_id': 'main', 'extension_message': 1,
                          'extension_context_messages': 1, 'extension_editor_write': 1})
        assert pong['type'] == 'pong'
        assert not {'extension_message', 'extension_context_messages', 'extension_editor_write'}.intersection(pong)
        response = await send({'type': 'message', 'group_id': 'main', 'content': 'MUST NOT BE SENT', token: 'old'})
        assert response['type'] == 'error' and 'removed' in response['message']
        runtime = gateway._orchestrator._runtimes['main']
        assert runtime._active_session is None and not adapter.calls
        assert not gateway._orchestrator.is_group_running('main')
