"""UI-053 inventory-independent wire contracts, including unselected legacy branches.

The installed CLI decides its offered inventory. XDOG must preserve every actual
function/custom/client-search declaration and namespace instead of demanding that
all client versions/models expose a particular flat inventory.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from typing import Any

import pytest
from xdog.ai.proxy_responses import format_response, parse_request
from xdog.ai.types import AssistantMessage, ToolCall


def assert_tools_normalized(request, tools):
    flat = []
    for wire in request['tools']:
        if wire['type'] == 'namespace':
            flat.extend((wire['name'], item) for item in wire['tools'])
        else:
            flat.append((None, wire))
    assert flat and len(tools) == len(flat)
    expected_names = set()
    for namespace, wire in flat:
        kind = wire['type']
        assert kind in {'function', 'custom', 'tool_search'}, 'Unsupported kinds must not silently vanish'
        name = 'tool_search' if kind == 'tool_search' else wire['name']
        identity = json.dumps([namespace, name], ensure_ascii=True, separators=(',', ':'))
        normalized = name if namespace is None else 'xdog_ns_' + hashlib.sha256(identity.encode()).hexdigest()[:56]
        expected_names.add(normalized)
        actual = next(tool for tool in tools if tool.name == normalized)
        assert wire.get('description', '') in actual.description
        if namespace is not None:
            assert f'Tool {namespace}.{name}.' in actual.description
        if kind == 'custom':
            assert actual.parameters == {'type': 'object', 'properties': {'input': {'type': 'string'}},
                                         'required': ['input'], 'additionalProperties': False}
            argument = {'input': 'RAW 界\nnot a shell execution'}
        else:
            assert actual.parameters == wire.get('parameters', {'type': 'object', 'properties': {}})
            argument = {'value': 'ARGUMENT 界', 'nested': [False, 0, None]}
        # Confirm reverse identity and exact argument preservation, without ever
        # executing the client-side tools. Forward normalization alone is not enough.
        message = AssistantMessage(content=(ToolCall(id='call_probe', name=normalized, arguments=argument),),
                                   stop_reason='toolUse')
        output = format_response(message, request['model'], request)['output'][0]
        if kind == 'tool_search':
            assert 'name' not in output and output['execution'] == 'client'
        else:
            assert output['name'] == name
        assert output.get('namespace') == namespace
        assert output['type'] == {'function': 'function_call', 'custom': 'custom_tool_call',
                                  'tool_search': 'tool_search_call'}[kind]
        assert (output['input'] == argument['input'] if kind == 'custom' else
                output['arguments'] == argument if kind == 'tool_search' else
                json.loads(output['arguments']) == argument)
        replay_request = {**request, 'input': [{'role': 'user', 'content': 'offline'}, output]}
        _, replay, _, _ = parse_request(replay_request)
        assert replay.messages[-1].content == (ToolCall(id='call_probe', name=normalized, arguments=argument),)
    assert {tool.name for tool in tools} == expected_names


def function(name='same'):
    return {'type': 'function', 'name': name, 'description': 'Exact schema is retained',
            'parameters': {'type': 'object', 'properties': {'value': {'type': 'string'}}, 'required': ['value']}}


def custom(name='same'):
    return {'type': 'custom', 'name': name, 'description': 'Exact raw input', 'format': {'type': 'text'}}


def namespace(name, *tools):
    return {'type': 'namespace', 'name': name, 'description': 'Namespace ownership', 'tools': list(tools)}


@pytest.mark.parametrize('inventory', [
    [function('exec_command'), namespace('multi_agent_v1', function('send_input'), function('wait_agent'))],
    [function(), custom('raw'), {'type': 'tool_search', 'execution': 'client'}],
    [namespace('one', function(), custom('raw')), namespace('two', function(), custom('raw'))],
])
def test_standard_inventory_kinds_and_namespaced_arguments_round_trip_without_client_inventory_assumptions(inventory):
    request = {'model': 'offline', 'input': 'no execution', 'tools': inventory}
    _, context, _, _ = parse_request(request)
    assert_tools_normalized(request, context.tools)


@pytest.mark.parametrize('corruption', ['missing', 'name', 'schema', 'namespace'])
def test_contract_assertion_detects_lost_tools_identity_or_argument_schema(corruption):
    request: dict[str, Any] = {'model': 'offline', 'input': 'no execution',
                               'tools': [function(), namespace('owned', function())]}
    _, context, _, _ = parse_request(request)
    tools = list(context.tools)
    if corruption == 'missing':
        tools.pop()
    elif corruption == 'name':
        tools[0] = replace(tools[0], name='wrong')
    elif corruption == 'schema':
        tools[0] = replace(tools[0], parameters={'type': 'object'})
    else:
        tools[1] = replace(tools[1], description='Lost original namespace')
    with pytest.raises((AssertionError, StopIteration)):
        assert_tools_normalized(request, tools)
