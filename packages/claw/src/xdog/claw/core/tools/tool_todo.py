"""Todo tool — ephemeral progress checklist. Uses ToolDef framework."""
from __future__ import annotations

from typing import Any

from xdog.agent.core import AgentTool
from xdog.agent.tool_def import Param, ToolDef, action

_VALID_STATUSES = frozenset({"pending", "in_progress", "completed"})


class TodoTool(ToolDef):
    name = "todo"
    description = ("Optional ephemeral progress checklist, not durable task state. Send the FULL list each time. "
                   "Use existing authoritative task files instead when available; do not duplicate them. "
                   "Not required for every multi-step task.")

    @action("write", description="Write the full todo list",
            todos=Param("array", required=True, description="Array of todo items", items={
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "content": {"type": "string"},
                    "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]},
                },
                "required": ["id", "content", "status"],
            }))
    async def write(self, ctx: dict[str, Any], todos: list[Any]) -> str:
        if not isinstance(todos, list) or not todos:
            return "Error: todos must be a non-empty array."
        if len(todos) > 50:
            return "Error: maximum 50 items."

        for i, item in enumerate(todos):
            if not isinstance(item, dict):
                return f"Error: todos[{i}] must be an object."
            if not item.get("id") or not item.get("content"):
                return f"Error: todos[{i}] needs id and content."
            if item.get("status") not in _VALID_STATUSES:
                return f"Error: todos[{i}].status invalid."

        icons = {"pending": "[ ]", "in_progress": "[~]", "completed": "[x]"}
        lines = [f"Updated {len(todos)} todo(s):"]
        for t in todos:
            lines.append(f"  {icons.get(t['status'], '[ ]')} {t['content']}")
        return "\n".join(lines)


def create_todo_tool() -> AgentTool:
    return TodoTool().build()
