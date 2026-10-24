"""Skill tool — create, patch, remove, list, load skills (procedural memory)."""
from __future__ import annotations

from typing import Any

from xdog.agent.core import AgentTool
from xdog.agent.skills import render_skill_body
from xdog.agent.tool_def import Param, ToolDef, action


class SkillTool(ToolDef):
    name = "skill"
    description = "Manage reusable skills (procedural memory) — create, update, remove, list, load."
    required_ctx = ("_skill_repo",)

    @action("create", description="Create a new skill from a successful workflow",
            slug=Param("string", required=True, description="URL-safe identifier (e.g. deploy-to-prod)"),
            skill_name=Param("string", required=True, description="Human-readable name"),
            content=Param("string", required=True, description="Skill content in markdown"),
            skill_description=Param("string", description="One-line description"),
            scope=Param("string", description="'shared' (all groups, default) or 'group' (this group only)",
                        enum=["shared", "group"]))
    async def create(self, ctx: dict[str, Any], slug: str, skill_name: str, content: str,
                     skill_description: str = "", scope: str = "shared") -> str:
        manager = ctx["_skill_repo"]
        skill = manager.save_skill(slug, content, name=skill_name,
                                   description=skill_description, scope=scope)
        return f"Skill created: {skill.name} [{skill.slug}]"

    @action("patch", description="Append content to an existing skill (token-efficient)",
            slug=Param("string", required=True, description="Skill slug"),
            patch=Param("string", required=True, description="Content to append"))
    async def patch(self, ctx: dict[str, Any], slug: str, patch: str) -> str:
        manager = ctx["_skill_repo"]
        skill = manager.patch_skill(slug, patch)
        if skill is None:
            return f"Skill not found: {slug}"
        return f"Skill updated: {skill.name} [{skill.slug}]"

    @action("remove", description="Delete a skill",
            slug=Param("string", required=True, description="Skill slug"))
    async def remove(self, ctx: dict[str, Any], slug: str) -> str:
        manager = ctx["_skill_repo"]
        if manager.remove_skill(slug):
            return f"Skill removed: {slug}"
        return f"Skill not found: {slug}"

    @action("list", description="List all available skills with descriptions")
    async def list(self, ctx: dict[str, Any]) -> str:
        manager = ctx["_skill_repo"]
        skills = manager.list_skills()
        if not skills:
            return "No skills saved yet."
        lines = []
        for s in skills:
            desc = f" — {s.description}" if s.description else ""
            lines.append(f"- **{s.name}** [{s.slug}]{desc}")
        return "\n".join(lines)

    @action("load", description="Load the full content of a skill",
            slug=Param("string", required=True, description="Skill slug"))
    async def load(self, ctx: dict[str, Any], slug: str) -> str:
        manager = ctx["_skill_repo"]
        skill = manager.load_skill(slug)
        if skill is None:
            return f"Skill not found: {slug}"
        # Rendered, not raw: a skill may point at files beside its SKILL.md, and
        # those paths mean nothing to a model that has not been told where the
        # skill lives. Handing over `skill.content` looks right and quietly
        # yields an agent hunting for files in the working directory.
        return f"# {skill.name}\n\n{render_skill_body(skill)}"


def create_skill_tool() -> AgentTool:
    return SkillTool().build()
