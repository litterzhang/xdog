"""Action profiles fail before child creation; no shell/model/service is executed."""
from __future__ import annotations

import copy
from typing import Any

import pytest
from xdog.agent.access import read_only_call
from xdog.agent.child_config import ChildConfig
from xdog.agent.child_policy import ChildDelegation, ParentHandle
from xdog.agent.children import ChildClient
from xdog.agent.core import AgentTool
from xdog.agent.tools.tool_bash import create_bash_tool
from xdog.agent.types import AgentContext, BeforeToolCallContext
from xdog.ai.types import AssistantMessage, ToolCall


def context(tool: AgentTool, args: dict[str, Any]) -> BeforeToolCallContext:
    return BeforeToolCallContext(
        AssistantMessage(content=()), ToolCall('call', tool.name, args), args,
        AgentContext(system_prompt='', messages=[], tools=[tool]),
    )


def filesystem() -> AgentTool:
    return AgentTool('filesystem', parameters={
        'type': 'object', 'properties': {'action': {'type': 'string', 'enum': ['read', 'write']},
                                        'path': {'type': 'string'}},
        'required': ['action', 'path'], 'additionalProperties': False,
    })


@pytest.mark.parametrize('actions', [('*',), ('sleep',), ('sleep 60',), ()])
def test_bash_action_profiles_are_rejected_at_live_capture(actions: tuple[str, ...]) -> None:
    bash = create_bash_tool()
    before = copy.deepcopy(bash.parameters)
    parent = ParentHandle(alive=lambda: True, tools=lambda: [bash], skills=lambda: None)
    with pytest.raises(ValueError, match="not a multi-action tool.*Omit its tool_actions entry"):
        parent.capture_tools(('bash',), (('bash', actions),))
    assert bash.parameters == before and 'action' not in bash.parameters['properties']


def test_historical_incident_stays_readable_but_cannot_be_readmitted() -> None:
    value = {'tools': ['bash', 'filesystem'], 'tool_actions': {'bash': ['*'], 'filesystem': ['read']}}
    before = copy.deepcopy(value)
    config = ChildConfig.from_record(value)
    assert config.to_dict()['tool_actions'] == value['tool_actions']
    with pytest.raises(ValueError, match='not a multi-action tool'):
        ChildDelegation.capture([create_bash_tool(), filesystem()], config.tools, config.tool_actions)
    assert value == before  # No migration, mutation, or implicit repair.


def test_legacy_bad_grant_still_denies_execution_in_both_check_paths() -> None:
    bash = create_bash_tool()
    grant = ChildDelegation((bash,), (('bash', ('*',)),), (copy.deepcopy(bash.parameters),))
    for bound in (False, True):
        with pytest.raises(ValueError, match='outside delegated permissions'):
            grant.check(context(bash, {'command': 'sleep 60'}), [bash], live=True, capability_bound=bound)


def test_correct_profile_selects_bash_without_inventing_actions_or_grants() -> None:
    bash, fs = create_bash_tool(), filesystem()
    grant = ChildDelegation.capture([bash, fs], ('bash', 'filesystem'), (('filesystem', ('read',)),))
    assert grant.actions == (('filesystem', ('read',)),)
    args = {'command': 'sleep 60', 'timeout_ms': 120000}
    grant.check(context(bash, args), [bash, fs], live=True)  # Validation only, never execute bash.
    assert grant.provider_parameters('bash') == bash.parameters
    assert not read_only_call('bash', args)  # A corrected profile is NOT a read-only exemption.
    for available in ([], [fs]):
        with pytest.raises(ValueError, match='exceeds parent'):
            ChildDelegation.capture(available, ('bash',))
    with pytest.raises(ValueError, match='delegation'):
        grant.check(context(bash, args), [fs], live=True)
    with pytest.raises(ValueError, match='retired'):
        grant.check(context(bash, args), [bash, fs], live=False)


@pytest.mark.parametrize('actions', [('*',), ('sleep',), ('delete',), ('read', 'write', 'unknown')])
def test_invalid_literals_are_rejected_before_effects(actions: tuple[str, ...]) -> None:
    with pytest.raises(ValueError, match='declared action schema'):
        ChildDelegation.capture([filesystem()], None, (('filesystem', actions),))


def test_valid_actions_still_narrow_full_captured_and_live_schemas() -> None:
    fs = filesystem()
    grant = ChildDelegation.capture([fs], None, (('filesystem', ('read',)),))
    grant.check(context(fs, {'action': 'read', 'path': '/a'}), [fs], live=True)
    with pytest.raises(ValueError, match='outside delegated permissions'):
        grant.check(context(fs, {'action': 'write', 'path': '/a'}), [fs], live=True)
    with pytest.raises(ValueError, match='schema'):
        grant.check(context(fs, {'action': 'read'}), [fs], live=True)
    fs.parameters['properties']['action']['enum'].remove('read')
    with pytest.raises(ValueError, match='schema'):
        grant.check(context(fs, {'action': 'read', 'path': '/a'}), [fs], live=True)


def test_empty_action_selection_remains_explicit_deny_all_not_unrestricted() -> None:
    fs = filesystem()
    grant = ChildDelegation.capture([fs], None, (('filesystem', ()),))
    for bound in (False, True):
        with pytest.raises(ValueError, match='outside delegated permissions'):
            grant.check(context(fs, {'action': 'read', 'path': '/a'}), [fs], live=True, capability_bound=bound)


@pytest.mark.parametrize('schema,good,bad', [
    ({'type': 'object', 'properties': {'action': {'const': 'read'}}}, 'read', 'write'),
    ({'type': 'object', 'properties': {'action': {'$ref': '#/$defs/action'}},
      '$defs': {'action': {'enum': ['read']}}}, 'read', 'write'),
    ({'type': 'object', 'enum': [{'action': 'read', 'path': '/a'}]}, 'read', 'write'),
    ({'type': 'object', 'properties': {'action': {'type': 'string', 'pattern': '^read$'}}}, 'read', '*'),
    ({'type': 'object', 'properties': {'action': {'enum': ['*', 'read']}}}, '*', 'write'),
])
def test_action_schema_checks_preserve_refs_legacy_and_literal_star(
    schema: dict[str, Any], good: str, bad: str,
) -> None:
    tool = AgentTool('custom', parameters=schema)
    before = copy.deepcopy(schema)
    grant = ChildDelegation.capture([tool], None, (('custom', (good,)),))
    assert grant.actions == (('custom', (good,)),)  # '*' can be a literal, never an expansion.
    with pytest.raises(ValueError, match='declared action schema'):
        ChildDelegation.capture([tool], None, (('custom', (bad,)),))
    assert schema == before


def test_unresolvable_action_schema_fails_closed_without_fetching() -> None:
    tool = AgentTool('custom', parameters={'type': 'object', 'properties': {
        'action': {'$ref': 'https://example.invalid/no-network'}}})
    with pytest.raises(ValueError, match='declared action schema'):
        ChildDelegation.capture([tool], None, (('custom', ('read',)),))


def test_duplicate_action_restrictions_cannot_shadow_sdk_grant() -> None:
    fs = filesystem()
    with pytest.raises(ValueError, match='Duplicate'):
        ChildDelegation.capture([fs], None, (('filesystem', ('read',)), ('filesystem', ('write',))))
    with pytest.raises(ValueError, match='distinct'):
        ChildDelegation.capture([fs], None, (('filesystem', ('read', 'read')),))


def test_sdk_describes_correct_format_without_starting_backend() -> None:
    class Backend:
        created = False

    # The backend is deliberately minimal: construction must not start anything.
    backend = Backend()
    tool = ChildClient(backend).tools()[0]  # type: ignore[arg-type]
    assert 'bash is NOT a multi-action tool' in tool.description
    description = tool.parameters['properties']['config']['properties']['tool_actions']['description']
    assert 'bash has command, not action' in description and 'No wildcard' in description
    assert not backend.created


async def test_tool_spawn_returns_action_diagnostic_before_backend_effects() -> None:
    class Backend:
        tool_return_owner = 'original'
        created = False

        def control_alive(self, owner: str) -> bool:
            return True

        async def start(self, task: str, **kwargs: Any) -> str:
            config = ChildConfig.from_dict(kwargs['config'])
            ChildDelegation.capture([create_bash_tool()], config.tools, config.tool_actions)
            self.created = True
            return 'never'

    backend = Backend()
    tool = ChildClient(backend).tools()[0]  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="Omit its tool_actions entry"):
        await tool.execute('call', {'action': 'spawn', 'task': 'No execution', 'access': 'inherit',
                                    'config': {'tools': ['bash'], 'tool_actions': {'bash': ['*']}}})
    assert not backend.created
