"""Actual owned hook workers, complete oversized context, decisions and immutable preparation."""
from __future__ import annotations

import base64

import pytest
from xdog.agent.extensions.compaction import (
    CompactionDecision,
    chosen_boundary,
    decision,
    record,
    saved_record,
)
from xdog.ai.types import (
    AssistantMessage,
    ImageContent,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)


def source():
    return [UserMessage(content=(TextContent(text='original user'),
                                ImageContent(data=base64.b64encode(b'x' * (7 * 1024 * 1024)).decode(),
                                             mime_type='image/png'))),
        AssistantMessage(content=(ThinkingContent(thinking='signed', thinking_signature='continuity'),
                                  ToolCall(id='call', name='read', arguments={'path': 'owned.py'}))),
        ToolResultMessage(tool_call_id='call', tool_name='read', content=(TextContent(text='tool result'),)),
        AssistantMessage(content=(TextContent(text='old answer'),)), UserMessage(content='second user'),
        AssistantMessage(content=(TextContent(text='second answer'),)), UserMessage(content='latest user'),
        AssistantMessage(content=(TextContent(text='latest answer'),))]


@pytest.mark.parametrize('value', [True, [], {'cancel': False}, {'cancel': True, 'summary': 'no'},
    {'summary': ''}, {'summary': 'x' * (256 * 1024 + 1)}, {'summary': 'valid', 'first_kept_index': True},
    {'summary': 'valid', 'details': float('nan')}, {'summary': 'valid', 'details': 'x' * 65537},
    {'summary': 'valid', 'permission_mode': 'allow-all'}])
def test_invalid_compaction_results_fail_closed(value):
    with pytest.raises(ValueError):
        decision(value)


def test_custom_boundary_cannot_orphan_tools_or_drop_latest_user_turn() -> None:

    def _check_case(cut):
        messages = [
            UserMessage(content="old"),
            AssistantMessage(content=(ToolCall(id="a", name="read"),)),
            ToolResultMessage(tool_call_id="a", tool_name="read"),
            AssistantMessage(),
            UserMessage(content="older"),
            AssistantMessage(),
            UserMessage(content="latest"),
            AssistantMessage(),
        ]
        with pytest.raises(ValueError):
            chosen_boundary(CompactionDecision(summary="summary", first_kept_index=cut), messages, 4)

    for cut in [0, 1, 2, 3, 5, 7, 8, 9, True]:
        _check_case(cut)


def test_record_roundtrip_copy_and_invalid_saved_metadata():
    details = {'durable': ['context']}
    metadata = record(CompactionDecision(summary='summary', details=details, source='compactor'), 4, 8)
    details['durable'].append('not committed')
    assert metadata['details'] == {'durable': ['context']}
    assert saved_record(metadata) == metadata
    for invalid in ({}, {'source': 'compactor'}, {**metadata, 'first_kept_index': 8},
                    {**metadata, 'details': float('inf')}, {**metadata, 'original_count': True}):
        with pytest.raises(ValueError):
            saved_record(invalid)
