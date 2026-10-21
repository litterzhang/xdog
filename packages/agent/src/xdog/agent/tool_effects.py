"""Inert tool-effect data, independent of host permission and execution policy."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class ToolEffect:
    """Describe an effect; consumers retain all authority over its admission."""

    access: Literal["read", "write", "execute"]
    scope: Literal["workspace", "outside", "tool_data", "external", "unknown"]
    requires_approval: bool = False

    def __post_init__(self) -> None:
        if self.access not in ("read", "write", "execute"):
            raise ValueError("Invalid tool effect access")
        if self.scope not in ("workspace", "outside", "tool_data", "external", "unknown"):
            raise ValueError("Invalid tool effect scope")
        allowed = {
            "read": ("workspace", "outside", "tool_data"),
            "write": ("workspace", "outside", "tool_data", "external"),
            "execute": ("unknown",),
        }
        if self.scope not in allowed[self.access]:
            raise ValueError("Invalid tool effect access/scope combination")
        if type(self.requires_approval) is not bool:
            raise ValueError("Tool effect requires_approval must be a boolean")
