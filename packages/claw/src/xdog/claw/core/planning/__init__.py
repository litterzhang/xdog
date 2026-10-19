"""Optional observation tool registration; no planner or Goal execution owner."""
from xdog.claw.core.tools.registry import register
from xdog.claw.core.tools.tool_eyes import create_eyes_tool

register("eyes", create_eyes_tool)
