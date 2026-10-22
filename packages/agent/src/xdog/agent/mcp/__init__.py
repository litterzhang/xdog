"""MCP tool client foundation; hosts own process trust and tool permission policy."""
from xdog.agent.mcp.completion import McpCompletion
from xdog.agent.mcp.config import McpServerConfig, parse_servers
from xdog.agent.mcp.registry import McpRegistry
from xdog.agent.mcp.stdio import McpError

__all__ = ["McpCompletion", "McpError", "McpRegistry", "McpServerConfig", "parse_servers"]
