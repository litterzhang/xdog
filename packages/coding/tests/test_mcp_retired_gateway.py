"""Removed optional capability packets never create sessions or borrow a viewer."""
from __future__ import annotations

import pytest

from packages.claw.tests.test_mcp_browser_owner import cold_gateway


@pytest.mark.parametrize('kind', ['mcp_elicitation_control', 'mcp_roots_control'])
@pytest.mark.parametrize('action', ['enable', 'answer', 'prepare', 'commit'])
async def test_retired_optional_control_refused_before_any_agent_or_provider_work(tmp_path, kind, action):
    async with cold_gateway(tmp_path) as (gateway, send, adapter):
        response = await send({'type': kind, 'action': action, 'request_id': 'retired', 'server': 'none',
            'group_id': 'main', 'session_id': '', 'view': 'b' * 32, 'tasks': True})
        assert response['type'] == 'error'
        assert not adapter.calls
        runtime = gateway._orchestrator._runtimes['main']
        assert runtime._active_session is None
        assert not hasattr(runtime.mcp, 'register_elicitation')
        assert not hasattr(runtime.mcp, 'manage_roots')
        assert not runtime.mcp.tools()


async def test_ping_does_not_negotiate_removed_optional_protocols(tmp_path):
    async with cold_gateway(tmp_path) as (gateway, send, adapter):
        response = await send({'type': 'ping', 'mcp_elicitation': 1, 'mcp_roots': 1,
                              'mcp_private_key_jwt': 1, 'mcp_registration': 1, 'mcp_client_auth': 1})
        assert response['type'] == 'pong'
        assert not {'mcp_elicitation', 'mcp_roots', 'mcp_private_key_jwt',
                    'mcp_registration', 'mcp_client_auth'} & response.keys()
        assert not adapter.calls and gateway._orchestrator._runtimes['main']._active_session is None
