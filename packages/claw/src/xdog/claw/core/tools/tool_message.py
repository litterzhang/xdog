"""Message tool — send a message to another group. Uses ToolDef framework.

This is a runtime-domain tool: the runtime owns the ``send_fn`` callback
and registers this tool with the tool registry.
"""
from __future__ import annotations

from typing import Any

from xdog.agent.core import AgentTool
from xdog.agent.tool_def import Param, ToolDef, action


class MessageTool(ToolDef):
    name = "message"
    description = "Send a message to another agent group."
    required_ctx = ("_send_fn",)

    @action("send", description="Send a message to a group",
            group_id=Param("string", required=True, description="Target group ID"),
            text=Param("string", required=True, description="Message text"))
    async def send(self, ctx: dict[str, Any], group_id: str, text: str) -> str:
        send_fn = ctx["_send_fn"]
        await send_fn(group_id, text)
        return "Message sent."


def create_message_tool() -> AgentTool:
    return MessageTool().build()
