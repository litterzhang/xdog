"""Task correlation is directional metadata, not a host-consent inheritance token."""
from __future__ import annotations

import asyncio

import pytest
from xdog.agent.mcp.client_capabilities import reverse_response
from xdog.agent.mcp.reverse_requests import ReverseRequests
from xdog.agent.mcp.task_registry import TaskRegistry

from packages.agent.tests.test_mcp_sampling_owner import request


@pytest.mark.parametrize('family', ['sampling/createMessage', 'elicitation/create'])
@pytest.mark.parametrize('identity', ['foreign', 'original'])
@pytest.mark.parametrize('creation', [False, True])
async def test_related_metadata_cannot_attach_new_operation_to_even_a_current_local_task(family, identity, creation):
    registry, reverse, calls = TaskRegistry(), ReverseRequests(), []
    reverse.active, reverse.tasks = True, registry
    async def held(identifier):
        await asyncio.Future()
    local = registry.create(family, held)['task']['taskId']
    async def handler(*args):
        calls.append(args)
        raise AssertionError('No approval inherited from related-task metadata')
    reverse.handler = reverse.elicitation_handler = handler
    reverse.sampling_task_handler = reverse.elicitation_task_handler = handler
    source = request()
    source['method'] = family
    source['params']['_meta'] = {'io.modelcontextprotocol/related-task': {
        'taskId': local if identity == 'original' else 'foreign'}}
    if creation:
        source['params']['task'] = {}
    try:
        assert not reverse.admit(source, handler, timeout=1)
        assert reverse_response(source)['error']['code'] == -32602
        assert not calls and registry.state(local)['status'] == 'working'
    finally:
        await reverse.close()
