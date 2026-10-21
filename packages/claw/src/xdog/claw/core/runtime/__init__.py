"""Runtime spine — gateway, orchestrator, group, session.

The runtime domain owns the message tool and registers it here.
"""
from xdog.claw.core.tools.registry import register
from xdog.claw.core.tools.tool_message import create_message_tool

register("message", create_message_tool)
