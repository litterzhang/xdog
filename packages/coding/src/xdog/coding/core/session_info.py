"""One secret-free session query for the native command and host extension tool."""
from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

from xdog.agent import AgentTool, AgentToolResult
from xdog.agent.core import AgentToolUpdateCallback
from xdog.agent.extensions.inspection import capture

if TYPE_CHECKING:
    from xdog.coding.core.agent_session import AgentSession

TOOL_NAME = 'ext_coding_session'


def snapshot(session: AgentSession) -> dict[str, Any]:
    inspection = capture(session.agent, session.session_id, session.session_id, session.model_info,
                         context_window=session.context_window, max_prompt_tokens=session.max_prompt_tokens)
    return {'session_id': session.session_id, 'branch_id': session.session_data.active_branch,
            'name': session.session_data.name, 'cwd': str(session.working_dir),
            'permission_mode': session.permissions.mode, 'read_only': session.access.state.read_only,
            'model': inspection['model'], 'usage': inspection['usage']}


def create_tool(session: AgentSession) -> AgentTool:
    data, branch, workspace = session.session_data, session.session_data.active_branch, session.working_dir

    def validate(params: dict[str, Any]) -> None:
        if type(params) is not dict or params:
            raise ValueError('Session inspection takes no arguments and cannot select another session')

    async def execute(tool_call_id: str, params: dict[str, Any], cancel: asyncio.Event | None = None,
                      on_update: AgentToolUpdateCallback | None = None,
                      *, ctx: dict[str, Any] | None = None) -> AgentToolResult:
        validate(params)
        if cancel is not None and cancel.is_set():
            raise asyncio.CancelledError
        if (session._sdk_disposing or session._navigating or session.session_data is not data
                or data.active_branch != branch or session.working_dir != workspace
                or session._session_info_tool is not tool
                or len([item for item in session.agent.active_tools if item.name == TOOL_NAME]) != 1
                or not any(item is tool for item in session.agent.active_tools)):
            raise RuntimeError('Session inspection owner/binding is no longer active')
        value = snapshot(session)
        return AgentToolResult.from_text(json.dumps(value, ensure_ascii=True), details=value)

    tool = AgentTool(name=TOOL_NAME, label='Session info',
        description='Read the current Coding session, selected model/capabilities, thinking, permissions and measured '
                    'context usage. Host-reported configuration, not verified underlying model identity. '
                    'Read-only, no arguments, no session switching or execution.',
        parameters={'type': 'object', 'properties': {}, 'additionalProperties': False},
        execute=execute, validate_arguments=validate)
    return tool
