"""Planning — goals (what to do); Eyes owns observations and time.

Domain tools are registered here; only Eyes computes trigger deadlines.
"""
from xdog.claw.core.planning.goal_tracker import GoalTracker
from xdog.claw.core.tools.registry import register
from xdog.claw.core.tools.tool_eyes import create_eyes_tool
from xdog.claw.core.tools.tool_goal import create_goal_tool

register("goal", create_goal_tool)
register("eyes", create_eyes_tool)

__all__ = ["GoalTracker"]
