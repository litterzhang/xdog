"""Bounded declarative extension UI, never arbitrary terminal escape output."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from xdog.tui.tui import Component
from xdog.tui.utils import sanitize_terminal_text, truncate_to_width


class ExtensionView(Component):
    def __init__(self, source: Callable[[], Mapping[str, Any]]) -> None:
        self.source = source

    def render(self, width: int) -> list[str]:
        rows = []
        for key, value in tuple(self.source().get("ui", {}).items())[:8]:
            if (not isinstance(value, dict) or value.get("kind") != "status"
                    or not isinstance(value.get("lines"), (list, tuple))):
                continue
            for line in value["lines"][:2]:
                text = sanitize_terminal_text(f"{key}: {line}").replace("\n", " ").replace("\r", " ")
                rows.append(truncate_to_width(text, max(0, width), ""))
        return rows[:4]
