"""Skills — reusable procedural knowledge an agent can discover and load.

A skill is a directory holding a ``SKILL.md``: YAML frontmatter (``name``,
``description``) followed by a markdown body. The frontmatter is cheap enough to
keep in the system prompt for every skill on disk; the body is only read when the
agent decides it needs that skill. That two-step disclosure is the whole point —
a hundred skills cost a hundred one-line descriptions, not a hundred documents.

**Who does what.** A `SkillManager` answers *which skills exist and where* —
each product builds one over its own directories, because only it knows where to
look: flow beside its workflow file, coding and claw in their group and shared
directories, all of them plus whatever packages ship. The `Agent` answers *where
each part goes in the request*, once, for everybody:

- **the index** (one line per skill) goes at the front of the system prompt: it
  is small, never changes, and nothing can ask for a skill it was not told
  exists.
- **an active session-scoped body** goes after the index, still in the prefix,
  because it is fixed for the session and the prompt cache keeps it.
- **an active `scope: turn` body** goes in as a message, after the prefix. It
  will be removed again, and adding then removing it from the prefix costs a
  full uncached re-send twice.

That split is the whole point. It used to be neither: three products each
resolved skills their own way *and* decided placement their own way, so the same
question had three answers and one of them was wrong.

This module deliberately depends on nothing but the standard library, because it
is shared: ``xdog.claw`` lets an agent *author* skills as procedural memory,
``xdog.coding`` reads them to answer slash commands, and packages can ship their
own — ``xdog.flow`` carries one at ``xdog/flow/skills/`` teaching an agent to
write flow workflows.
"""

from xdog.agent.skills.discovery import load_packaged_skill, packaged_skills
from xdog.agent.skills.manager import SkillManager
from xdog.agent.skills.render import (
    render_skill_body,
    resolvable_skill,
    resolve_skills,
    skills_preamble,
)
from xdog.agent.skills.tool import create_skill_tool
from xdog.agent.skills.types import Skill, SkillPlacement

__all__ = [
    "Skill",
    "SkillPlacement",
    "SkillManager",
    "create_skill_tool",
    "load_packaged_skill",
    "packaged_skills",
    "render_skill_body",
    "resolvable_skill",
    "resolve_skills",
    "skills_preamble",
]
