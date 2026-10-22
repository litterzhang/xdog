"""Thin SDK default-tool convenience for callers without an existing owner."""
from __future__ import annotations

from pathlib import Path

from xdog.agent import AgentTool
from xdog.agent.tools import ToolContext, ToolRegistry


def get_default_tools(working_dir: Path) -> list[AgentTool]:
    """Use SDK defaults; session owners reuse their own registry instead."""
    return ToolRegistry().create_tools(context=ToolContext(cwd=working_dir))
