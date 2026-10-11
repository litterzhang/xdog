"""Actual owned hook workers, complete oversized context, decisions and immutable preparation."""
from __future__ import annotations

import base64
import copy
import json
import shutil
from pathlib import Path

import pytest
from xdog.agent.extensions.compaction import (
    CompactionDecision,
    chosen_boundary,
    decision,
    record,
    saved_record,
)
from xdog.agent.extensions.runtime import ExtensionRuntime
from xdog.agent.messages import messages_to_dicts
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


async def test_real_worker_full_oversized_preparation_immutable_siblings_disposal_and_capability(tmp_path):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parent / 'fixtures/extensions', root)
    runtime = ExtensionRuntime(['compactor'], root, tmp_path,
                               context=lambda: {'session_id': 'owner', 'model': 'selected'})
    messages = source()
    prepared = {'messages': messages_to_dicts(messages), 'original_count': len(messages), 'first_kept_index': 4,
        'automatic': False, 'instructions': 'retain evidence', 'previous': None, 'tokens_before': 100,
        'context_limit': 20000}
    original = copy.deepcopy(prepared)
    try:
        chosen = await runtime.before_compaction(prepared)
        assert chosen.summary == 'CUSTOM-COMPACTION-SUMMARY' and chosen.source == 'compactor'
        assert chosen_boundary(chosen, messages, 4) == 4
        assert prepared == original and 'signed' in str(original['messages'][1])
        observed = json.loads(await runtime.command('ext-compactor-status', ''))
        assert observed['image_bytes'] > 8 * 1024 * 1024 and observed['prepared_count'] == 8
        assert observed['sibling_saw_original'] and observed['no_borrowed_authority']
        assert observed['after'] == 0  # A decision receipt is not a committed-history event.
        client = runtime._manager._clients['compactor']
        process = client.process
        await runtime.command('ext-compactor-dispose', '')
        assert (await runtime.before_compaction(prepared)).summary is None
        assert json.loads(await runtime.command('ext-compactor-status', ''))['before'] == 1
        assert client.process is process and process.returncode is None
        # Re-register using a fresh worker, then deny a worker without the actual
        # owned operation version before transferring/invoking any hook.
        await runtime.manage('reload', 'compactor')
        runtime._infos['compactor']['compaction_hooks'] = 0
        with pytest.raises(ValueError, match='original history retained'):
            await runtime.before_compaction(prepared)
        assert 'compactor' in runtime._failed
    finally:
        await runtime.aclose()


@pytest.mark.parametrize('value', [True, [], {'cancel': False}, {'cancel': True, 'summary': 'no'},
    {'summary': ''}, {'summary': 'x' * (256 * 1024 + 1)}, {'summary': 'valid', 'first_kept_index': True},
    {'summary': 'valid', 'details': float('nan')}, {'summary': 'valid', 'details': 'x' * 65537},
    {'summary': 'valid', 'permission_mode': 'allow-all'}])
def test_invalid_compaction_results_fail_closed(value):
    with pytest.raises(ValueError):
        decision(value)


@pytest.mark.parametrize('cut', [0, 1, 2, 3, 5, 7, 8, 9, True])
def test_custom_boundary_cannot_orphan_tools_or_drop_latest_user_turn(cut):
    messages = [UserMessage(content='old'), AssistantMessage(content=(ToolCall(id='a', name='read'),)),
        ToolResultMessage(tool_call_id='a', tool_name='read'), AssistantMessage(), UserMessage(content='older'),
        AssistantMessage(), UserMessage(content='latest'), AssistantMessage()]
    with pytest.raises(ValueError):
        chosen_boundary(CompactionDecision(summary='summary', first_kept_index=cut), messages, 4)


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
