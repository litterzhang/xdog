"""Memory tool — get, search, write. Uses ToolDef framework.

Delegates storage to ``DailyLog`` and ``LongTermMemory`` via the
``MemoryManager`` search function passed in ``ctx["_memory_search"]``.
Falls back to inline file I/O when the search function is unavailable.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from xdog.agent.core import AgentTool
from xdog.agent.tool_def import Param, ToolDef, action
from xdog.claw.core.memory.simple_search import SimpleMemorySearch
from xdog.claw.core.memory.types import MemoryChunk

MEMORY_MAX_CHARS = 2200


class MemoryTool(ToolDef):
    name = "memory"
    description = "Agent memory: get (read file), search (keyword/semantic), write (daily log or MEMORY.md)."
    required_ctx = ("workspace_dir",)

    @action("get", description="Read a file from workspace",
            filename=Param("string", required=True, description="File path relative to workspace"))
    async def get(self, ctx: dict[str, Any], filename: str) -> str:
        ws = Path(ctx["workspace_dir"]).resolve()
        path = (ws / filename).resolve()
        if not path.exists():
            return f"File not found: {filename}"
        if not path.is_relative_to(ws):
            return "Access denied: path outside workspace"
        return path.read_text(encoding="utf-8")

    @action("search", description="Search memory by keyword or semantic similarity",
            query=Param("string", required=True, description="Search query"),
            top_k=Param("integer", default=5, description="Max results"))
    async def search(self, ctx: dict[str, Any], query: str, top_k: int = 5) -> str:
        search_fn = ctx.get("_memory_search")
        if search_fn is None:
            search_fn = SimpleMemorySearch(Path(ctx["workspace_dir"])).search
        chunks: list[MemoryChunk] = await search_fn(query, top_k=top_k)
        if not chunks:
            return "No results found."
        return "\n\n".join(f"[{c.source}] {c.text[:300]}" for c in chunks)

    @action("write", description="Write to daily log or MEMORY.md",
            target=Param("string", required=True, enum=["daily", "memory"]),
            text=Param("string", required=True, description="Content to write"))
    async def write(self, ctx: dict[str, Any], target: str, text: str) -> str:
        memory_manager = ctx.get("_memory_manager")
        if memory_manager is not None:
            if target == "daily":
                memory_manager.daily_log.append(text)
                return "Written to daily log."
            elif target == "memory":
                current = memory_manager.long_term.read()
                if len(current) + len(text) > MEMORY_MAX_CHARS:
                    return (
                        f"Error: MEMORY.md is at capacity ({len(current)}/{MEMORY_MAX_CHARS} chars). "
                        "Consolidate or remove old entries before adding new ones. "
                        "Use memory (action: get, filename: MEMORY.md) to review current content."
                    )
                memory_manager.long_term.append(text)
                return "Appended to MEMORY.md."
        else:
            # Fallback: direct file I/O when no manager available
            ws = Path(ctx["workspace_dir"])
            if target == "daily":
                from xdog.claw.core.memory.daily_log import DailyLog
                DailyLog(ws / "memory").append(text)
                return "Written to daily log."
            elif target == "memory":
                from xdog.claw.core.memory.long_term import LongTermMemory
                LongTermMemory(ws).append(text)
                return "Appended to MEMORY.md."
        return f"Unknown target: {target}"


def create_memory_tool() -> AgentTool:
    return MemoryTool().build()
