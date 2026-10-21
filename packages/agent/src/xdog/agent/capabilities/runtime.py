"""Inert installed-provider discovery and owned capability lifecycle."""
from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from dataclasses import dataclass
from importlib.metadata import entry_points
from typing import TYPE_CHECKING, Any
from weakref import WeakValueDictionary

from xdog.agent.agent import Agent
from xdog.agent.async_work import settle_cleanup, wait_owned
from xdog.agent.core import AgentTool, AgentToolResult

if TYPE_CHECKING:
    from xdog.agent.capabilities.children import ChildPort
    from xdog.agent.extensions.runtime import ExtensionRuntime

_SCOPES = frozenset({"session", "group", "application"})


@dataclass(frozen=True)
class ParentPort:
    """A pinned public runtime handle, never a raw application/session object."""

    agent: Agent
    extensions: ExtensionRuntime
    children: ChildPort
    valid: Callable[[], bool]
    user_authorized: Callable[[], bool]


def _names(value: Any, *, scopes: bool = False) -> tuple[str, ...]:
    if (not isinstance(value, (tuple, list, frozenset))
            or any(not isinstance(item, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", item)
                   for item in value) or len(value) != len(set(value))
            or scopes and not set(value) <= _SCOPES):
        raise ValueError("Invalid installed capability metadata")
    return tuple(value)


def discover(scope: str) -> tuple[Any, ...]:
    """Only trusted installed entry points, never modules named by saved data."""
    if scope not in _SCOPES:
        raise ValueError("Unknown capability host scope")
    providers = []
    names: set[str] = set()
    tools: set[str] = set()
    for entry in sorted(entry_points(group="xdog.capabilities"), key=lambda value: value.name):
        provider = entry.load()
        supported = _names(provider.scopes, scopes=True)
        if scope not in supported:
            continue
        if (not isinstance(provider.name, str) or provider.name != entry.name or provider.name in names
                or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", provider.name)
                or not callable(getattr(provider, "open", None))):
            raise ValueError("Invalid or duplicate installed capability identity")
        selected = _names(provider.tool_names)
        _names(provider.requirements)
        _names(getattr(provider, "command_names", ()))
        _names(getattr(provider, "optional_requirements", ()))
        if tools.intersection(selected):
            raise ValueError("Duplicate installed capability tool name")
        names.add(provider.name)
        tools.update(selected)
        providers.append(provider)
    return tuple(providers)


class CapabilitySet:
    """One host owner; feature code owns its own tools, data and business lifetime."""

    def __init__(self, scope: str) -> None:
        self.providers = discover(scope)
        self._bindings: dict[str, Any] = {}
        self._tools: dict[int, tuple[Any, AgentTool]] = {}
        self._owned: WeakValueDictionary[int, AgentTool] = WeakValueDictionary()
        self._host: object | None = None
        self._start_task: asyncio.Task[None] | None = None
        self._close_task: asyncio.Task[None] | None = None
        self._closing = False

    @property
    def declared_tools(self) -> frozenset[str]:
        return frozenset(name for provider in self.providers for name in provider.tool_names)

    @property
    def declared_commands(self) -> frozenset[str]:
        """Inert native command discovery; unrelated local controls need no owner startup."""
        return frozenset(name for provider in self.providers for name in getattr(provider, "command_names", ()))

    @property
    def requirements(self) -> frozenset[str]:
        return frozenset(name for provider in self.providers for name in provider.requirements)

    @property
    def optional_requirements(self) -> frozenset[str]:
        """Requested integrations whose absence does not disable core capability data."""
        return frozenset(name for provider in self.providers
                         for name in getattr(provider, "optional_requirements", ()))

    def get(self, name: str) -> Any:
        """Diagnostic access; application execution must not branch on feature IDs."""
        return self._bindings.get(name)

    async def start(self, host: object) -> None:
        if self._closing:
            raise RuntimeError("Capability owner is closed")
        if self._host is not None and self._host is not host:
            raise RuntimeError("Capability host owner changed")
        self._host = host
        if self._start_task is None:
            self._start_task = asyncio.create_task(self._open(host), name="capability-start")
        await wait_owned(self._start_task)

    async def _open(self, host: object) -> None:
        try:
            for provider in self.providers:
                if self._closing:
                    break
                binding = await provider.open(host)
                if binding is None or not callable(getattr(binding, "aclose", None)):
                    raise ValueError("Installed capability returned an invalid binding")
                self._bindings[provider.name] = binding
            self.tools  # Validate the complete published inventory before success.
        except BaseException as error:
            try:
                await self._close_bindings()
            except BaseException as cleanup:
                raise BaseExceptionGroup("Capability startup and cleanup failed", [error, cleanup]) from None
            raise

    def attach(self, parent: ParentPort) -> None:
        if self._closing:
            raise RuntimeError("Capability owner is closed")
        if not parent.valid():
            raise ValueError("Capability parent owner is stale")
        for binding in self._bindings.values():
            attach = getattr(binding, "attach", None)
            if attach is not None:
                attach(parent)

    @property
    def tools(self) -> tuple[AgentTool, ...]:
        result = []
        names: set[str] = set()
        active: set[int] = set()
        for binding in self._bindings.values():
            for definition in binding.tools:
                if definition.name in names:
                    raise ValueError("Duplicate capability tool name")
                names.add(definition.name)
                if isinstance(definition, AgentTool):
                    tool = definition
                else:
                    key = id(definition)
                    active.add(key)
                    previous = self._tools.get(key)
                    if previous is None:
                        async def execute(*args: Any, _definition: Any = definition, **kwargs: Any) -> AgentToolResult:
                            value = await _definition.execute(*args, **kwargs)
                            return value if isinstance(value, AgentToolResult) else AgentToolResult.from_text(value)
                        tool = AgentTool(name=definition.name, description=definition.description,
                                         parameters=definition.parameters, execute=execute)
                        self._tools[key] = definition, tool
                    else:
                        tool = previous[1]
                self._owned[id(tool)] = tool
                result.append(tool)
        self._tools = {key: value for key, value in self._tools.items() if key in active}
        return tuple(result)

    @property
    def owned_tools(self) -> tuple[AgentTool, ...]:
        """Weak identities let a retiring host remove only its original tools."""
        return tuple(self._owned.values())

    def owns_tool(self, tool: AgentTool) -> bool:
        return self._owned.get(id(tool)) is tool

    async def flush(self) -> None:
        if self._closing:
            raise RuntimeError("Capability owner is closed")
        if self._start_task is not None:
            await wait_owned(self._start_task)
        for binding in self._bindings.values():
            flush = getattr(binding, "flush", None)
            if flush is not None:
                await flush()

    async def _close_bindings(self) -> None:
        bindings = tuple(reversed(tuple(self._bindings.values())))
        try:
            await settle_cleanup(*(binding.aclose for binding in bindings),
                                 message="Installed capabilities failed to close")
        finally:
            self._bindings.clear()
            self._tools.clear()

    async def _close(self) -> None:
        if self._start_task is not None:
            try:
                await wait_owned(self._start_task)
            except BaseException:
                pass  # Startup reports its own failure after closing partial bindings.
        try:
            await self._close_bindings()
        finally:
            self._host = None

    async def aclose(self) -> None:
        self._closing = True
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close(), name="capability-close")
        await wait_owned(self._close_task)
