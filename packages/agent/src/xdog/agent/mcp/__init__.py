"""MCP tool client foundation; hosts own process trust and tool permission policy."""
from xdog.agent.mcp.completion import McpCompletion
from xdog.agent.mcp.config import McpServerConfig, parse_servers
from xdog.agent.mcp.manager import McpManager
from xdog.agent.mcp.stdio import McpError

__all__ = ["McpCompletion", "McpError", "McpManager", "McpServerConfig", "parse_servers"]
