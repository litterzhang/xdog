"""Prompt builder — assembles static base + workspace overrides + dynamic context.

Returns a tuple of ``SystemPromptBlock`` for prompt caching support.
The static base is cacheable (identical across turns); workspace and
dynamic sections change per-turn.

Usage::

    from xdog.claw.core.prompt import build_system_prompt
    blocks = build_system_prompt(
        workspace_dir,
        tools=runtime.tools,
        model=runtime.model,
    )
    # blocks is tuple[SystemPromptBlock, ...] — pass to Context.system_prompt
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from xdog.ai.types import SystemPromptBlock
from xdog.claw.core.identity.context import RULES as IDENTITY_RULES
from xdog.claw.core.prompt.environment import environment_section
from xdog.claw.core.prompt.sections import (
    action_safety_section,
    identity_section,
    memory_guidance_section,
    output_section,
    system_rules_section,
    task_execution_section,
    tone_section,
    tool_usage_section,
)
from xdog.claw.core.prompt.workspace import (
    load_memory_section,
    load_workspace_overrides,
)


def build_system_prompt(
    workspace_dir: Path,
    *,
    tools: list[Any] | None = None,
    model: str = "",
    bootstrap_content: str | None = None,
    memory_content: str = "",
) -> tuple[SystemPromptBlock, ...]:
    """Assemble the complete system prompt as cacheable blocks.

    Returns a tuple of ``SystemPromptBlock``:
    - Block 0: static base (cacheable — identical across turns)
    - Block 1: dynamic content (per-turn — workspace, env, memory)

    Parameters
    ----------
    workspace_dir:
        Path to the workspace directory containing identity files.
    tools:
        List of AgentTool instances for dynamic tool guidance.
    model:
        Model name for the environment section.
    bootstrap_content:
        One-time bootstrap content (from BOOTSTRAP.md).
    """
    # --- Static base (cacheable) ---
    static_parts = [
        identity_section(),
        system_rules_section(),
        IDENTITY_RULES,
        task_execution_section(),
        action_safety_section(),
        tool_usage_section(tools),
        memory_guidance_section(),
        output_section(),
        tone_section(),
    ]
    static_text = "\n\n".join(static_parts)

    # --- Dynamic content (per-turn) ---
    dynamic_parts: list[str] = []

    overrides = load_workspace_overrides(workspace_dir)
    if overrides:
        dynamic_parts.append(overrides)

    dynamic_parts.append(environment_section(model))

    # Memory: use provided content (frozen snapshot) or load from disk
    if memory_content:
        from xdog.claw.core.prompt.workspace import format_memory_section
        dynamic_parts.append(format_memory_section(memory_content))
    else:
        memory = load_memory_section(workspace_dir)
        if memory:
            dynamic_parts.append(memory)



    if bootstrap_content:
        dynamic_parts.append(f"# Bootstrap\n\n{bootstrap_content}")

    dynamic_text = "\n\n".join(dynamic_parts)

    return (
        SystemPromptBlock(text=static_text, cache=True),
        SystemPromptBlock(text=dynamic_text, cache=False),
    )
