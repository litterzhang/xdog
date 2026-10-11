"""Scoped disclosure projection never implicitly shares unrelated turns/signatures/tools."""
from __future__ import annotations

import json

import pytest
from xdog.agent.mcp.sampling_context import project
from xdog.ai.types import AssistantMessage, TextContent, ThinkingContent, ToolCall, ToolResultMessage, UserMessage
from xdog.tui.utils import redact_sensitive_text


def turn(alias, key, text):
    return (UserMessage(content=text),
        AssistantMessage(content=(ToolCall(id=key, name=alias,
            arguments={'api_key': 'PRIVATE KEY', 'query': 'public'}),)),
        ToolResultMessage(tool_call_id=key, tool_name=alias, content=(TextContent(text='RESULT'),)),
        AssistantMessage(content=(TextContent(text='DONE'), ThinkingContent(thinking='PRIVATE REASONING',
                                                                           thinking_signature='PRIVATE SIGNATURE'))))


def test_scoped_complete_turns_redact_credentials_and_never_borrow_unrelated_local_history():
    aliases = {'one_alias': ('one', 'lookup'), 'two_alias': ('two', 'search')}
    history = (*turn('one_alias', '1', 'ONE PUBLIC'), *turn('two_alias', '2', 'TWO PUBLIC'),
               *turn('localtool', '3', 'LOCAL PRIVATE'), UserMessage(content='UNFINISHED'),
               AssistantMessage(content=(ToolCall(id='4', name='one_alias', arguments={}),)))
    one = project(history, aliases, 'one', 'thisServer', redact_sensitive_text)
    serialized = json.dumps(one)
    assert 'ONE PUBLIC' in serialized and 'lookup' in serialized
    assert all(text not in serialized for text in ['TWO PUBLIC', 'LOCAL PRIVATE', 'UNFINISHED', 'PRIVATE KEY',
                                                  'PRIVATE REASONING', 'PRIVATE SIGNATURE'])
    assert '<redacted>' in serialized
    all_servers = project(history, aliases, 'one', 'allServers', redact_sensitive_text)
    assert 'TWO PUBLIC' in str(all_servers) and 'LOCAL PRIVATE' not in str(all_servers)
    assert project(history, {}, 'one', 'thisServer', redact_sensitive_text) == []


def test_projection_is_independent_copy_and_bounds_instead_of_truncating():
    history = turn('alias', '1', 'PUBLIC')
    aliases = {'alias': ('one', 'lookup')}
    rows = project(history, aliases, 'one', 'thisServer', redact_sensitive_text)
    rows[0]['content'][0]['text'] = 'changed'
    assert history[0].content == 'PUBLIC'
    with pytest.raises(RuntimeError, match='capacity'):
        project(history * 501, aliases, 'one', 'thisServer', redact_sensitive_text)


@pytest.mark.parametrize('fault', ['wrong-result-name', 'duplicate-id', 'duplicate-result'])
def test_ambiguous_or_foreign_tool_provenance_cannot_enter_scoped_disclosure(fault):
    from dataclasses import replace
    history = list(turn('alias', 'call', 'PUBLIC'))
    if fault == 'wrong-result-name':
        history[2] = replace(history[2], tool_name='local-unrelated', content=(TextContent(text='PRIVATE LOCAL'),))
    elif fault == 'duplicate-id':
        history[1] = AssistantMessage(content=(ToolCall(id='call', name='alias', arguments={}),
                                               ToolCall(id='call', name='local-unrelated', arguments={})))
    else:
        history.insert(3, history[2])
    with pytest.raises(RuntimeError, match='identity|provenance'):
        project(tuple(history), {'alias': ('owned', 'lookup')}, 'owned', 'thisServer', redact_sensitive_text)


@pytest.mark.parametrize('scope', ['thisServer', 'allServers'])
def test_structured_mcp_result_projection_preserves_only_provenanced_payload_and_redacts(scope):
    from dataclasses import replace
    details = {'mcp_server': 'owned', 'structured_content_present': True,
               'structured_content': {'answer': 42, 'api_key': 'PRIVATE KEY',
                                      'nested': {'credential': 'PRIVATE CREDENTIAL', 'label': 'PUBLIC STRUCTURED'}},
               'host_private': 'PRIVATE HOST WRAPPER'}
    history = list(turn('alias', 'call', 'PUBLIC'))
    history[2] = replace(history[2], details=details)
    rows = project(tuple(history), {'alias': ('owned', 'lookup')}, 'owned', scope, redact_sensitive_text)
    value = next(row['content'][0] for row in rows if row['content'][0]['type'] == 'tool_result')
    assert value['structuredContent'] == {'answer': 42, 'api_key': '<redacted>',
                                          'nested': {'credential': '<redacted>', 'label': 'PUBLIC STRUCTURED'}}
    assert all(text not in json.dumps(rows) for text in ['PRIVATE KEY', 'PRIVATE CREDENTIAL', 'PRIVATE HOST WRAPPER'])
    value['structuredContent']['answer'] = 99
    assert details['structured_content']['answer'] == 42


@pytest.mark.parametrize('details', [
    {'mcp_server': 'foreign', 'structured_content_present': True, 'structured_content': {'private': 'FOREIGN'}},
    {'mcp_server': 'owned', 'structured_content_present': 1, 'structured_content': {}},
    {'mcp_server': 'owned', 'structured_content_present': True},
])
def test_structured_projection_invalid_manager_provenance_refuses(details):
    from dataclasses import replace
    history = list(turn('alias', 'call', 'PUBLIC'))
    history[2] = replace(history[2], details=details)
    with pytest.raises(RuntimeError, match='provenance'):
        project(tuple(history), {'alias': ('owned', 'lookup')}, 'owned', 'thisServer', redact_sensitive_text)


@pytest.mark.parametrize('payload', [None, [1, {'answer': 'PUBLIC'}], False])
def test_structured_projection_preserves_present_null_array_or_boolean(payload):
    from dataclasses import replace
    history = list(turn('alias', 'call', 'PUBLIC'))
    history[2] = replace(history[2], details={'mcp_server': 'owned', 'structured_content_present': True,
                                          'structured_content': payload})
    rows = project(tuple(history), {'alias': ('owned', 'lookup')}, 'owned', 'thisServer', redact_sensitive_text)
    block = next(row['content'][0] for row in rows if row['content'][0]['type'] == 'tool_result')
    assert 'structuredContent' in block and block['structuredContent'] == payload


def test_structured_absent_or_unrelated_host_details_are_not_shared():
    from dataclasses import replace
    for details in ({'host_private': 'PRIVATE WRAPPER'},
                    {'mcp_server': 'owned', 'structured_content_present': False,
                     'structured_content': 'PRIVATE ABSENT'}):
        history = list(turn('alias', 'call', 'PUBLIC'))
        history[2] = replace(history[2], details=details)
        rows = project(tuple(history), {'alias': ('owned', 'lookup')}, 'owned', 'thisServer', redact_sensitive_text)
        block = next(row['content'][0] for row in rows if row['content'][0]['type'] == 'tool_result')
        assert 'structuredContent' not in block and 'PRIVATE WRAPPER' not in json.dumps(rows)


def test_oversized_structured_context_refuses_instead_of_silently_dropping_payload():
    from dataclasses import replace
    history = list(turn('alias', 'call', 'PUBLIC'))
    history[2] = replace(history[2], details={'mcp_server': 'owned', 'structured_content_present': True,
                                           'structured_content': {'body': 'x' * (256 * 1024)}})
    with pytest.raises((RuntimeError, ValueError), match='capacity|limit'):
        project(tuple(history), {'alias': ('owned', 'lookup')}, 'owned', 'thisServer', redact_sensitive_text)


def test_independent_completed_turns_may_reuse_ids_without_cross_pairing():
    history = (*turn('alias', 'reused', 'FIRST'), *turn('alias', 'reused', 'SECOND'))
    rows = project(history, {'alias': ('owned', 'lookup')}, 'owned', 'thisServer', redact_sensitive_text)
    calls = [part['id'] for row in rows for part in row['content'] if part['type'] == 'tool_use']
    results = [part['toolUseId'] for row in rows for part in row['content'] if part['type'] == 'tool_result']
    assert len(calls) == 2 and len(set(calls)) == 2 and results == calls
    assert 'FIRST' in str(rows) and 'SECOND' in str(rows)
    assert history[1].content[0].id == history[5].content[0].id == 'reused'
    assert project(history, {'alias': ('owned', 'lookup')}, 'owned', 'thisServer', redact_sensitive_text) == rows


def test_binding_rejects_duplicate_context_identity_instead_of_repairing_it():
    from xdog.agent.mcp.sampling_context import bind_context
    source = {'params': {'messages': []}}
    rows = [
        {'role': 'assistant', 'content': {'type': 'tool_use', 'id': 'same', 'name': 'lookup', 'input': {}}},
        {'role': 'user', 'content': {'type': 'tool_result', 'toolUseId': 'same', 'content': []}}]
    with pytest.raises(RuntimeError, match='identity'):
        bind_context([*rows, *rows], source)
