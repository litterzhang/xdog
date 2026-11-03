"""Bind actual host tools to the existing turn owner without knowing channel domains."""
from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from xdog.agent import AgentTool
from xdog.claw.core.runtime.identity_scope import allows_tool
from xdog.claw.core.tools.input_tool import InputReader, InputTool
from xdog.claw.core.types import GroupInput

if TYPE_CHECKING:
    from xdog.claw.core.runtime.session import AgentSession


def bind_input_tools(session: AgentSession, original: GroupInput, channel: object | None,
                     input_id: str | None, current: Callable[[], bool]) -> dict[str, InputReader]:
    agent, runtime = session.agent, session._runtime
    bindings: dict[str, InputReader] = {}
    for tool in agent.active_tools:
        if not isinstance(tool, InputTool) or not any(tool is actual for actual in runtime._tools or ()):
            continue
        def valid(actual: AgentTool = tool) -> bool:
            scope = session._input_execution
            return (current() and not runtime._closing and runtime._active_session is session
                    and session.agent is agent and scope is not None and input_id is not None
                    and scope.input_id == input_id and agent.tool_context.get('_input_tools') is bindings
                    and any(actual is registered for registered in runtime._tools or ())
                    and any(actual is selected for selected in agent.active_tools)
                    and allows_tool(session, actual.name))
        reader = tool.bind_input(original, channel, valid)
        if reader is not None:
            bindings[tool.name] = reader
    return bindings
