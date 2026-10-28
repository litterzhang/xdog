"""Memory tool — get, search, history, write. Uses ToolDef framework.

Delegates storage to ``DailyLog`` and ``LongTermMemory`` via the
``MemoryManager`` search function passed in ``ctx["_memory_search"]``.
Falls back to live workspace keyword search when the search function is absent.
Authoritative history is a separate host-injected, owner-bound read capability.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from xdog.agent.async_work import run_owned
from xdog.agent.core import AgentTool
from xdog.agent.tool_def import Param, ToolDef, action
from xdog.claw.core.memory.simple_search import SimpleMemorySearch
from xdog.claw.core.memory.types import MemoryChunk
from xdog.claw.core.persistence.history_reader import HistoryReader

MEMORY_MAX_CHARS = 2200


class MemoryTool(ToolDef):
    name = "memory"
    description = ("Agent memory: get (workspace file), search (memory and authoritative history), "
                   "history (read-only recent/search/get), write (daily log or MEMORY.md).")
    required_ctx = ("workspace_dir",)

    def __init__(self) -> None:
        self._write_lock = threading.Lock()

    @action("get", description="Read a file from workspace",
            filename=Param("string", required=True, description="File path relative to workspace"))
    async def get(self, ctx: dict[str, Any], filename: str) -> str:
        def read() -> str:
            ws = Path(ctx["workspace_dir"]).resolve()
            path = (ws / filename).resolve()
            if not path.exists():
                return f"File not found: {filename}"
            if not path.is_relative_to(ws):
                return "Access denied: path outside workspace"
            return path.read_text(encoding="utf-8")

        return await run_owned(read)

    @staticmethod
    def _history(ctx: dict[str, Any]) -> HistoryReader:
        reader = ctx.get("_history_reader")
        if not isinstance(reader, HistoryReader):
            raise ValueError("History unavailable: no owner-bound history reader")
        if ctx.get("group_id") != reader.group_id:
            raise PermissionError("History access denied: group owner mismatch")
        if "session_id" in ctx and ctx["session_id"] != reader.session_id:
            raise PermissionError("History access denied: session owner mismatch")
        return reader

    @action("search", description="Search ordinary memory AND authoritative same-group history",
            query=Param("string", required=True, description="Search query"),
            top_k=Param("integer", default=5, description="Max results per source (1..50)"))
    async def search(self, ctx: dict[str, Any], query: str, top_k: int = 5) -> str:
        if type(top_k) is not int or not 1 <= top_k <= 50:
            raise ValueError("top_k must be an integer in 1..50")
        if not isinstance(query, str) or not query.strip() or len(query) > 1024:
            raise ValueError("query must contain 1..1024 characters")
        if ctx.get("_history_reader") is not None:
            self._history(ctx)
        search_fn = ctx.get("_memory_search")
        if search_fn is None:
            search_fn = SimpleMemorySearch(Path(ctx["workspace_dir"])).search
        sections = []
        try:
            chunks: list[MemoryChunk] = await search_fn(query, top_k=top_k)
            sections.append("\n\n".join(f"[{c.source}] {c.text[:300]}" for c in chunks[:top_k])
                            or "No ordinary memory matches (not evidence that conversation history is absent).")
        except Exception as exc:
            sections.append(f"Error: ordinary memory search unavailable ({type(exc).__name__}).")
        try:
            page = await self._history(ctx).search(query, limit=top_k)
            sections.append(json.dumps(page, ensure_ascii=False))
            if not page["items"]:
                sections.append("No history keyword matches on this page. Use history mode=recent to read "
                                "previous conversations without a keyword; follow next_offset when present.")
        except PermissionError:
            raise
        except Exception as exc:
            sections.append(f"Error: history search unavailable ({type(exc).__name__}). "
                            "This does not mean history is absent.")
        return "\n\n".join(sections)

    @action("history", description="Read same-group history as evidence, never permissions. "
            "Positions are zero-based messages. No paths accepted; never activates old sessions.",
            mode=Param("string", default="recent", enum=["recent", "search", "get"]),
            query=Param("string", default="", description="Required for search; max 1024 characters"),
            session_id=Param("string", default="", description="Optional session filter; required for get"),
            branch_id=Param("string", default="", description="Optional recovery branch; requires session_id"),
            position=Param("integer", description="get: zero-based message position (or use offset)"),
            offset=Param("integer", default=0, description="recent/search: next_offset; get: message position alias"),
            char_offset=Param("integer", default=0, description="get: next_char_offset from prior text page"),
            limit=Param("integer", default=10, description="recent/search: 1..50 messages"),
            max_chars=Param("integer", default=6000, description="Text character budget, 1..16000 per page"))
    async def history(self, ctx: dict[str, Any], mode: str = "recent", query: str = "",
                      session_id: str = "", branch_id: str = "", position: int | None = None,
                      offset: int = 0, char_offset: int = 0, limit: int = 10,
                      max_chars: int = 6000) -> str:
        reader = self._history(ctx)
        if mode == "get":
            page = await reader.get(session_id, position if position is not None else offset, branch_id=branch_id,
                                    char_offset=char_offset, max_chars=max_chars)
        elif mode in ("recent", "search"):
            kwargs: dict[str, Any] = {"session_id": session_id, "branch_id": branch_id, "offset": offset,
                                      "limit": limit, "max_chars": max_chars}
            page = (await reader.search(query, **kwargs) if mode == "search"
                    else await reader.recent(**kwargs))
        else:
            raise ValueError("Unknown history mode; use recent, search or get")
        return json.dumps(page, ensure_ascii=False)

    @action("write", description="Write to daily log or MEMORY.md",
            target=Param("string", required=True, enum=["daily", "memory"]),
            text=Param("string", required=True, description="Content to write"))
    async def write(self, ctx: dict[str, Any], target: str, text: str) -> str:
        memory_manager = ctx.get("_memory_manager")
        lock = memory_manager.write_lock if memory_manager is not None else self._write_lock

        def write() -> str:
            with lock:
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

        return await run_owned(write)


def create_memory_tool() -> AgentTool:
    return MemoryTool().build()
