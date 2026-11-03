"""Compose the stable host contract and mutable group context.

Tool schemas and the skill index are supplied separately by Agent. Workspace
files, environment, memory and optional bootstrap remain non-cacheable context.
"""
from __future__ import annotations

from pathlib import Path

from xdog.ai.types import SystemPromptBlock
from xdog.claw.core.identity.context import RULES as IDENTITY_RULES
from xdog.claw.core.prompt.environment import environment_section
from xdog.claw.core.prompt.templates import MEMORY_GUIDANCE, SYSTEM_RULES
from xdog.claw.core.prompt.workspace import format_memory_section, load_memory_section, load_workspace_overrides


def build_system_prompt(
    workspace_dir: Path,
    *,
    model: str = "",
    bootstrap_content: str | None = None,
    memory_content: str = "",
) -> tuple[SystemPromptBlock, ...]:
    """Build host rules plus group context without duplicating tool/skill catalogs."""
    static_text = "\n\n".join((SYSTEM_RULES, IDENTITY_RULES, MEMORY_GUIDANCE))
    dynamic_parts = [
        load_workspace_overrides(workspace_dir),
        environment_section(model),
        format_memory_section(memory_content) if memory_content else load_memory_section(workspace_dir),
    ]
    if bootstrap_content:
        dynamic_parts.append(f"# Bootstrap\n\n{bootstrap_content}")
    return (
        SystemPromptBlock(text=static_text, cache=True),
        SystemPromptBlock(text="\n\n".join(part for part in dynamic_parts if part), cache=False),
    )
