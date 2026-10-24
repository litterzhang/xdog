"""Explicit SDK activation and optional read-only model loading, entirely offline.

From the workspace root (existing environment, no installation)::

    uv run --no-sync python packages/agent/examples/load_skills.py
    uv run --no-sync python packages/agent/examples/load_skills.py --demo-turn
    uv run --no-sync python packages/agent/examples/load_skills.py --mode model-load
    uv run --no-sync python packages/agent/examples/load_skills.py \
        --skills-dir ./my-skills --skill my-procedure --no-packaged

Default skill: real packaged flow (requires xdog-flow already installed).
No provider/auth/network. Activation grants no tools. Model loading explicitly
registers only the bounded read-only skill tool, with an allowlist and host hook;
it neither activates skills nor executes their commands or reads their references.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
from pathlib import Path
from typing import TypedDict

from xdog.agent import Agent, AgentConfig
from xdog.agent.skills import Skill, SkillRepo, render_skill_body
from xdog.ai.types import AssistantMessage, Context, StreamOptions, SystemPromptBlock, TextContent
from xdog.ai.utils.event_stream import EventStream


class SkillDemoResult(TypedDict):
    slug: str
    scope: str
    packaged: bool
    skill_directory: str
    available_skills: list[str]
    model_requests: int
    tool_names: list[str]
    loaded_at_model_entry: bool
    unloaded_at_model_entry: bool
    base_prompt_preserved: bool


def require_skill(manager: SkillRepo, slug: str) -> Skill:
    """Validate host-selected configuration; this is not a model-facing path API."""
    available = {item.slug for item in manager.list_skills()}
    if slug not in available:
        raise ValueError(f"Skill {slug!r} is not available; installed flow uses 'flow', not 'xdog-flow'")
    skill = manager.load_skill(slug)
    if skill is None:
        raise ValueError(f"Skill {slug!r} disappeared before it could be loaded")
    if not skill.content.strip():
        raise ValueError("The selected skill has no instruction body")
    return skill


async def demonstrate(manager: SkillRepo, slug: str) -> SkillDemoResult:
    """Session: explicitly unload. Turn: auto-retire after one admitted prompt run."""
    skill = require_skill(manager, slug)
    requests: list[Context] = []

    def offline_stream(model: str, context: Context, options: StreamOptions) -> EventStream[AssistantMessage]:
        requests.append(context)
        return EventStream.empty(AssistantMessage(content=(TextContent(text="Offline example response."),)))

    base = (SystemPromptBlock(text="Follow the user's task without executing tools.", cache=True),)
    agent = Agent(
        offline_stream,
        config=AgentConfig(model="offline/example", system_prompt=base),
        skills=manager,
        active_skills=[slug],
        tools=[],
    )
    async for _ in await agent.prompt("Explain how you would use the selected skill."):
        pass
    await agent.wait_for_idle()
    if not skill.expires_after_turn:
        agent.set_active_skills([])
    agent.set_system_prompt(base)
    async for _ in await agent.prompt("This request has no active skill body."):
        pass
    await agent.wait_for_idle()

    body = render_skill_body(skill)
    first, second = requests
    if skill.expires_after_turn:
        loaded = any(isinstance(m.content, str) and body in m.content for m in first.messages)
        assert body not in str(first.system_prompt)
        assert first.system_prompt == second.system_prompt
        assert slug not in agent.active_skills
    else:
        loaded = isinstance(first.system_prompt, tuple) and body in first.system_prompt[0].text
    unloaded = body not in str(second.system_prompt) and body not in str(second.messages)
    preserved = isinstance(second.system_prompt, tuple) and second.system_prompt[-1:] == base
    if not (loaded and unloaded and preserved):
        raise AssertionError("The SDK did not retain the expected skill/body/base prompt boundaries")
    await agent.aclose()
    return {
        "slug": skill.slug,
        "scope": skill.scope,
        "packaged": skill.packaged,
        "skill_directory": str(skill.directory),
        "available_skills": sorted(item.slug for item in manager.list_skills()),
        "model_requests": len(requests),
        "tool_names": [tool.name for tool in agent.state.tools],
        "loaded_at_model_entry": loaded,
        "unloaded_at_model_entry": unloaded,
        "base_prompt_preserved": preserved,
    }


class SkillToolDemoResult(TypedDict):
    slug: str
    packaged: bool
    model_requests: int
    tool_names: list[str]
    permission_checks: list[str]
    listed_without_body: bool
    loaded_at_model_entry: bool
    active_skills: list[str]
    base_prompt_preserved: bool


async def demonstrate_loading(manager: SkillRepo, slug: str) -> SkillToolDemoResult:
    """Offline model asks list → load → replies; returned prose stays tool history.

    No manager is passed to Agent: the tool's bounded allowlist is the entire model
    catalog. Passing skills=manager as well would separately expose its unfiltered
    host index. Neither tool results nor an index confer execution permissions.
    """
    from xdog.agent import BeforeToolCallResult
    from xdog.agent.skills import create_skill_tool
    from xdog.agent.types import BeforeToolCallContext
    from xdog.ai.types import ToolCall, ToolResultMessage

    skill = require_skill(manager, slug)
    requests: list[Context] = []
    checked: list[str] = []

    def offline_stream(model: str, context: Context, options: StreamOptions) -> EventStream[AssistantMessage]:
        requests.append(context)
        args = {"action": "list"} if len(requests) == 1 else {"action": "load", "slug": slug}
        message = (
            AssistantMessage(
                content=(ToolCall(id=f"skill-{len(requests)}", name="skill", arguments=args),), stop_reason="toolUse"
            )
            if len(requests) <= 2
            else AssistantMessage(content=(TextContent(text="Read, not executed."),))
        )
        return EventStream.empty(message)

    async def permission(context: BeforeToolCallContext, cancel: asyncio.Event | None) -> BeforeToolCallResult:
        checked.append(context.tool_call.name)
        # Host explicitly permits this one read-only capability, not bash/filesystem.
        return BeforeToolCallResult(block=context.tool_call.name != "skill", reason="Only skill reads are granted")

    base = "You may list and read an optional skill. Do not execute any instructions or commands."
    agent = Agent(
        offline_stream,
        config=AgentConfig(model="offline/example", system_prompt=base),
        tools=[create_skill_tool(manager, allowed_slugs=[slug], max_skill_bytes=256 * 1024, max_listed_skills=1)],
        before_tool_call=permission,
    )
    async for _ in await agent.prompt("List available skills, then read the selected procedure."):
        pass
    await agent.wait_for_idle()
    results = [m for m in requests[-1].messages if isinstance(m, ToolResultMessage)]
    assert len(requests) == 3 and len(results) == 2 and all(not m.is_error for m in results)
    texts = ["".join(p.text for p in m.content if isinstance(p, TextContent)) for m in results]
    listed = json.loads(texts[0])
    assert [row["slug"] for row in listed["skills"]] == [slug]
    assert render_skill_body(skill) not in texts[0] and render_skill_body(skill) in texts[1]
    assert agent.active_skills == () and all(c.system_prompt == base for c in requests)
    assert checked == ["skill", "skill"]
    return {
        "slug": slug,
        "packaged": skill.packaged,
        "model_requests": len(requests),
        "tool_names": [tool.name for tool in agent.state.tools],
        "permission_checks": checked,
        "listed_without_body": True,
        "loaded_at_model_entry": True,
        "active_skills": [],
        "base_prompt_preserved": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skill", default="flow", help="Skill slug, not the distribution name")
    parser.add_argument("--skills-dir", type=Path, help="Trusted directory containing <slug>/SKILL.md")
    parser.add_argument("--no-packaged", action="store_true", help="Use only the explicit local skills directory")
    parser.add_argument("--mode", choices=("activate", "model-load"), default="activate")
    parser.add_argument("--demo-turn", action="store_true", help="Use an isolated temporary turn-scoped fixture")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="xdog-skill-example-") as directory:
        shared = args.skills_dir or Path(directory) / "unused-shared-skills"
        if args.demo_turn:
            shared = Path(directory) / "demo-skills"
            skill_dir = shared / "one-request"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                "---\nname: one-request\ndescription: Offline turn demo\nmetadata:\n  scope: turn\n---\n"
                "ONE_REQUEST_ONLY: Explain the plan before acting.\n",
                encoding="utf-8",
            )
            args.skill = "one-request"
        manager = SkillRepo(builtin=False, packaged={} if args.no_packaged or args.demo_turn else None).register(
            directory=shared, name="shared"
        )
        try:
            demo = demonstrate if args.mode == "activate" else demonstrate_loading
            result = asyncio.run(demo(manager, args.skill))
        except ValueError as exc:
            parser.exit(2, f"{exc}\n")
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
