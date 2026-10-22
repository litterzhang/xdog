"""Claw factory composition for a caller-owned Agent SDK registry.

Importing a domain never registers tools. Each group owns its registry; inventory
callers may create a fresh one without starting host resources or capabilities.
"""
from xdog.agent.tools import ToolRegistry
from xdog.claw.core.tools.tool_generate_image import create_generate_image_tool
from xdog.claw.core.tools.tool_memory import create_memory_tool
from xdog.claw.core.tools.tool_message import create_message_tool
from xdog.claw.core.tools.tool_skill import create_skill_tool
from xdog.claw.core.tools.tool_todo import create_todo_tool


def new_tool_registry() -> ToolRegistry:
    """Add only Claw factories to the SDK's built-in factory inventory."""
    registry = ToolRegistry()
    registry.register_factory("memory", lambda context: create_memory_tool(), default=True)
    registry.register_factory("message", lambda context: create_message_tool(), default=True)
    registry.register_factory("skill", lambda context: create_skill_tool(), default=True)
    registry.register_factory("todo", lambda context: create_todo_tool(), default=True)
    registry.register_factory("generate_image", lambda context: create_generate_image_tool(model=context.image_model))
    return registry


__all__ = ["new_tool_registry"]
