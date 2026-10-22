"""Thin argv-based tool over the installed Flow CLI, not a second workflow API."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from xdog.flow.capability import FlowCapability


class FlowTool:
    name = 'flow'
    description = (
        'Use the flow skill to author workflows, then invoke the installed xdog-flow CLI here. '
        'Pass arguments exactly as CLI argv, without an executable or shell syntax. '
        'For example {"arguments":["validate","/project/workflow.json","--json"]}. '
        'Supports non-interactive validate, run, generate, graph, test, scheduling, and --help. '
        'The interactive build TUI is for manual CLI use, not this tool. '
        'Returns actual exit_code, stdout and stderr; exit_code != 0 is not success. '
        'No daemon is used. Loading/registering Flow never starts a workflow. '
        'Workflow code/imports/models and scheduling may have effects: normal host permissions apply. '
        'Cancellation stops the owned CLI process group; interrupted effects are never automatically retried.'
    )
    parameters: dict[str, Any] = {
        'type': 'object', 'required': ['arguments'], 'additionalProperties': False,
        'properties': {
            'arguments': {'type': 'array', 'minItems': 1, 'maxItems': 128,
                'items': {'type': 'string'},
                'description': 'xdog-flow command and argv only, no executable. Use --help or COMMAND --help '
                               'for flags. Prefer absolute workflow/output paths. Shell operators are literal '
                               'arguments, never interpreted. Maximum argv size: 64 KiB.'},
            'cwd': {'type': 'string', 'minLength': 1,
                'description': 'Optional absolute existing CLI process working directory, for relative paths '
                               'and workflow execution. Omit to inherit the host process directory. '
                               'Not an owner, registration namespace, session selector or permission grant.'},
        },
    }

    def __init__(self, binding: FlowCapability) -> None:
        self._binding = binding

    async def execute(self, tool_call_id: str, params: dict[str, Any], cancel: asyncio.Event | None = None,
                      on_update: Any = None, ctx: dict[str, Any] | None = None) -> str:
        if self._binding.closed:
            raise RuntimeError('Flow tool binding is closed')
        if not isinstance(params, dict) or set(params) - {'arguments', 'cwd'} or 'arguments' not in params:
            raise ValueError('Flow requires arguments and accepts only optional cwd')
        arguments = params['arguments']
        if (not isinstance(arguments, list) or not 1 <= len(arguments) <= 128
                or any(not isinstance(value, str) or '\x00' in value for value in arguments)):
            raise ValueError('Flow arguments must be 1..128 strings without NUL bytes')
        if sum(len(value.encode('utf-8')) + 1 for value in arguments) > 65_536:
            raise ValueError('Flow argv exceeds 64 KiB')
        if arguments[0] == 'build':
            raise ValueError('Flow build is an interactive TUI; use the manual CLI or author JSON with filesystem')
        cwd = None
        if 'cwd' in params:
            value = params['cwd']
            if not isinstance(value, str) or '\x00' in value or not Path(value).is_absolute():
                raise ValueError('Flow cwd must be an absolute existing directory')
            cwd = Path(value)  # Existence/type is checked by subprocess creation, off the host event loop.
        result = await self._binding.run(tuple(arguments), cwd, cancel)
        return json.dumps(result, ensure_ascii=False, allow_nan=False)
