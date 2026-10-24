"""Host-owned collections of actual tools. No global registry or runtime."""
from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from xdog.agent.core import AgentTool

if TYPE_CHECKING:
    from xdog.agent.agent import Agent


@dataclass(frozen=True)
class ToolRegistration:
    """Withdraw only these original instances, never their later replacements."""

    _repo: ToolRepo
    _entries: tuple[tuple[str, object], ...]

    def close(self) -> None:
        for name, token in self._entries:
            current = self._repo._tools.get(name)
            if current is not None and current[0] is token:
                del self._repo._tools[name]


class ToolRepo:
    """An owner-local tool collection; selecting never clones executors or grants.

    Builtins are fresh instances, created only on explicit construction. A host
    with isolated tools uses builtin=False and registers its own instances.
    Repositories do not close borrowed tools or publish changes automatically.
    """

    def __init__(self, *tools: AgentTool, builtin: bool = True, cwd: Path | None = None) -> None:
        if type(builtin) is not bool:
            raise TypeError('builtin must be a bool')
        self._tools: dict[str, tuple[object, AgentTool]] = {}
        if builtin:
            from xdog.agent.tools.tool_bash import create_bash_tool
            from xdog.agent.tools.tool_current_time import create_current_time_tool
            from xdog.agent.tools.tool_filesystem import create_filesystem_tool

            self.register(create_bash_tool(initial_cwd=cwd), create_filesystem_tool(), create_current_time_tool())
        self.register(*tools)

    def register(self, *tools: AgentTool) -> ToolRegistration:
        """Register instances atomically. Duplicate names require explicit removal."""
        names: set[str] = set()
        for tool in tools:
            if not isinstance(tool, AgentTool):
                raise TypeError('Register actual AgentTool instances')
            name = tool.name
            if not isinstance(name, str) or not name or name.strip() != name:
                raise ValueError('Tool names must be nonempty text without surrounding whitespace')
            if name in names or name in self._tools:
                raise ValueError(f'Duplicate tool name: {name}')
            names.add(name)
        entries = tuple((tool.name, object()) for tool in tools)
        self._tools.update((name, (token, tool)) for (name, token), tool in zip(entries, tools, strict=True))
        return ToolRegistration(self, entries)

    def unregister(self, name: str) -> None:
        """Explicit removal; old registration receipts cannot remove a new binding."""
        if name not in self._tools:
            raise ValueError(f'Unknown tool: {name}')
        del self._tools[name]

    def get(self, name: str) -> AgentTool:
        try:
            return self._tools[name][1]
        except KeyError:
            raise ValueError(f'Unknown tool: {name}') from None

    def names(self) -> frozenset[str]:
        return frozenset(self._tools)

    @property
    def tools(self) -> tuple[AgentTool, ...]:
        return tuple(tool for _, tool in self._tools.values())

    def __iter__(self) -> Iterator[AgentTool]:
        return iter(self.tools)

    def __len__(self) -> int:
        return len(self._tools)

    def select(self, names: Iterable[str]) -> ToolRepo:
        if isinstance(names, str):
            raise TypeError('Tool names must be an iterable, not a string')
        chosen = tuple(names)
        if len(set(chosen)) != len(chosen):
            raise ValueError('Duplicate tool selection')
        return ToolRepo(*(self.get(name) for name in chosen), builtin=False)

    def merge(self, *others: ToolRepo) -> ToolRepo:
        """Union by exact identity; a different same-name executor is a conflict."""
        result = ToolRepo(*self.tools, builtin=False)
        for other in others:
            for tool in other:
                if tool.name not in result.names():
                    result.register(tool)
                elif result.get(tool.name) is not tool:
                    raise ValueError(f'Duplicate tool name: {tool.name}')
        return result

    def publish(self, agent: Agent) -> None:
        """Publish on the existing owner without broadening active selection."""
        replacement = self.tools
        if tuple(map(id, agent.local_tools)) != tuple(map(id, replacement)):
            agent.set_tools(replacement)
