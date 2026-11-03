"""A second, non-channel-specific tool exercises the generic input binding contract."""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest
from xdog.agent import AgentTool
from xdog.claw.core.runtime.identity_scope import resolve
from xdog.claw.core.runtime.input_tools import bind_input_tools
from xdog.claw.core.tools.input_tool import InputTool
from xdog.claw.core.types import Group, UserInput


class FixtureTool(InputTool):
    def __init__(self):
        super().__init__('fixture-reader')

    def bind_input(self, original, channel, current):
        async def read():
            if not current():
                raise ValueError('Original input owner retired')
            return {'content': original.content, 'channel': channel}
        return read

    def read_only(self, arguments):
        return not arguments


def owner(tool):
    message = UserInput(group_id='main', content='original')
    agent = SimpleNamespace(active_tools=[tool], tool_context={})
    runtime = SimpleNamespace(_closing=False, _tools=[tool], group=Group(id='main'))
    session = SimpleNamespace(agent=agent, _runtime=runtime, _input_execution=resolve(message, 'input'))
    runtime._active_session = session
    return session, message


async def test_binding_supports_arbitrary_host_tool_without_domain_name_dispatch():
    tool = FixtureTool()
    session, message = owner(tool)
    bindings = bind_input_tools(session, message, 'opaque-channel', 'input', lambda: True)
    session.agent.tool_context['_input_tools'] = bindings
    assert await bindings[tool.name]() == {'content': 'original', 'channel': 'opaque-channel'}
    # Rebinding the same input ID is a new lifetime, not authority to revive an old handle.
    replacement = bind_input_tools(session, message, 'opaque-channel', 'input', lambda: True)
    session.agent.tool_context['_input_tools'] = replacement
    with pytest.raises(ValueError, match='retired'):
        await bindings[tool.name]()
    assert await replacement[tool.name]()


@pytest.mark.parametrize('revocation', ['registration', 'selection', 'input', 'owner', 'permission', 'binding'])
async def test_live_binding_rechecks_original_actual_tool_and_input_owner(revocation):
    tool = FixtureTool()
    session, message = owner(tool)
    current = True
    bindings = bind_input_tools(session, message, None, 'input', lambda: current)
    session.agent.tool_context['_input_tools'] = bindings
    if revocation == 'registration':
        session._runtime._tools = [AgentTool(tool.name)]
    elif revocation == 'selection':
        session.agent.active_tools = [AgentTool(tool.name)]
    elif revocation == 'input':
        session._input_execution = resolve(message, 'another-input')
    elif revocation == 'owner':
        current = False
    elif revocation == 'permission':
        from xdog.claw.core.identity import IdentityResolver, MessageSource
        message = replace(
            message, identity=IdentityResolver().channel('main', MessageSource('public', sender_id='external')),
        )
        session._input_execution = resolve(message, 'input')
    else:
        session.agent.tool_context['_input_tools'] = {}
    with pytest.raises(ValueError, match='retired'):
        await bindings[tool.name]()


def test_plain_same_named_tool_and_unregistered_tool_do_not_receive_a_binding():
    actual = FixtureTool()
    session, message = owner(actual)
    session.agent.active_tools = [AgentTool(actual.name)]
    assert bind_input_tools(session, message, None, 'input', lambda: True) == {}
    session.agent.active_tools = [FixtureTool()]
    assert bind_input_tools(session, message, None, 'input', lambda: True) == {}
