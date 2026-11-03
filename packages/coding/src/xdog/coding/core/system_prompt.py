"""Compose a stable host contract and separately mutable workspace context."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from xdog.ai.types import SystemPromptBlock, Tool
from xdog.coding.config import PlatformInfo
from xdog.coding.core.prompt_templates import (
    ENVIRONMENT_SECTION,
    HOST_CONTRACT,
    INSPECTION_HEADER,
    PROJECT_INSTRUCTIONS_HEADER,
    USER_INSTRUCTIONS_HEADER,
)
from xdog.coding.core.resource_loader import ContextFile


def build_system_prompt(
    working_dir: Path,
    platform_info: PlatformInfo,
    *,
    custom_instructions: str = "",
    project_files: tuple[ContextFile, ...] = (),
    inspection_tools: tuple[Tool, ...] = (),
    access_prompt: str = "",
) -> tuple[SystemPromptBlock, ...]:
    """Tool schemas/skills stay Agent-owned; inspection is only for text-only models."""
    environment = ENVIRONMENT_SECTION.format(
        working_dir=working_dir,
        os_name=platform_info.os_name,
        os_version=platform_info.os_version,
        python_version=platform_info.python_version,
        shell=platform_info.shell,
        date=date.today().isoformat(),
    )
    context = [environment]
    if custom_instructions:
        context.append(USER_INSTRUCTIONS_HEADER + "\n\n" + custom_instructions)
    if project_files:
        sources = [{'path': item.relative_path, 'instructions': item.content} for item in project_files]
        context.append(PROJECT_INSTRUCTIONS_HEADER + "\n\n" + json.dumps(sources, ensure_ascii=False))
    if inspection_tools:
        inventory = [{'name': tool.name, 'description': tool.description, 'parameters': tool.parameters}
                     for tool in inspection_tools]
        context.append(INSPECTION_HEADER + "\n\n" + json.dumps(inventory, ensure_ascii=False))
    if access_prompt:
        context.append(access_prompt)
    return (
        SystemPromptBlock(text=HOST_CONTRACT, cache=True),
        SystemPromptBlock(text="\n\n".join(context), cache=False),
    )
