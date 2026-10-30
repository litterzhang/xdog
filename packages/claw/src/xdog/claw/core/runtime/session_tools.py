"""Session-owned readonly operations; no independent execution owner."""
from __future__ import annotations

import asyncio
import inspect
import logging
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from xdog.agent import (
    AfterToolCallContext,
    AfterToolCallResult,
    AgentTool,
    BeforeToolCallContext,
    BeforeToolCallResult,
)
from xdog.agent.access import read_only_call
from xdog.ai.types import (
    AssistantMessage,
    TextContent,
    ToolCall,
)

if TYPE_CHECKING:
    from xdog.claw.core.runtime.session import AgentSession

logger = logging.getLogger(__name__)


def read_only_tool_call(session: AgentSession, ctx: BeforeToolCallContext) -> bool:
    # Classify the actual advertised identity, not an arbitrary tool name.
    active = tuple(tool for tool in session._agent.active_tools if tool.name == ctx.tool_call.name)
    supplied = tuple(tool for tool in (ctx.context.tools or ()) if tool.name == ctx.tool_call.name)
    if len(active) == len(supplied) == 1 and active[0] is supplied[0]:
        if (ctx.tool_call.name == 'memory' and ctx.args.get('action') in {'get', 'search', 'history'}
                and any(active[0] is original for original in session._runtime._tools or ())):
            return True  # Actual host memory reader, never a same-named external tool.
        if (ctx.tool_call.name == 'xhs_post' and not ctx.args
                and any(active[0] is original for original in session._runtime._tools or ())):
            return True
        effect = session._runtime.capability_assembly.tool_effect(active[0], dict(ctx.args))
        if effect is not None:
            return effect.access == "read"
    return read_only_call(ctx.tool_call.name, ctx.args)


def validate_tool_execution(session: AgentSession, ctx: BeforeToolCallContext) -> None:
    """Final synchronous ceiling check; another prepared call cannot lend a grant."""
    from xdog.claw.core.runtime.identity_scope import allows_tool
    if not allows_tool(session, ctx.tool_call.name):
        raise ValueError('Tool execution is outside the original input scope')
    actual = next((tool for tool in session.agent.active_tools if tool.name == ctx.tool_call.name), None)
    if actual is None or not any(tool is actual for tool in ctx.context.tools or ()):
        raise ValueError('Original tool registration or selection changed before execution')
    if session.access.state.read_only and not read_only_tool_call(session, ctx):
        raise ValueError('Host access scope is read-only')
    mcp = session._runtime.mcp
    if mcp.owns_tool(actual) and (session.agent.mcp_registry is not mcp
                                or not mcp.is_current_tool(actual) or not mcp.allowed(actual.name)):
        raise ValueError('Original MCP tool permission changed before execution')


async def before_tool_call(
    session: AgentSession, ctx: BeforeToolCallContext, cancel: asyncio.Event | None = None,
    *, memory_maintenance: bool = False,
) -> BeforeToolCallResult | None:
    from xdog.claw.core.compaction.managed_memory import allowed
    from xdog.claw.core.runtime.identity_scope import allows_tool
    try:
        scope = session._input_execution
        if not allows_tool(session, ctx.tool_call.name):
            return BeforeToolCallResult(block=True, reason='Tool execution is outside the original input scope')
        metadata = (memory_maintenance and ctx.tool_call.name == 'memory'
                    and await allowed(session._runtime.workspace_dir, ctx.args))
        if session.access.state.read_only and not metadata and not session._read_only_tool_call(ctx):
            return BeforeToolCallResult(block=True, reason="Host access scope is read-only")
        if session.extensions.enabled:
            await session.extensions.event("tool_start", {"tool_name": ctx.tool_call.name})
            from xdog.ai.utils.validation import validate_tool_arguments
            try:
                effective = await session.extensions.pre(ctx.tool_call.name, dict(ctx.args))
                if effective is None:
                    return BeforeToolCallResult(block=True, reason="Extension vetoed this tool call")
                tool = next(tool for tool in session._agent.state.tools if tool.name == ctx.tool_call.name)
                if tool.validate_arguments is not None:
                    checked = tool.validate_arguments(effective)
                    if inspect.isawaitable(checked):
                        await checked
                elif not validate_tool_arguments(effective, tool.parameters).valid:
                    raise ValueError("Extension rewrote parameters outside the tool schema")
                ctx.args.clear()
                ctx.args.update(effective)
            except Exception as exc:
                return BeforeToolCallResult(block=True, reason=str(exc))
        # pre hooks may replace arguments. Validate the final effective call,
        # not just the model's initial proposal, before executing maintenance.
        metadata = (memory_maintenance and ctx.tool_call.name == 'memory'
                    and await allowed(session._runtime.workspace_dir, ctx.args))
        if memory_maintenance and not metadata:
            return BeforeToolCallResult(block=True, reason='Outside managed memory maintenance scope')
        if session.access.state.read_only and not metadata and not session._read_only_tool_call(ctx):
            return BeforeToolCallResult(block=True, reason="Host access scope is read-only")
        if scope is not session._input_execution or not allows_tool(session, ctx.tool_call.name):
            return BeforeToolCallResult(block=True, reason='Original input identity scope changed')
        logger.info("Tool call: %s (group=%s)", ctx.tool_call.name, session._group_id)
        mcp = session._runtime.mcp
        actual = next((tool for tool in session._agent.state.tools if tool.name == ctx.tool_call.name), None)
        if actual is not None and mcp.owns_tool(actual):
            if (session._agent.mcp_registry is not mcp or not mcp.is_current_tool(actual)
                    or not mcp.allowed(ctx.tool_call.name)):
                return BeforeToolCallResult(block=True, reason="External MCP tool use is disabled; "
                                            "configure allow_tools explicitly for this server")
        return None
    finally:
        await session._persist_tool_retirement()


async def after_tool_call(
    session: AgentSession, ctx: AfterToolCallContext, cancel: asyncio.Event | None = None,
) -> AfterToolCallResult | None:
    try:
        if not session.extensions.enabled or ctx.is_error or any(not isinstance(part, TextContent)
                                                              for part in ctx.result.content):
            return None
        text = "\n".join(part.text for part in ctx.result.content if isinstance(part, TextContent))
        try:
            result = await session.extensions.post(ctx.tool_call.name, ctx.args, text)
        except Exception:
            return None
        return AfterToolCallResult(content=(TextContent(text=result),)) if result != text else None
    finally:
        await session._persist_tool_retirement()


def compaction_tools(session: AgentSession, *, memory_only: bool = False) -> list[AgentTool]:
    from xdog.agent import AgentContext, AgentToolResult
    if session._agent.supports_tool_calls is False:
        return []  # Silent flush also cannot grant a native execution path.
    tools = []
    active = {tool.name for tool in session._agent.active_tools}
    from xdog.agent.children import RESERVED_TOOLS
    for tool in session._agent.state.tools:
        if (tool.name not in active or tool.name in RESERVED_TOOLS
                or memory_only and tool.name != 'memory'):
            continue  # Silent compaction is not authority to regain excluded parent tools.
        def guarded(original: AgentTool) -> AgentTool:
            async def execute(call_id: str, params: dict[str, Any], *args: Any, **kwargs: Any) -> AgentToolResult:
                ctx = BeforeToolCallContext(assistant_message=AssistantMessage(), args=params,
                    tool_call=ToolCall(id=call_id, name=original.name, arguments=params), context=AgentContext())
                # Even maintenance keeps actual host veto/rewrite hooks. Only
                # explicitly managed memory gets the existing readonly exception.
                from xdog.claw.core.compaction.managed_memory import allowed
                metadata = original.name == 'memory' and await allowed(session._runtime.workspace_dir, params)
                block = await session._before_tool_call(ctx, memory_maintenance=memory_only or metadata)
                if block is not None and block.block:
                    return AgentToolResult.from_text(block.reason, is_error=True)
                if not any(original is current for current in session._agent.active_tools):
                    return AgentToolResult.from_text('Original maintenance tool was retired', is_error=True)
                if original.execute is None:
                    raise ValueError("Compaction tool has no executor")
                kwargs['ctx'] = session._agent.tool_context
                return await original.execute(call_id, params, *args, **kwargs)
            return replace(original, execute=execute)
        tools.append(guarded(tool))
    return tools
