"""Thin SDK default-tool convenience for callers without an existing owner."""
from __future__ import annotations

from pathlib import Path

from xdog.agent import AgentTool, ToolRepo


def get_default_tools(working_dir: Path) -> list[AgentTool]:
    """Use SDK defaults; session owners retain their own repository instead."""
    return list(ToolRepo(cwd=working_dir).tools)
