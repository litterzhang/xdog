"""Owner-bound mcp controls; no application-wide runtime access."""
from __future__ import annotations

from typing import Any, Callable

from xdog.agent.mcp.completion import McpCompletion, request_arguments
from xdog.agent.mcp.context import McpDocument, McpEntry
from xdog.claw.core.runtime.group import GroupRuntime
from xdog.claw.core.runtime.group_controller import GroupController


async def manage_mcp(runtime: GroupRuntime, controller: GroupController,
                     action: str, server: str) -> tuple[dict[str, Any], ...]:
    if action in {"list", "status"}:
        return runtime.mcp.status()
    async with controller.operation(context_change=True):
        return await runtime.manage_mcp(action, server)


async def manage_mcp_auth(runtime: GroupRuntime, controller: GroupController, server: str, action: str, receipt: str,
                          session: str, lease: str, alive: Callable[[], bool]) -> dict[str, Any]:
    runtime.require_current(session)
    if action in {"status", "cancel"}:
        return await runtime.mcp.manage_auth(server, action, receipt, lease=lease, alive=alive)
    async with controller.operation(
            validate=lambda: runtime.require_current(session)):
        if runtime._active_session is not None:
            runtime._active_session._children_idle()
        result = await runtime.mcp.manage_auth(server, action, receipt, lease=lease, alive=alive)
        if action == "forget":
            if runtime._active_session is not None:
                runtime._active_session.agent.sync_mcp_tools()
                await runtime._active_session._persist_tool_retirement()
        return result


async def mcp_context(runtime: GroupRuntime,
                      action: str, server: str, name: str,
                      arguments: dict[str, str]) -> tuple[McpEntry, ...] | McpDocument | McpCompletion:
    if action in {"resources", "templates", "prompts"}:
        kind = {"resources": "resource", "templates": "template", "prompts": "prompt"}[action]
        return await runtime.mcp.catalog(server, kind)
    if action == "read":
        return await runtime.mcp.resource(server, name)
    if action == "prompt":
        return await runtime.mcp.prompt(server, name, arguments)
    if action in {"complete_prompt", "complete_template", "complete_resource"}:
        argument, value, context = request_arguments(arguments)
        return await runtime.mcp.complete(server, action.removeprefix("complete_"), name, argument, value, context)
    raise ValueError("Unknown MCP context action")
