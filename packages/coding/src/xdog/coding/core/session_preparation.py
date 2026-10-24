"""Detached filesystem prompt inputs; placement semantics remain Agent-owned."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from xdog.agent.skills import SkillRepo
from xdog.agent.skills.types import SkillPlacement
from xdog.coding.core import resource_loader
from xdog.coding.core.resource_loader import ContextFile


@dataclass(frozen=True)
class PromptResources:
    files: tuple[ContextFile, ...]
    skills: SkillPlacement

    @classmethod
    def read(cls, directory: Path, manager: SkillRepo | None, active: tuple[str, ...]) -> PromptResources:
        return cls(tuple(resource_loader.discover_context_files(directory)), SkillPlacement.read(manager, active))
