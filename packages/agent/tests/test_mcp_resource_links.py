from __future__ import annotations

import json

import pytest
from xdog.agent.mcp.context import McpDocument, prompt_blocks
from xdog.agent.mcp.stdio import McpError
from xdog.agent.mcp.subscriptions import SubscriptionError

LINK = {'type': 'resource_link', 'name': 'document', 'title': 'Untrusted title',
        'uri': 'https://must-not-fetch.invalid/path', 'mimeType': 'text/plain', 'size': 123,
        'description': 'untrusted linked data', 'annotations': {'audience': ['user']},
        '_meta': {'vendor/opaque': {'value': 'full metadata retained'}}}


def test_prompt_link_complete_metadata_survives_preview_export_and_explicit_draft_without_fetching():
    blocks = prompt_blocks([{'role': 'user', 'content': LINK}, {'role': 'assistant', 'content': LINK}])
    assert [block.role for block in blocks] == ['user', 'assistant']
    for block in blocks:
        assert json.loads(block.text.split('\n', 1)[1]) == LINK
        assert block.uri == LINK['uri'] and not block.data
    document = McpDocument('peer', 'prompt', 'review', blocks)
    assert 'NOT fetched' in document.text and 'vendor/opaque' in document.draft_text()
    assert 'User-selected untrusted MCP prompt' in document.draft_text()


@pytest.mark.parametrize('updates', [
    {'uri': None}, {'uri': ''}, {'uri': 'test://path\0private'}, {'uri': 'x' * 8193},
    {'name': ''}, {'name': True}, {'name': 'x' * 257}, {'title': []}, {'description': 1},
    {'mimeType': False}, {'size': True}, {'size': -1}, {'size': 2**53},
    {'_meta': {'private': float('nan')}}, {'_meta': {'private': object()}},
    {'_meta': {'private': '\ud800'}},
])
def test_invalid_link_metadata_is_not_coerced_dropped_or_promoted_to_content(updates):
    with pytest.raises((McpError, SubscriptionError)):
        prompt_blocks([{'role': 'user', 'content': {**LINK, **updates}}])


def test_link_metadata_budget_and_prompt_role_are_still_authoritative():
    with pytest.raises(McpError):
        prompt_blocks([{'role': 'system', 'content': LINK}])
    with pytest.raises(McpError, match='retention limit'):
        prompt_blocks([{'role': 'user', 'content': {**LINK, '_meta': {'private': 'x' * (1024 * 1024)}}}])
