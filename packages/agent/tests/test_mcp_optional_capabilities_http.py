"""Owned actual HTTP refusal of retired Elicitation/Tasks, not vendor conformance."""
from __future__ import annotations

import pytest
from xdog.agent.mcp import McpError

from packages.agent.tests.test_mcp_sampling_http_retirement import SamplingHttpPeer, declarations, invoke, make_manager


@pytest.mark.parametrize('family', ['elicitation/create', 'tasks/list', 'tasks/get', 'tasks/result', 'tasks/cancel'])
@pytest.mark.parametrize('modern', [False, True])
async def test_actual_http_removed_family_never_runs_callback_or_creates_task_or_replays_post(tmp_path, family, modern):
    with SamplingHttpPeer('modern' if modern else 'legacy', family=family) as peer:
        manager = make_manager(peer, tmp_path)
        try:
            await manager.connect('owned')
            client = manager._clients['owned']
            assert not hasattr(client, 'reverse') and not hasattr(client.inputs, 'elicitation_handler')
            if modern:
                with pytest.raises(McpError, match='not declared'):
                    await invoke(manager, 'tools/call', 'retired')
            else:
                assert 'RETAINED-retired' in str(await invoke(manager, 'tools/call', 'retired'))
            operations = [row for row in peer.requests if row.get('method') == 'tools/call']
            assert len(operations) == 1 and 'inputResponses' not in operations[0]['params']
            replies = [row for row in peer.requests if row.get('id') == 'retired-sampling']
            if modern:
                assert not replies and peer.gets == 0
            else:
                assert len(replies) == 1 and replies[0]['error']['code'] == -32601
            assert all(not {'sampling', 'elicitation', 'tasks'} & declaration.keys()
                       for declaration in declarations(peer))
            assert not client.inputs._jobs
            assert 'RETAINED-safe' in str(await invoke(manager, 'tools/call', 'safe'))
            assert len([row for row in peer.requests if row.get('method') == 'tools/call']) == 2
        finally:
            await manager.aclose()
        assert not manager._thread.is_alive()
