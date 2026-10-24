"""Detached skill placement keeps the shared runtime's existing prefix/lifetime rules."""
import asyncio

import pytest
from xdog.agent.skills.types import SkillPlacement

from packages.agent.tests.test_skill_lifecycle import make_agent, prompt
from packages.agent.tests.test_skill_lifecycle import manager as manager


@pytest.mark.parametrize("constructor", [False, True])
async def test_prepared_placement_never_rereads_disk_and_preserves_turn_skill_lifetime(
    manager, monkeypatch, constructor,
):
    placement = await asyncio.to_thread(SkillPlacement.read, manager, ("guard", "once"))

    def unexpected(*args):
        raise AssertionError("Prepared adoption must not resolve filesystem instructions again")

    if constructor:
        monkeypatch.setattr(manager, "skills_summary", unexpected)
        monkeypatch.setattr(manager, "load_skill", unexpected)
        agent, calls = make_agent(manager, ("guard", "once"), prepared_skills=placement)
    else:
        agent, calls = make_agent(manager)
        monkeypatch.setattr(manager, "skills_summary", unexpected)
        monkeypatch.setattr(manager, "load_skill", unexpected)
        agent.set_active_skills(("guard", "once"), prepared_skills=placement)
    prefix = agent.state.system_prompt
    assert "SESSION_GUARD" in str(prefix) and "TURN_PROCEDURE" not in str(prefix)
    await prompt(agent)
    assert "TURN_PROCEDURE" in str(calls[0].messages) and "TURN_PROCEDURE" not in str(agent.messages)
    await prompt(agent)
    assert "TURN_PROCEDURE" not in str(calls[1].messages)
    assert agent.state.system_prompt == prefix


def test_prepared_placement_cannot_be_adopted_for_a_different_selection(manager):
    placement = SkillPlacement.read(manager, ("guard",))
    with pytest.raises(ValueError, match="Prepared skills"):
        make_agent(manager, ("once",), prepared_skills=placement)
    agent, _ = make_agent(manager)
    with pytest.raises(ValueError, match="Prepared skills"):
        agent.set_active_skills(("once",), prepared_skills=placement)


@pytest.mark.parametrize('selection', [('missing',), ('guard', 'missing')])
def test_explicit_missing_skill_fails_constructor_read_and_atomic_selection(manager, selection):
    with pytest.raises(ValueError, match='missing'):
        make_agent(manager, selection)
    with pytest.raises(ValueError, match='missing'):
        SkillPlacement.read(manager, selection)
    agent, calls = make_agent(manager, ('guard', 'once'))
    before = agent.dump()
    with pytest.raises(ValueError, match='missing'):
        agent.set_active_skills(selection)
    assert agent.dump() == before and not calls


def test_active_selection_requires_manager_or_complete_prepared_placement():
    from xdog.agent import Agent

    with pytest.raises(ValueError, match='SkillRepo'):
        Agent(lambda *_: None, active_skills=('absent',))
    with pytest.raises(ValueError, match='SkillRepo'):
        SkillPlacement.read(None, ('absent',))


@pytest.mark.parametrize('failure', [OSError('disk error'), UnicodeError('bad encoding')])
def test_active_load_errors_propagate_without_changing_selection(manager, monkeypatch, failure):
    agent, calls = make_agent(manager, ('guard', 'once'))
    before = agent.dump()

    def broken(slug):
        raise failure

    monkeypatch.setattr(manager, 'load_skill', broken)
    with pytest.raises(type(failure)) as caught:
        agent.set_active_skills(('guard',))
    assert caught.value is failure
    assert agent.dump() == before and not calls
    with pytest.raises(type(failure)):
        SkillPlacement.read(manager, ('guard',))


@pytest.mark.parametrize('change', ['resolved', 'fixed', 'transient', 'empty', 'scope', 'identity'])
def test_prepared_placement_cannot_bypass_strict_activation(manager, change):
    from dataclasses import replace

    placement = SkillPlacement.read(manager, ('guard', 'once'))
    if change in ('resolved', 'fixed', 'transient'):
        placement = replace(placement, **{change: '' if change == 'fixed' else ()})
    else:
        field, value = {'empty': ('content', '  '), 'scope': ('scope', 'invalid'),
                        'identity': ('slug', 'other')}[change]
        placement = replace(placement, resolved=(replace(placement.resolved[0], **{field: value}),
                                                  placement.resolved[1]))
    agent, _ = make_agent(manager, ('once',))
    before = agent.dump()
    with pytest.raises(ValueError):
        agent.set_active_skills(('guard', 'once'), prepared_skills=placement)
    assert agent.dump() == before
    with pytest.raises(ValueError):
        make_agent(manager, ('guard', 'once'), prepared_skills=placement)


def test_empty_active_body_fails_but_index_discovery_is_optional(manager, monkeypatch, caplog):
    from dataclasses import replace

    real_load = manager.load_skill
    monkeypatch.setattr(manager, 'load_skill', lambda slug: replace(real_load(slug), content='\n '))
    with pytest.raises(ValueError, match='empty'):
        make_agent(manager, ('guard',))
    monkeypatch.setattr(manager, 'load_skill', real_load)

    def broken_index():
        raise OSError('optional catalog unreadable')

    monkeypatch.setattr(manager, 'skills_summary', broken_index)
    agent, _ = make_agent(manager, ('guard',))
    assert agent.active_skills == ('guard',) and 'SESSION_GUARD' in str(agent.state.system_prompt)
    assert 'optional skill index' in caplog.text
    index_only, _ = make_agent(manager)
    assert not index_only.active_skills
