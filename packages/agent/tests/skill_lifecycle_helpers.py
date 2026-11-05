"""Skill bodies have an owning prompt run, not an anonymous history message."""

from __future__ import annotations

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.agent.skills import SkillRepo
from xdog.ai.types import AssistantMessage, SystemPromptBlock, TextContent
from xdog.ai.utils.event_stream import EventStream


@pytest.fixture
def manager(tmp_path):
    for slug, scope, body in [("guard", "session", "SESSION_GUARD"), ("once", "turn", "TURN_PROCEDURE")]:
        directory = tmp_path / slug
        directory.mkdir()
        (directory / "SKILL.md").write_text(
            f"---\nname: {slug}\ndescription: procedure\nmetadata:\n  scope: {scope}\n---\n{body}"
        )
    return SkillRepo(builtin=False, packaged={}).register(directory=tmp_path, name="shared")


def make_agent(manager, selected=(), **kwargs):
    calls = []

    def stream(model, context, options):
        calls.append(context)
        return EventStream.empty(AssistantMessage(content=(TextContent(text="ok"),)))

    base = (SystemPromptBlock(text="STATIC", cache=True), SystemPromptBlock(text="DYNAMIC", cache=False))
    return Agent(
        stream,
        config=AgentConfig(model="offline/test", system_prompt=base),
        skills=manager,
        active_skills=selected,
        **kwargs,
    ), calls


async def prompt(agent, text="user request"):
    async for _ in await agent.prompt(text):
        pass
    await agent.wait_for_idle()
