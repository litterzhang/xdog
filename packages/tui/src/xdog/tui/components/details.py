"""Shared detail expansion support for retained component trees."""

from __future__ import annotations

import re
from typing import Protocol, runtime_checkable

from xdog.tui.components.text import Text
from xdog.tui.tui import Component, Container
from xdog.tui.utils import truncate_to_width


def reasoning_preview(thinking: str) -> str:
    """Show readable reasoning even when the full detail panel is closed."""
    lines = [line.strip() for line in thinking.splitlines() if line.strip()]
    body = [line for line in lines if not re.fullmatch(r"(?:#{1,6}\s+.+|\*\*[^*]+\*\*)", line)]
    preview = (body or lines or [""])[0]
    preview = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", preview)
    preview = re.sub(r"\*\*|__|`|(?<!\w)[*_]|[*_](?!\w)", "", preview)
    return "Thinking (Ctrl+O: full details)\n" + preview


class ReasoningText(Text):
    """Limit compact reasoning to a header and two readable body rows."""

    compact = False

    def render(self, width: int) -> list[str]:
        rows = super().render(width)
        if self.compact and len(rows) > 3:
            return [*rows[:2], truncate_to_width(rows[2], max(0, width - 1), "") + "…"]
        return rows


@runtime_checkable
class ExpandableComponent(Protocol):
    """Component whose detailed presentation can be expanded or collapsed."""

    def set_expanded(self, expanded: bool) -> None:
        """Select the expanded or compact presentation."""


def set_details_expanded(component: Component, expanded: bool) -> None:
    """Apply a detail mode recursively without changing component identity."""
    if isinstance(component, ExpandableComponent):
        component.set_expanded(expanded)
    if isinstance(component, Container):
        for child in tuple(component.children):
            set_details_expanded(child, expanded)
