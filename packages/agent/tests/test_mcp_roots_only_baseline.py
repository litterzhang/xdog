"""Root-only transport without optional handlers or mutable review APIs."""
from __future__ import annotations

import pytest
from xdog.agent.mcp import McpError, McpRegistry

from packages.agent.tests.test_mcp_sampling_http_retirement import (
    METHODS,
    ROOTS,
    STATE,
    SamplingHttpPeer,
    declarations,
    invoke,
)


@pytest.mark.parametrize('method', METHODS)
@pytest.mark.parametrize('sse', [False, True])
@pytest.mark.parametrize('configured', [False, True])
async def test_modern_roots_only_never_depends_on_optional_review_or_task_registration(
    tmp_path, method, sse, configured,
):
    with SamplingHttpPeer('retained', sse=sse) as peer:
        manager = McpRegistry({'owned': {
            'transport': 'http', 'url': peer.url, 'timeout': 2,
            **({'roots': ROOTS} if configured else {}),
        }}, tmp_path)
        try:
            # Do not register Elicitation/Sampling/Tasks just to make Roots work.
            await manager.connect('owned')
            client = manager._clients['owned']
            assert not hasattr(manager, '_elicitation')
            assert not hasattr(client.inputs, 'elicitation_handler') and not hasattr(client, 'reverse')
            assert client.inputs.advertised() == ({'roots': {}} if configured else {})
            if configured:
                assert 'RETAINED-roots' in str(await invoke(manager, method, 'roots'))
            else:
                with pytest.raises(McpError, match='not declared'):
                    await invoke(manager, method, 'roots')
            calls = [row for row in peer.requests if row.get('method') == method]
            assert len(calls) == (2 if configured else 1)
            assert 'inputResponses' not in calls[0]['params'] and 'requestState' not in calls[0]['params']
            if configured:
                original, resumed = calls[0]['params'], calls[1]['params']
                assert calls[0]['id'] != calls[1]['id']
                assert resumed['inputResponses'] == {'review': {'roots': ROOTS}}
                assert resumed['requestState'] == STATE
                assert {key: value for key, value in resumed.items()
                        if key not in {'inputResponses', 'requestState'}} == original
            assert all(not ({'sampling', 'elicitation', 'tasks'} & declaration.keys())
                       for declaration in declarations(peer))
            assert peer.gets == 0 and not client.inputs._jobs
            # No failed or completed root lookup can poison the ordinary channel.
            assert 'RETAINED-safe' in str(await invoke(manager, method, 'safe'))
        finally:
            await manager.aclose()
        assert not manager._thread.is_alive()
