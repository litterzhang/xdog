"""Extension type definitions."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ExtensionManifest:
    """Metadata for an extension, loaded from extension.yaml."""

    name: str
    version: str = "0.0.0"
    description: str = ""
    author: str = ""
    entry_point: str = "main"
    dependencies: tuple[str, ...] = ()
    settings_schema: dict[str, Any] = field(default_factory=dict)


class ExtensionHook(ABC):
    """Base class for extension hooks.

    Extensions implement hooks to intercept and modify agent behavior
    at well-defined points.
    """

    @abstractmethod
    def name(self) -> str:
        """Unique name for this hook."""
        ...


class PreToolUseHook(ExtensionHook):
    """Called before a tool is executed.

    Can modify parameters or abort the tool call.
    """

    @abstractmethod
    async def before_tool(
        self,
        tool_name: str,
        params: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Process a tool call before execution.

        Return the (possibly modified) params to proceed, or ``None``
        to abort the tool call.
        """
        ...


class PostToolUseHook(ExtensionHook):
    """Called after a tool is executed."""

    @abstractmethod
    async def after_tool(
        self,
        tool_name: str,
        params: dict[str, Any],
        result: str,
    ) -> str:
        """Process a tool result after execution.

        Return the (possibly modified) result string.
        """
        ...


class StopHook(ExtensionHook):
    """Called when the agent session is ending."""

    @abstractmethod
    async def on_stop(self) -> None:
        """Perform cleanup or final checks."""
        ...


@dataclass
class Extension:
    """A loaded extension instance."""

    manifest: ExtensionManifest
    hooks: list[ExtensionHook] = field(default_factory=list)
    settings: dict[str, Any] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.manifest.name

    def get_hooks_of_type(self, hook_type: type) -> list[ExtensionHook]:
        return [h for h in self.hooks if isinstance(h, hook_type)]
