"""Actual instances, independent subsets, atomic registration and owner publication."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from xdog.agent import Agent, AgentTool, ToolRepo


def tool(name: str) -> AgentTool:
    return AgentTool(name=name, description=name)


def test_defaults_are_actual_owner_local_tools() -> None:
    first, second = ToolRepo(), ToolRepo()
    assert [t.name for t in first] == ['bash', 'filesystem', 'current_time']
    assert first.names() == second.names()
    assert all(a is not b for a, b in zip(first, second, strict=True))
    assert not ToolRepo(builtin=False).tools


def test_register_is_atomic_and_conflicts_do_not_replace() -> None:
    repo = ToolRepo(builtin=False)
    original = tool('x')
    repo.register(original)
    before = repo.tools
    for additions in [(tool('y'), tool('x')), (tool('y'), tool('y'))]:
        with pytest.raises(ValueError, match='Duplicate'):
            repo.register(*additions)
        assert repo.tools == before
    with pytest.raises(TypeError, match='actual'):
        repo.register(object())
    assert repo.get('x') is original


def test_select_is_an_independent_subset_of_the_original_instances() -> None:
    a, b = tool('a'), tool('b')
    repo = ToolRepo(a, b, builtin=False)
    selected = repo.select(['b'])
    assert selected.tools == (b,) and repo.tools == (a, b)
    selected.unregister('b')
    assert repo.get('b') is b
    assert not repo.select([]).tools
    for names in [['missing'], ['a', 'missing'], ['a', 'a']]:
        with pytest.raises(ValueError):
            repo.select(names)
    with pytest.raises(TypeError):
        repo.select('a')


def test_stale_receipts_cannot_remove_new_bindings_or_other_sources() -> None:
    repo = ToolRepo(builtin=False)
    stale = repo.register(tool('x'))
    repo.unregister('x')
    replacement = tool('x')
    current = repo.register(replacement)
    other = tool('other')
    repo.register(other)
    stale.close()
    assert repo.tools == (replacement, other)
    current.close()
    current.close()
    assert repo.tools == (other,)


def test_merge_deduplicates_actual_identity_only_and_does_not_modify_inputs() -> None:
    original = tool('x')
    a, b = ToolRepo(original, builtin=False), ToolRepo(original, tool('y'), builtin=False)
    union = a.merge(b)
    assert union.get('x') is original and union.names() == {'x', 'y'}
    assert a.names() == {'x'}
    with pytest.raises(ValueError, match='Duplicate'):
        a.merge(ToolRepo(tool('x'), builtin=False))
    assert a.get('x') is original


async def test_publication_never_starts_a_model_or_rearms_retired_selection() -> None:
    def forbidden(*args, **kwargs):
        pytest.fail('Publication must not start a model')
    async with Agent(forbidden) as agent:
        first = ToolRepo(tool('a'), tool('b'), builtin=False)
        first.publish(agent)
        agent.set_active_tools(('a',), expected_revision=agent.tool_selection_revision)
        revision = agent.tool_selection_revision
        first.publish(agent)
        assert agent.tool_selection_revision == revision
        ToolRepo(tool('b'), tool('c'), builtin=False).publish(agent)
        assert agent.active_tool_selection == ()
        ToolRepo(tool('a'), builtin=False).publish(agent)
        assert agent.active_tool_selection == ()
        assert not agent.messages


def test_import_has_no_global_repo_or_execution_loop() -> None:
    from xdog.agent.tools import repo
    assert not hasattr(repo, '_repo')
    with pytest.raises(RuntimeError):
        asyncio.get_running_loop()


async def test_builtin_bash_cwd_is_not_shared(tmp_path: Path) -> None:
    first, second = tmp_path / 'a', tmp_path / 'b'
    first.mkdir()
    second.mkdir()
    a, b = ToolRepo(cwd=first).get('bash'), ToolRepo(cwd=second).get('bash')
    await a.execute('cd', {'command': 'cd ..'})
    result = await b.execute('pwd', {'command': 'pwd'})
    assert not result.is_error and str(second) in str(result.content)
