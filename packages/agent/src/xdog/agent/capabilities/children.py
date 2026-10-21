"""Owner-bound child admission ports, independent of installed feature schemas."""
from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable, Sequence
from contextvars import ContextVar
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol

from xdog.agent.capabilities.runtime import ParentPort
from xdog.agent.core import AgentTool, AgentToolResult
from xdog.agent.session_input import SessionInput
from xdog.agent.supervision import ChildSpec, ChildSupervisor, settle_on


class CapabilityHost(Protocol):
    scope: str
    owner_prefix: str
    identity: str
    workspace: Path
    storage_root: Path
    namespace: str
    branch: str
    def valid(self) -> bool: ...
    def accepting(self) -> bool: ...
    def owns_parent(self, parent: ParentPort) -> bool: ...
    async def prepare_parent(self) -> ParentPort: ...
    async def enqueue(self, message: SessionInput) -> str: ...
    async def receipt(self, message: SessionInput) -> str | None: ...
    def subscribe_completed(self, callback: Callable[[], None]) -> Callable[[], None]: ...
    async def child_record(self, session: str, branch: str, identifier: str) -> dict[str, Any] | None: ...
    def subscribe_events(self, callback: Callable[[dict[str, Any]], None]) -> Callable[[], None]: ...
    def execution_snapshot(self) -> dict[str, Any]: ...
    async def latest_execution(self) -> dict[str, Any] | None:
        """Latest durable execution; None means no fact, not unavailable storage.

        IDs match execution.settled, not owner generations. message_ids are the
        authoritative execution inputs. Optional settled_at is Unix seconds only
        when persisted by the execution owner; running has no settled_at.
        Legacy facts lacking metadata return availability=unavailable with reason.
        Retirement before admission returns None; corrupt
        data and storage errors raise. Retirement during owned IO cannot hide its
        actual error. Running is a fact, never authorization to replay execution.
        """
        ...
    async def acp_target(self) -> dict[str, Any] | None: ...


@dataclass
class _Admission:
    owner: ChildPort
    narrow: Callable[[tuple[AgentTool, ...]], tuple[AgentTool, ...]]
    tools: Callable[[ChildSpec], Awaitable[tuple[AgentTool, ...]]]
    admitted: Callable[[ChildSpec], None]
    loop: asyncio.AbstractEventLoop
    claimed: bool = False


_admission: ContextVar[_Admission | None] = ContextVar('capability_child_admission', default=None)


class ChildPort:
    """The existing supervisor only; a copied admission cannot start a second role."""
    def __init__(self, session: str, branch: str, parent_tools: Callable[[], Sequence[AgentTool]]) -> None:
        self.session, self.branch = session, branch
        self.parent_tools = parent_tools
        self.supervisor: ChildSupervisor
        self._lock = threading.Lock()
        self._roles: dict[str, _Admission] = {}
        self._bound: dict[str, tuple[AgentTool, ...]] = {}
        self._parent_only: dict[int, AgentTool] = {}
        self._loops: dict[str, asyncio.AbstractEventLoop] = {}

    async def start(self, prompt: str, *, access: str, config: dict[str, Any],
                    narrow: Callable[[tuple[AgentTool, ...]], tuple[AgentTool, ...]],
                    tools: Callable[[ChildSpec], Awaitable[tuple[AgentTool, ...]]],
                    admitted: Callable[[ChildSpec], None]) -> str:
        ticket = _Admission(self, narrow, tools, admitted, asyncio.get_running_loop())
        token = _admission.set(ticket)
        try:
            identifier = await self.supervisor.start(prompt, access=access, inherit_context=False, config=config)
            if not ticket.claimed:
                raise ValueError('Capability child did not receive actual admission')
            return identifier
        finally:
            _admission.reset(token)

    def capture(self, spec: ChildSpec, tools: tuple[AgentTool, ...],
                allowed: tuple[str, ...] | None) -> tuple[str, ...] | None:
        # Pin parent-only identities at real admission, before asynchronous startup.
        # Later inventory replacement cannot leak a retired capability to this child.
        parent_only = tuple(self.parent_tools())
        with self._lock:
            self._parent_only.update((id(tool), tool) for tool in parent_only)
        ticket = _admission.get()
        if ticket is None or ticket.owner is not self:
            return allowed
        with self._lock:
            if ticket.claimed:
                return allowed
            narrowed = ticket.narrow(tools)
            if any(not any(tool is original for original in tools) for tool in narrowed):
                raise ValueError('Child policy returned an ungranted tool object')
            ticket.admitted(spec)
            ticket.claimed = True
            self._roles[spec.id] = ticket
            self._loops[spec.id] = ticket.loop
        return tuple(tool.name for tool in narrowed if allowed is None or tool.name in allowed)

    async def tools(self, spec: ChildSpec) -> tuple[AgentTool, ...]:
        with self._lock:
            ticket = self._roles.get(spec.id)
        if ticket is None:
            return ()
        loop = self._loops[spec.id]
        async def originals_on_owner() -> tuple[AgentTool, ...]:
            return await ticket.tools(spec)
        originals = await settle_on(loop, originals_on_owner())
        def bridge(tool: AgentTool) -> AgentTool:
            async def execute(call: str, params: dict[str, Any], cancel: asyncio.Event | None = None,
                              on_update: Any = None, ctx: Any = None) -> AgentToolResult:
                async def invoke() -> AgentToolResult:
                    assert tool.execute is not None
                    return await tool.execute(call, params, None, None, ctx)
                return await settle_on(loop, invoke())
            return replace(tool, execute=execute)
        result = tuple(bridge(tool) for tool in originals)
        with self._lock:
            self._bound[spec.id] = result
        return result

    def permits(self, identifier: str, name: str, tools: Sequence[AgentTool]) -> bool:
        selected = next((tool for tool in tools if tool.name == name), None)
        with self._lock:
            return selected is not None and any(selected is tool for tool in self._bound.get(identifier, ()))

    def inherited(self, tool: AgentTool) -> bool:
        with self._lock:
            captured = self._parent_only.get(id(tool)) is tool
        return not captured and not any(tool is value for value in self.parent_tools())

    @property
    def active(self) -> bool:
        return bool(self.supervisor.active)

    @property
    def closed(self) -> bool:
        return self.supervisor._closed

    async def inspect(self, identifier: str) -> dict[str, Any]:
        return await self.supervisor.control('inspect', identifier)

    async def cancel(self, identifier: str) -> None:
        await self.supervisor.control('cancel', identifier)

    def subscribe(self, callback: Callable[[dict[str, Any]], None]) -> Callable[[], None]:
        return self.supervisor.subscribe(callback)

    def settled(self, identifier: str) -> bool:
        return not self.supervisor.capture_source(identifier).settling
