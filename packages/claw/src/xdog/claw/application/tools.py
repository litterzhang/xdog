"""Application-owned composition of core and channel tools using Agent ToolRepo."""
from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from xdog.agent.capabilities import CapabilitySet
from xdog.agent.tools import ToolRepo
from xdog.claw.channels.xhs.tools import XhsPostTool
from xdog.claw.core.tools import AVAILABLE_TOOL_NAMES as CORE_TOOL_NAMES
from xdog.claw.core.tools import DEFAULT_TOOL_NAMES as DEFAULT_TOOL_NAMES
from xdog.claw.core.tools import new_tool_repo as core_tools
from xdog.claw.core.types import Group

AVAILABLE_TOOL_NAMES = CORE_TOOL_NAMES | {'xhs_post'}


def new_tool_repo(names: Iterable[str] | None = None, *, cwd: Path | None = None,
                  image_model: str = '') -> ToolRepo:
    if isinstance(names, str):
        raise TypeError('Tool names must be an iterable of names, not a string')
    selected = tuple(DEFAULT_TOOL_NAMES if names is None else names)
    if any(not isinstance(name, str) or not name or name.strip() != name for name in selected):
        raise ValueError('Tool names must be nonempty text without surrounding whitespace')
    if len(set(selected)) != len(selected):
        raise ValueError('Duplicate selected tool name')
    repo = core_tools(tuple(name for name in selected if name != 'xhs_post'), cwd=cwd, image_model=image_model)
    if 'xhs_post' in selected:
        repo.register(XhsPostTool())
    return repo.select(selected)


def group_tools(group: Group, workspace: Path) -> ToolRepo:
    selected = group.enabled_tools
    if selected is not None:
        declared = CapabilitySet('group').declared_tools | CapabilitySet('application').declared_tools
        selected = tuple(name for name in selected if name not in declared or name in AVAILABLE_TOOL_NAMES)
    return new_tool_repo(selected, cwd=workspace, image_model=group.image_model)
