"""Inert application capability: a discoverable skill and a CLI-only model tool.

No daemon, workflow runtime, host context, storage or process is opened at registration.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from xdog.agent.async_work import wait_owned
from xdog.agent.tool_effects import ToolEffect


class FlowCapability:
    def __init__(self) -> None:
        from xdog.flow.tool import FlowTool

        self.closed = False
        self.tools = (FlowTool(self),)
        self._calls: set[asyncio.Task[dict[str, Any]]] = set()
        self._waiters: set[asyncio.Task[bool]] = set()
        self._close_task: asyncio.Task[None] | None = None

    def tool_effect(self, tool: object, args: dict[str, Any]) -> ToolEffect | None:
        # Even validation can import workflow-declared tool references. CLI
        # execution is not automatically read-only and grants no new authority.
        return ToolEffect('execute', 'unknown') if any(tool is item for item in self.tools) else None

    async def run(self, arguments: tuple[str, ...], cwd: Path | None,
                  cancel: asyncio.Event | None) -> dict[str, Any]:
        from xdog.flow.cli_process import run

        if self.closed:
            raise RuntimeError('Flow tool binding is closed')
        if cancel is not None and cancel.is_set():
            raise asyncio.CancelledError
        operation = asyncio.create_task(run(arguments, cwd), name='flow-cli-call')
        self._calls.add(operation)
        cancelled = asyncio.create_task(cancel.wait()) if cancel is not None else None
        if cancelled is not None:
            self._waiters.add(cancelled)
        try:
            if cancelled is not None:
                await asyncio.wait((operation, cancelled), return_when=asyncio.FIRST_COMPLETED)
                if cancel is not None and cancel.is_set():
                    raise asyncio.CancelledError
            return await asyncio.shield(operation)
        finally:
            if not operation.done():
                operation.cancel()
            if cancelled is not None:
                cancelled.cancel()
            async def settle() -> None:
                try:
                    await asyncio.gather(operation, *(() if cancelled is None else (cancelled,)),
                                         return_exceptions=True)
                    if not operation.cancelled():
                        operation.result()  # A real cleanup failure must not be swallowed by cancellation.
                finally:
                    self._calls.discard(operation)
                    if cancelled is not None:
                        self._waiters.discard(cancelled)
            await wait_owned(asyncio.create_task(settle()))

    async def _close(self) -> None:
        calls = (*self._calls, *self._waiters)
        for call in calls:
            call.cancel()
        results = await asyncio.gather(*calls, return_exceptions=True)
        errors = [value for value in results if isinstance(value, Exception)]
        if errors:
            raise ExceptionGroup('Flow CLI shutdown failed', errors)

    async def aclose(self) -> None:
        self.closed = True
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close())
        await wait_owned(self._close_task)


@dataclass(frozen=True)
class FlowProvider:
    name: str = 'flow'
    scopes: tuple[str, ...] = ('application',)
    tool_names: tuple[str, ...] = ('flow',)
    skill_names: tuple[str, ...] = ('flow',)
    requirements: tuple[str, ...] = ()

    async def open(self, host: object) -> FlowCapability:
        return FlowCapability()  # Never inspect the caller's group/session/application context.


provider = FlowProvider()
