"""Sampling codec retains review data but never grants parent context/tools/model admission."""
from __future__ import annotations

import copy

import pytest
from xdog.agent.mcp.sampling import request, result, review
from xdog.agent.mcp.stdio import McpError


def envelope(**fields):
    return {'jsonrpc': '2.0', 'id': 'owned', 'method': 'sampling/createMessage', 'params': {
        'messages': [{'role': 'user', 'content': {'type': 'text', 'text': 'SERVER REQUEST'}}],
        'maxTokens': 100, **fields}}


def test_sampling_review_preserves_context_preferences_tools_and_exact_digest_without_authority():
    source = envelope(includeContext='allServers', systemPrompt='UNTRUSTED SYSTEM',
        modelPreferences={'hints': [{'name': 'preferred-model'}], 'costPriority': .2}, temperature=.1,
        stopSequences=['END'], metadata={'vendor': ['retained', 1]}, _meta={'progressToken': 'progress'},
        task={'ttl': 1000}, tools=[{'name': 'servertool', 'inputSchema': {'type': 'object'},
                                  'annotations': {'readOnlyHint': True}}], toolChoice={'mode': 'required'})
    validated = request(source)
    assert validated == source and validated is not source
    inspected = review(source)
    assert inspected['request'] == source and len(inspected['sha256']) == 64
    inspected['request']['params']['messages'].clear()
    assert source['params']['messages']
    assert review(source)['sha256'] == review(copy.deepcopy(source))['sha256']
    changed = copy.deepcopy(source)
    changed['params']['includeContext'] = 'none'
    assert review(changed)['sha256'] != review(source)['sha256']


@pytest.mark.parametrize('fields', [
    {'maxTokens': True}, {'maxTokens': 0}, {'maxTokens': 65537}, {'temperature': float('nan')},
    {'includeContext': 'automatic'}, {'modelPreferences': {'speedPriority': 2}},
    {'modelPreferences': {'hints': ['name']}}, {'stopSequences': [False]}, {'metadata': []},
    {'messages': [{'role': 'system', 'content': {'type': 'text', 'text': 'forged'}}]},
    {'messages': [{'role': 'user', 'content': {'type': 'unknown', 'data': 'body'}}]},
    {'messages': [{'role': 'user', 'content': {'type': 'image', 'data': 'bad', 'mimeType': 'image/png'}}]},
    {'tools': [{'name': 'unsafe', 'inputSchema': {'type': 'object', '$ref': 'https://never-fetch/schema'}}]},
    {'tools': [{'name': 'x', 'inputSchema': {'type': 'object'}}] * 2}, {'toolChoice': {'mode': 'required'}},
    {'task': {'ttl': True}}, {'unexpected': 'must-not-drop'},
])
def test_invalid_sampling_contract_refuses_not_coerces_or_fetches(fields):
    with pytest.raises((McpError, ValueError)):
        request(envelope(**fields))


@pytest.mark.parametrize('content', [
    {'type': 'text', 'text': 'APPROVED RESULT'},
    {'type': 'audio', 'data': 'AAEC', 'mimeType': 'audio/wav'},
])
def test_sampling_result_exact_typed_block_metadata_not_plain_string(content):
    value = {'model': 'chosen-by-host', 'role': 'assistant', 'content': content, 'stopReason': 'endTurn',
             '_meta': {'vendor': 'retained'}}
    assert result(value, request(envelope())) == value


@pytest.mark.parametrize('value', [
    {'model': '', 'role': 'assistant', 'content': {'type': 'text', 'text': 'body'}},
    {'model': 'chosen', 'role': 'system', 'content': {'type': 'text', 'text': 'body'}},
    {'model': 'chosen', 'role': 'assistant', 'content': [{'type': 'text', 'text': 'body'}]},
    {'model': 'chosen', 'role': 'assistant', 'content': {
        'type': 'tool_use', 'name': 'unreviewed', 'id': 'call', 'input': {}}},
    {'model': 'chosen', 'role': 'assistant', 'content': {'type': 'unknown', 'data': 'not dropped'}},
])
def test_invalid_result_cannot_impersonate_approved_model_or_tool(value):
    with pytest.raises(McpError):
        result(value, request(envelope()))


def test_tool_result_and_use_retained_only_with_explicit_named_tool_request():
    original = request(envelope(tools=[{'name': 'approved', 'inputSchema': {'type': 'object'}}]))
    response = {'model': 'host', 'role': 'assistant', 'content': [
        {'type': 'text', 'text': 'tool proposal'},
        {'type': 'tool_use', 'name': 'approved', 'id': 'call', 'input': {'a': 1}}]}
    assert result(response, original) == response
    response['content'][1]['name'] = 'unreviewed'
    with pytest.raises(McpError, match='outside reviewed'):
        result(response, original)


def test_result_tool_arguments_validate_exact_declared_schema_without_secret_diagnostic():
    source = request(envelope(tools=[{'name': 'approved', 'inputSchema': {
        'type': 'object', 'required': ['count'], 'properties': {'count': {'type': 'integer'}}}}]))
    response = {'model': 'host', 'role': 'assistant', 'content': [
        {'type': 'tool_use', 'name': 'approved', 'id': 'call', 'input': {'count': 'PRIVATE-INVALID'}}]}
    with pytest.raises(McpError, match='JSON Schema') as error:
        result(response, source)
    assert 'PRIVATE' not in str(error.value)


def paired_messages():
    return [
        {'role': 'assistant', 'content': [
            {'type': 'tool_use', 'name': 'one', 'id': '1', 'input': {}},
            {'type': 'tool_use', 'name': 'two', 'id': '2', 'input': {}}]},
        {'role': 'user', 'content': [
            {'type': 'tool_result', 'toolUseId': '2', 'content': [{'type': 'text', 'text': 'SECOND'}]},
            {'type': 'tool_result', 'toolUseId': '1', 'content': [{'type': 'text', 'text': 'FIRST'}]}]},
        {'role': 'user', 'content': {'type': 'text', 'text': 'Continue'}}]


def test_parallel_original_tool_pairing_and_icon_task_metadata_remain_inert_and_lossless():
    source = envelope(messages=paired_messages(), task={'ttl': 12.5}, tools=[{
        'name': 'one', 'inputSchema': {'type': 'object'}, 'icons': [{'src': 'https://never-fetch.invalid/icon',
            'mimeType': 'image/png', 'sizes': ['32x32', 'any'], 'theme': 'dark'}]}])
    assert request(source) == source


@pytest.mark.parametrize('fault', ['missing', 'duplicate-call', 'duplicate-result', 'foreign-result',
                                  'user-call', 'assistant-result', 'early-assistant'])
def test_invalid_tool_pairing_refuses_before_review_or_model_conversion(fault):
    rows = paired_messages()
    if fault == 'missing':
        rows.pop(1)
    elif fault == 'duplicate-call':
        rows[0]['content'][1]['id'] = '1'
    elif fault == 'duplicate-result':
        rows[1]['content'][1]['toolUseId'] = '2'
    elif fault == 'foreign-result':
        rows[1]['content'][1]['toolUseId'] = 'foreign'
    elif fault == 'user-call':
        rows[0]['role'] = 'user'
    elif fault == 'assistant-result':
        rows[1]['role'] = 'assistant'
    else:
        rows.insert(1, {'role': 'assistant', 'content': {'type': 'text', 'text': 'premature'}})
    with pytest.raises(McpError):
        request(envelope(messages=rows))


@pytest.mark.parametrize('fields', [{'task': {'ttl': None}}, {'task': {'ttl': False}},
                                  {'_meta': {'progressToken': True}},
                                  {'tools': [{'name': 'x', 'inputSchema': {'type': 'object'}, 'icons': 'bad'}]},
                                  {'tools': [{'name': 'x', 'inputSchema': {'type': 'object'},
                                              'icons': [{'src': 'https://inert', 'theme': 'automatic'}]}]}])
def test_bad_optional_icon_task_progress_metadata_is_not_silently_accepted(fields):
    with pytest.raises(McpError):
        request(envelope(**fields))


def test_duplicate_output_tool_calls_and_forged_tool_result_refused():
    source = request(envelope(tools=[{'name': 'one', 'inputSchema': {'type': 'object'}}]))
    call = {'type': 'tool_use', 'name': 'one', 'id': 'same', 'input': {}}
    with pytest.raises(McpError, match='identity'):
        result({'model': 'host', 'role': 'assistant', 'content': [call, call]}, source)
    with pytest.raises(McpError, match='impersonate'):
        result({'model': 'host', 'role': 'assistant', 'content': [
            {'type': 'tool_result', 'toolUseId': 'same', 'content': []}]}, source)


def test_official_optional_tool_choice_mode_is_preserved_not_invented():
    source = envelope(tools=[{'name': 'approved', 'inputSchema': {'type': 'object'}}], toolChoice={})
    assert request(source) == source


def test_explicit_tool_choice_none_blocks_tool_proposal_even_with_named_definition():
    source = request(envelope(tools=[{'name': 'approved', 'inputSchema': {'type': 'object'}}],
                              toolChoice={'mode': 'none'}))
    output = {'model': 'chosen', 'role': 'assistant', 'content': [
        {'type': 'tool_use', 'name': 'approved', 'id': 'proposal', 'input': {}}]}
    with pytest.raises(McpError, match='tool choice'):
        result(output, source)


def test_required_tool_choice_cannot_accept_text_only_result():
    source = request(envelope(tools=[{'name': 'approved', 'inputSchema': {'type': 'object'}}],
                              toolChoice={'mode': 'required'}))
    value = {'model': 'host', 'role': 'assistant', 'content': {'type': 'text', 'text': 'PRIVATE NO TOOL'}}
    with pytest.raises(McpError, match='required tool'):
        result(value, source)


def test_new_assistant_tool_batch_cannot_hide_unsettled_previous_batch():
    rows = [
        {'role': 'assistant', 'content': {'type': 'tool_use', 'name': 'one', 'id': 'first', 'input': {}}},
        {'role': 'assistant', 'content': {'type': 'tool_use', 'name': 'two', 'id': 'second', 'input': {}}},
        {'role': 'user', 'content': [
            {'type': 'tool_result', 'toolUseId': 'first', 'content': []},
            {'type': 'tool_result', 'toolUseId': 'second', 'content': []}]}]
    with pytest.raises(McpError, match='settlement'):
        request(envelope(messages=rows))


def test_settled_sequential_tool_batches_remain_lossless():
    rows = []
    for identifier in ['first', 'second']:
        rows.extend([
            {'role': 'assistant', 'content': {'type': 'tool_use', 'name': 'one', 'id': identifier, 'input': {}}},
            {'role': 'user', 'content': {'type': 'tool_result', 'toolUseId': identifier, 'content': []}}])
    assert request(envelope(messages=rows)) == envelope(messages=rows)
