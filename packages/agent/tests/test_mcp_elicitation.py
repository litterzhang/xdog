"""Inert obsolete-protocol fixture plus explicit Elicitation refusal tests."""
from __future__ import annotations

import pytest
from xdog.agent.mcp.client_capabilities import RootCapability, reverse_response


def form(**fields):
    return {'jsonrpc': '2.0', 'id': 'elicitation', 'method': 'elicitation/create', 'params': {
        'message': 'Enter reviewed data', 'requestedSchema': {'type': 'object', 'properties': {
            'name': {'type': 'string', 'minLength': 2},
            'count': {'type': 'integer', 'minimum': 0},
            'enabled': {'type': 'boolean'},
            'tags': {'type': 'array', 'items': {'type': 'string', 'enum': ['a', 'b']}}},
            'required': ['name']}, **fields}}


@pytest.mark.parametrize('fields', [{}, {'mode': 'url', 'url': 'https://inert.invalid/review'},
                                   {'task': {}},
                                   {'_meta': {'io.modelcontextprotocol/related-task': {'taskId': 'old'}}}])
def test_elicitation_is_unsupported_even_with_valid_form_or_task_metadata(fields):
    value = form(**fields)
    capability = RootCapability((('file:///explicit/root', 'Explicit'),))
    capability.active = True
    expected = {'error': {'code': -32601, 'message': 'Elicitation is not supported'}}
    assert reverse_response(value) == expected and capability.response(value) == expected
    assert capability.roots == capability.ceiling == (('file:///explicit/root', 'Explicit'),)
