"""Provider-compatible advertisements never replace the execution-time grant."""
from __future__ import annotations

import copy
import json
from decimal import Decimal
from typing import Any

import pytest
from jsonschema import Draft7Validator, Draft202012Validator
from xdog.agent.child_policy import ChildDelegation
from xdog.agent.core import AgentTool
from xdog.agent.types import AgentContext, BeforeToolCallContext
from xdog.ai.types import AssistantMessage, ToolCall


def delegation(schema: dict[str, Any]) -> ChildDelegation:
    return ChildDelegation.capture([AgentTool('filesystem', parameters=schema)], None)


def context(grant: ChildDelegation, args: dict[str, Any]) -> BeforeToolCallContext:
    return BeforeToolCallContext(
        AssistantMessage(content=()), ToolCall('call', 'filesystem', args), args,
        AgentContext(system_prompt='', messages=[], tools=list(grant.tools)),
    )


def provider_schema(grant: ChildDelegation) -> dict[str, Any]:
    schema = grant.provider_parameters('filesystem')
    # Validate both common schema dialects, as well as strict JSON serialization.
    Draft7Validator.check_schema(schema)
    Draft202012Validator.check_schema(schema)
    json.dumps(schema, allow_nan=False)
    assert schema['type'] == 'object'
    assert isinstance(schema['properties'], dict)
    assert 'enum' not in schema
    assert schema['additionalProperties'] is False
    for prop in schema['properties'].values():
        assert 'type' in prop
        assert not any(isinstance(value, (dict, list)) for value in prop['enum'])
    return schema


def test_legacy_advertises_fields_without_mutating_authoritative_schemas():
    candidates = [
        {'action': 'read', 'path': '/a', 'limit': 10},
        {'action': 'stat', 'path': '/b', 'follow': False},
    ]
    grant = delegation({'type': 'object', 'enum': candidates})
    original = copy.deepcopy(grant.schemas)
    source = copy.deepcopy(grant.tools[0].parameters)
    schema = provider_schema(grant)
    assert schema == {
        'type': 'object',
        'properties': {
            'action': {'type': 'string', 'enum': ['read', 'stat']},
            'path': {'type': 'string', 'enum': ['/a', '/b']},
            'limit': {'type': 'integer', 'enum': [10]},
            'follow': {'type': 'boolean', 'enum': [False]},
        },
        'required': ['action', 'path'],
        'additionalProperties': False,
    }
    validator = Draft202012Validator(schema)
    for args in candidates:
        validator.validate(args)
        grant.check(context(grant, args), grant.tools, live=True)
    assert not validator.is_valid({**candidates[0], 'extra': 'not advertised'})
    assert not validator.is_valid({'path': '/a'})
    assert grant.schemas == original
    assert grant.tools[0].parameters == source
    schema['properties']['action']['enum'].append('write')
    schema['required'].clear()
    assert grant.schemas == original
    assert grant.tools[0].parameters == source
    assert grant.provider_parameters('filesystem')['required'] == ['action', 'path']


@pytest.mark.parametrize('args', [
    {'action': 'read', 'path': '/b', 'limit': 10},  # Mixed historical fields.
    {'action': 'read', 'path': '/a'},  # Missing a field required in this candidate.
    {'action': 'stat', 'path': '/b', 'follow': False, 'limit': 10},
])
def test_provider_field_combinations_do_not_grant_execution(args):
    grant = delegation({'type': 'object', 'enum': [
        {'action': 'read', 'path': '/a', 'limit': 10},
        {'action': 'stat', 'path': '/b', 'follow': False},
    ]})
    Draft202012Validator(provider_schema(grant)).validate(args)
    with pytest.raises(ValueError, match='schema'):
        grant.check(context(grant, args), grant.tools, live=True)


@pytest.mark.parametrize('schema', [
    {'type': 'object', 'properties': {'action': {'type': 'string', 'enum': ['read']}},
     'required': ['action'], 'additionalProperties': False},
    {'type': 'object', 'properties': {'nested': {'type': 'object', 'enum': [{'a': 1}]}}},
    {'type': 'string', 'enum': ['read', 'stat']},
])
def test_ordinary_schema_is_unchanged_deep_copy(schema):
    grant = delegation(schema)
    before = copy.deepcopy(grant.schemas)
    result = grant.provider_parameters('filesystem')
    assert result == schema
    assert result is not grant.schemas[0]
    assert result is not grant.tools[0].parameters
    if 'properties' in result:
        assert result['properties'] is not grant.schemas[0]['properties']
        key = next(iter(result['properties']))
        result['properties'][key]['description'] = 'detached'
        result['required'] = []
    else:
        result['enum'].append('write')
    assert grant.schemas == before
    assert grant.tools[0].parameters == schema


def test_provider_parameters_uses_captured_schema_not_expanded_live_tool():
    grant = delegation({'type': 'object', 'enum': [{'action': 'read'}]})
    grant.tools[0].parameters['enum'].append({'action': 'write'})
    schema = provider_schema(grant)
    assert schema['properties']['action']['enum'] == ['read']
    with pytest.raises(ValueError, match='schema'):
        grant.check(context(grant, {'action': 'write'}), grant.tools, live=True)


def test_nullable_mixed_primitives_and_json_boolean_number_deduplication():
    values = [None, False, 0, True, 1, 1.0, 1.5, '1', False, 0.0]
    grant = delegation({'type': 'object', 'enum': [{'value': value} for value in values]})
    schema = provider_schema(grant)
    prop = schema['properties']['value']
    assert prop['type'] == ['null', 'boolean', 'integer', 'number', 'string']
    assert [(type(value), value) for value in prop['enum']] == [
        (type(None), None), (bool, False), (int, 0), (bool, True),
        (int, 1), (float, 1.5), (str, '1'),
    ]
    validator = Draft202012Validator(schema)
    for value in values:
        validator.validate({'value': value})
    assert not validator.is_valid({'value': 2})
    assert not validator.is_valid({'value': []})
    assert not validator.is_valid({'value': {}})


@pytest.mark.parametrize('values, types', [([None, 'a'], ['null', 'string']),
                                          ([None, 2.5], ['null', 'number']),
                                          ([None], 'null')])
def test_nullable_field_type_compatibility(values, types):
    grant = delegation({'type': 'object', 'enum': [{'value': value} for value in values]})
    schema = provider_schema(grant)
    assert schema['properties']['value']['type'] == types
    for value in values:
        Draft202012Validator(schema).validate({'value': value})


@pytest.mark.parametrize('candidates', [[{}], [{}, {}], [{}, {'optional': 'yes'}]])
def test_empty_candidate_objects(candidates):
    grant = delegation({'type': 'object', 'enum': candidates})
    schema = provider_schema(grant)
    assert schema['required'] == []
    if all(not candidate for candidate in candidates):
        assert schema['properties'] == {}
    for candidate in candidates:
        Draft202012Validator(schema).validate(candidate)
    assert not Draft202012Validator(schema).is_valid({'unknown': 1})


@pytest.mark.parametrize('candidates', [
    [], None, {}, (), 'not an enum', [None], ['read'], [1], [[]],
    [{'ok': 'read'}, 'not an object'],
    [{'value': []}], [{'value': {}}], [{'value': (1,)}], [{'value': {1}}],
    [{'value': b'read'}], [{'value': Decimal('1')}], [{'value': object()}],
    [{'value': float('nan')}], [{'value': float('inf')}], [{'value': float('-inf')}],
    [{1: 'not a JSON object key'}],
])
def test_invalid_legacy_shapes_fail_closed(candidates):
    grant = delegation({'type': 'object', 'enum': candidates})
    with pytest.raises(ValueError, match='Legacy object enum'):
        grant.provider_parameters('filesystem')


@pytest.mark.parametrize('extra', [
    {'properties': {'action': {'const': 'read'}}},
    {'required': ['action']}, {'additionalProperties': False},
    {'allOf': [{'maxProperties': 1}]}, {'minProperties': 1},
    {'$ref': '#/$defs/allowed'}, {'description': 'Do not silently drop root keywords'},
])
def test_additional_legacy_root_constraints_are_explicitly_rejected(extra):
    grant = delegation({'type': 'object', 'enum': [{'action': 'read'}], **extra})
    original = copy.deepcopy(grant.schemas)
    with pytest.raises(ValueError, match='root constraints'):
        grant.provider_parameters('filesystem')
    assert grant.schemas == original


def test_unknown_tool_is_not_advertised():
    with pytest.raises(ValueError, match='delegation'):
        delegation({'type': 'object'}).provider_parameters('not-delegated')


def test_direct_grant_without_captured_schemas_returns_detached_parameters():
    source = AgentTool('filesystem', parameters={'type': 'object', 'enum': [{}]})
    grant = ChildDelegation((source,))
    assert provider_schema(grant)['properties'] == {}
    assert grant.schemas == ()
    assert source.parameters == {'type': 'object', 'enum': [{}]}
