"""Skill types — frozen dataclass for skill metadata and content."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from xdog.agent.skills.repo import SkillRepo


@dataclass(frozen=True)
class Skill:
    """A reusable skill (procedural memory).

    Skills are markdown files that encode workflows, procedures, and
    learned approaches. The agent creates them after completing complex
    tasks and loads them when similar work arises.
    """

    name: str = ""
    slug: str = ""
    description: str = ""
    content: str = ""
    created: str = ""
    updated: str = ""
    path: Path | None = None
    #: True when the skill came from an installed package rather than a user
    #: directory. Packaged skills are read-only — they live in site-packages,
    #: and editing or deleting one would corrupt the installation.
    packaged: bool = False
    #: How long the skill stays active once invoked: ``"session"`` (default,
    #: until unloaded) or ``"turn"`` (one turn, then it retires itself).
    #:
    #: Declared by the author under the standard's ``metadata`` field, because
    #: only the author knows which kind of skill this is. A one-shot procedure
    #: should get out of the way; a guardrail — "always run the tests before
    #: deploying" — must not, and neither a timer nor the model it constrains
    #: is in a position to tell those apart.
    scope: str = "session"

    @property
    def expires_after_turn(self) -> bool:
        return self.scope == "turn"

    @property
    def directory(self) -> Path | None:
        """The skill's own directory — what its relative paths resolve against."""
        return self.path.parent if self.path is not None else None


def validate_active_skill(slug: str, skill: Skill | None) -> None:
    """Explicit activation must bind the exact slug and nonempty instructions."""
    from xdog.agent.skills.repo import is_valid_skill_name

    if not isinstance(slug, str) or not is_valid_skill_name(slug):
        raise ValueError(f"Invalid active skill slug: {slug!r}")
    if (not isinstance(skill, Skill) or skill.slug != slug or skill.scope not in ("session", "turn")
            or not isinstance(skill.content, str) or not skill.content.strip()):
        raise ValueError(f"Active skill {slug!r} is missing, empty or has an invalid binding")


@dataclass(frozen=True)
class SkillPlacement:
    """Detached prompt placement; hosts may resolve filesystem skills in owned IO."""

    slugs: tuple[str, ...]
    index: str
    fixed: str
    transient: tuple[Skill, ...]
    resolved: tuple[Skill, ...]

    @classmethod
    def read(cls, manager: SkillRepo | None, selected: Sequence[str]) -> SkillPlacement:
        # Same placement engine as ordinary Agent construction/selection, not a
        # host-specific skill resolver or a second cache/persistence authority.
        from xdog.agent.agent import _place_skills
        index, fixed, transient, resolved = _place_skills(manager, selected)
        return cls(tuple(dict.fromkeys(selected)), index, fixed, tuple(transient), tuple(resolved.values()))

    def unpack(self) -> tuple[str, str, list[Skill], dict[str, Skill]]:
        from xdog.agent.agent import _skill_preamble

        if (self.slugs != tuple(dict.fromkeys(self.slugs))
                or tuple(skill.slug for skill in self.resolved) != self.slugs):
            raise ValueError("Prepared skills do not bind every selected slug exactly once")
        for slug, skill in zip(self.slugs, self.resolved, strict=True):
            validate_active_skill(slug, skill)
        if (self.transient != tuple(skill for skill in self.resolved if skill.expires_after_turn)
                or self.fixed != _skill_preamble([skill for skill in self.resolved if not skill.expires_after_turn])
                or not isinstance(self.index, str)):
            raise ValueError("Prepared skills have inconsistent prompt placement")
        return self.index, self.fixed, list(self.transient), {skill.slug: skill for skill in self.resolved}
