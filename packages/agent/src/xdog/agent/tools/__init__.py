"""Built-in tools for the agent runtime.

Each tool is a standalone module. Use the ``create_*`` factory functions
to instantiate tools::

    from xdog.agent.tools import create_bash_tool, create_filesystem_tool

    tools = [create_bash_tool(), create_filesystem_tool()]
"""

from xdog.agent.skills.tool import create_skill_tool
from xdog.agent.tools.repo import ToolRegistration, ToolRepo
from xdog.agent.tools.tool_bash import create_bash_tool
from xdog.agent.tools.tool_current_time import create_current_time_tool
from xdog.agent.tools.tool_embed import create_embed_tool_from_fn
from xdog.agent.tools.tool_filesystem import create_filesystem_tool
from xdog.agent.tools.tool_submit_result import create_submit_result_tool
from xdog.agent.tools.tool_web_search import create_web_search_tool_from_fn

__all__ = [
    "create_current_time_tool",
    "create_skill_tool",
    "create_bash_tool",
    "create_filesystem_tool",
    "create_web_search_tool_from_fn",
    "create_embed_tool_from_fn",
    "create_submit_result_tool",
    "ToolRegistration",
    "ToolRepo",
]
