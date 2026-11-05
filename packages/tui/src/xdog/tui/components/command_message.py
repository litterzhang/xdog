"""Presentation-only slash invocation/result; never an Agent message or saved record."""

from __future__ import annotations

from collections.abc import Callable

from xdog.tui.components.panel import PanelTheme
from xdog.tui.tui import Component
from xdog.tui.utils import sanitize_terminal_text, truncate_to_width, wrap_text_with_ansi


class CommandMessage(Component):
    transcript_role = "system"

    def __init__(self, command: str, theme: PanelTheme, *, marker_style: Callable[[str], str] | None = None) -> None:
        self.detail_title = sanitize_terminal_text(command)
        self.detail_body = ""
        self._preview = ""
        self._marker_style = marker_style if marker_style is not None else theme.success
        self._theme = theme
        self._expanded = False

    @property
    def transcript_body(self) -> str:
        return self.detail_title + ("\n" + self.detail_body if self.detail_body else "")

    def set_result(self, text: str) -> None:
        self.detail_body = sanitize_terminal_text(text)
        self._preview = self.detail_body

    def append_result(self, text: str) -> None:
        previous = self.detail_body
        self.set_result(text)
        if previous:
            self.detail_body = previous + "\n\n" + self.detail_body

    def set_expanded(self, expanded: bool) -> None:
        self._expanded = expanded

    def render(self, width: int) -> list[str]:
        if width <= 0:
            return []
        gutter = min(3, max(0, width - 1))
        available = max(1, width - gutter)
        prefix = " " + self._marker_style("•") + " " if gutter == 3 else " " * gutter
        title = self.detail_title.replace("\n", " ").replace("\t", "    ")
        rows = [prefix + self._theme.accent(truncate_to_width(title, available, ""))]
        if self.detail_body:
            if self._expanded:
                body = wrap_text_with_ansi(self.detail_body, available)
            else:
                preview = wrap_text_with_ansi(self._preview, available)
                body = preview[:10]
                if len(preview) > 10:
                    body.append(self._theme.dim(truncate_to_width("Ctrl+O: full command result", available, "")))
            rows.extend(" " * gutter + row for row in body)
        return rows
