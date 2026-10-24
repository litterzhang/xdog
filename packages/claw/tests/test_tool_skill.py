"""Tests for the skill tool — the path by which a claw agent reads a skill.

There are two places skill text reaches a model: coding's system prompt and
this tool. The first was fixed to resolve a skill's file references; this one
was left returning the raw body, so the same bug stayed live behind a different
caller. These tests pin the contract on this side of it.
"""
from pathlib import Path

import pytest
from xdog.agent.skills import SkillRepo
from xdog.claw.core.tools.tool_skill import SkillTool


@pytest.fixture
def ctx(tmp_path: Path) -> dict:
    shared = tmp_path / "skills"
    d = shared / "deploy"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: deploy\ndescription: ship it\n---\n\n"
        "Read `references/CHECKLIST.md`, then run ${SKILL_DIR}/scripts/go.sh",
        encoding="utf-8",
    )
    return {"_skill_repo": SkillRepo(builtin=False).register(directory=shared, name="shared")}


async def test_loading_a_skill_tells_the_agent_where_its_files_are(ctx: dict) -> None:
    out = await SkillTool().load(ctx, slug="deploy")

    skill_dir = str(ctx["_skill_repo"].load_skill("deploy").directory)
    assert skill_dir in out, "a relative path is unusable without the directory"
    assert "${SKILL_DIR}" not in out, "the variable must be substituted"
    assert f"{skill_dir}/scripts/go.sh" in out


async def test_loading_a_missing_skill_says_so_rather_than_raising(ctx: dict) -> None:
    out = await SkillTool().load(ctx, slug="nope")

    assert "not found" in out.lower()


async def test_the_skill_name_leads_the_output(ctx: dict) -> None:
    out = await SkillTool().load(ctx, slug="deploy")

    assert out.startswith("# deploy")
