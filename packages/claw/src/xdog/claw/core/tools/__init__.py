"""Claw tools — registry + domain-less tools.

Agent built-in tools and claw-specific tools without a domain register
here. Domain tools register in their own ``__init__.py`` and are loaded
by ``core/__init__.py``.
"""
from xdog.agent.tools import create_bash_tool, create_current_time_tool, create_filesystem_tool
from xdog.claw.core.tools.registry import create_tools, default_enabled_tools, register, registered_names
from xdog.claw.core.tools.tool_generate_image import create_generate_image_tool
from xdog.claw.core.tools.tool_todo import create_todo_tool

register("current_time", create_current_time_tool)
register("filesystem", create_filesystem_tool)
register("bash", create_bash_tool)
register("todo", create_todo_tool)
register("generate_image", create_generate_image_tool)

__all__ = ["create_tools", "default_enabled_tools", "register", "registered_names"]
