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
