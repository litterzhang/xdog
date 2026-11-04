"""Child provider advertisements retain exact frozen schemas, never approximate grants."""
import copy

import pytest
from xdog.agent.child_policy import ChildDelegation
from xdog.agent.core import AgentTool
from xdog.agent.types import AgentContext, BeforeToolCallContext
from xdog.ai.types import AssistantMessage, ToolCall


def test_frozen_whole_object_enum_is_not_rewritten_or_widened():
    schema = {'type': 'object', 'enum': [{'action': 'read', 'path': '/a'}, {'action': 'stat', 'path': '/b'}]}
    actual = AgentTool('filesystem', parameters=copy.deepcopy(schema))
    grant = ChildDelegation.capture([actual], None)
    advertised = grant.provider_parameters('filesystem')
    assert advertised == schema and advertised is not schema
    actual.parameters['enum'].append({'action': 'write', 'path': '/c'})
    assert grant.provider_parameters('filesystem') == schema
    advertised['enum'].clear()
    assert grant.provider_parameters('filesystem') == schema
    for args in ({'action': 'read', 'path': '/b'}, {'action': 'write', 'path': '/c'}, {}):
        ctx = BeforeToolCallContext(AssistantMessage(), ToolCall('id', 'filesystem', args), args, AgentContext())
        with pytest.raises(ValueError):
            grant.check(ctx, [actual], live=True)


def test_tool_actions_require_an_explicit_action_property_without_legacy_inference():
    actual = AgentTool('filesystem', parameters={'type': 'object', 'enum': [{'action': 'read'}]})
    with pytest.raises(ValueError, match='declared.*action'):
        ChildDelegation.capture([actual], None, (('filesystem', ('read',)),))


def test_provider_parameters_preserve_additional_constraints_and_are_detached():
    schema = {'type': 'object', 'properties': {'id': {'type': 'string'}}, 'required': ['id'],
              'additionalProperties': False, 'allOf': [{'properties': {'id': {'minLength': 2}}}]}
    actual = AgentTool('sample', parameters=copy.deepcopy(schema))
    grant = ChildDelegation.capture([actual], None)
    assert grant.provider_parameters('sample') == schema
    assert grant.provider_parameters('sample') is not actual.parameters
    with pytest.raises(ValueError):
        grant.provider_parameters('missing')
