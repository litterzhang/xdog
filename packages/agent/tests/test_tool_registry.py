"""Registry is an inert host-owned SDK; bindings and factories never leak owners."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from xdog.agent import Agent, ToolContext, ToolRegistry
from xdog.agent.core import AgentTool


def tool(name: str) -> AgentTool:
    return AgentTool(name=name, description=name)


def test_defaults_are_definitions_and_instances_are_owner_local(tmp_path: Path) -> None:
    first, second = ToolRegistry(), ToolRegistry()
    assert first.default_names() == ('bash', 'filesystem', 'current_time')
    assert 'submit_result' in first.registered_names()
    assert first.tools == second.tools == ()
    a = first.create_tools(context=ToolContext(cwd=tmp_path / 'a'))
    b = second.create_tools(context=ToolContext(cwd=tmp_path / 'b'))
    assert [t.name for t in a] == [t.name for t in b] == list(first.default_names())
    assert all(left is not right for left, right in zip(a, b, strict=True))
    first.replace_source('builtin', a)
    assert first.tools == tuple(a) and not second.tools
    assert not first.create_tools([])


def test_registration_is_local_and_duplicate_factories_fail() -> None:
    first, second = ToolRegistry(), ToolRegistry()
    first.register_factory('business', lambda context: tool('business'))
    assert 'business' not in second.registered_names()
    with pytest.raises(ValueError, match='already registered'):
        first.register_factory('bash', lambda context: tool('bash'))
    assert 'business' not in first.default_names()


def test_invalid_selection_fails_before_any_factory_runs() -> None:
    registry = ToolRegistry(builtins=False)
    calls = []
    registry.register_factory('valid', lambda context: calls.append(context) or tool('valid'))
    for names in (['valid', 'unknown'], ['valid', 'valid']):
        with pytest.raises(ValueError):
            registry.create_tools(names)
    assert not calls
    with pytest.raises(TypeError):
        registry.create_tools('valid')
    registry.register_factory('bad', lambda context: tool('wrong'))
    with pytest.raises(ValueError, match='named bad'):
        registry.create_tools(['bad'])


def test_sources_are_atomic_and_old_handles_cannot_retire_new_bindings() -> None:
    registry = ToolRegistry(builtins=False)
    original, replacement = tool('x'), tool('x')
    stale = registry.replace_source('owner', (original,))
    current = registry.replace_source('owner', (replacement,))
    stale.close()
    assert registry.tools == (replacement,)
    registry.replace_source('mcp', (tool('remote'),))
    before = registry.tools
    with pytest.raises(ValueError, match='Duplicate'):
        registry.replace_source('owner', (tool('remote'),))
    assert all(a is b for a, b in zip(registry.tools, before, strict=True))
    with pytest.raises(ValueError, match='Duplicate'):
        registry.replace_source('bad', (original, replacement))
    assert registry.tools == before
    current.close()
    current.close()
    assert [t.name for t in registry.tools] == ['remote']


def test_publication_preserves_restrictions_and_does_not_start_agent() -> None:
    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError('Registry must not start a model')
    agent = Agent(forbidden)  # type: ignore[arg-type]
    registry = ToolRegistry(builtins=False)
    registry.replace_source('host', (tool('a'), tool('b')))
    registry.publish(agent)
    agent.set_active_tools(('a',), expected_revision=agent.tool_selection_revision)
    revision = agent.tool_selection_revision
    registry.publish(agent)
    assert agent.tool_selection_revision == revision
    registry.replace_source('host', (tool('b'), tool('c')))
    registry.publish(agent)
    assert agent.active_tool_selection == ()
    registry.replace_source('host', (tool('a'),))
    registry.publish(agent)
    assert agent.active_tool_selection == ()
    assert not agent.messages


def test_import_has_no_registry_singleton_or_loop() -> None:
    from xdog.agent.tools import registry
    assert not hasattr(registry, '_registry')
    assert not hasattr(registry, 'register_tool')
    with pytest.raises(RuntimeError):
        asyncio.get_running_loop()


async def test_bash_cwd_is_not_shared_between_host_registries(tmp_path: Path) -> None:
    first_dir, second_dir = tmp_path / 'first', tmp_path / 'second'
    first_dir.mkdir()
    second_dir.mkdir()
    first = ToolRegistry().create_tools(['bash'], context=ToolContext(cwd=first_dir))[0]
    second = ToolRegistry().create_tools(['bash'], context=ToolContext(cwd=second_dir))[0]
    assert first.execute is not None and second.execute is not None
    await first.execute('cd', {'command': 'cd ..'})
    result = await second.execute('pwd', {'command': 'pwd'})
    assert str(second_dir) in str(result.content)
    assert not result.is_error
