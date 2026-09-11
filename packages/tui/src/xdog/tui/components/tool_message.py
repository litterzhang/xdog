"""Shared compact tool-message presentation, independent of execution policy."""

from __future__ import annotations

import os
from collections.abc import Callable

from xdog.tui.editor_layout import grapheme_clusters
from xdog.tui.tui import Component
from xdog.tui.utils import sanitize_terminal_text, string_width, truncate_to_width


class ToolMessage(Component):
    """A stable header, result branch, and full retained detail body."""

    def __init__(self) -> None:
        self.header = ""
        self.summary = ""
        self.output = ""
        self.running = True
        self.style: Callable[[str], str] = lambda value: value

    def update(
        self, *, header: str, summary: str, output: str, running: bool,
        style: Callable[[str], str],
    ) -> None:
        self.header = " ".join(header.split())
        self.summary = " ".join(sanitize_terminal_text(summary).split())
        self.output = sanitize_terminal_text(output)
        self.running = running
        self.style = style

    @property
    def detail_body(self) -> str:
        return self.output

    def render(self, width: int) -> list[str]:
        width = max(0, width)
        branch = "    -> " if os.environ.get("XDOG_TUI_ASCII") == "1" else "  └ "
        if self.output:
            lines = self.output.splitlines()
            hidden = max(0, len(lines) - 1)
            suffix = (f" · {hidden} hidden lines; Ctrl+O" if width >= 60 else f" [+{hidden}]") if hidden else ""
            available = max(0, width - string_width(branch + suffix))
            text = " ".join((lines[-1] if self.running else lines[0]).split())
            if self.running:
                parts: list[str] = []
                used = 0
                for _left, _right, cluster in reversed(grapheme_clusters(text)):
                    size = string_width(cluster)
                    if used + size > available:
                        break
                    parts.append(cluster)
                    used += size
                preview = "".join(reversed(parts))
            else:
                preview = truncate_to_width(text, available, "…")
            detail = preview + suffix
        else:
            detail = self.summary if self.running else "(no output)"
        return [
            truncate_to_width(self.header, width, "…"),
            self.style(truncate_to_width(branch + detail, width, "…")),
            "",
        ]
