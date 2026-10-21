"""Shared compact tool-message presentation, independent of execution policy."""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Mapping

from xdog.tui.editor_layout import grapheme_clusters
from xdog.tui.tui import Component
from xdog.tui.utils import redact_sensitive_text, sanitize_terminal_text, string_width, truncate_to_width


class ToolMessage(Component):
    """A stable header, result branch, and full retained detail body."""

    def __init__(self) -> None:
        self.header = ""
        self.summary = ""
        self.output = ""
        self.running = True
        self.style: Callable[[str], str] = lambda value: value
        # Retain only the latest raw/safe input pair, never a cache per width.
        self._header_input: tuple[str, str] | None = None
        self._summary_input: tuple[str, str] | None = None
        self._output_input: tuple[str, str] | None = None
        self._render_key: tuple[object, ...] | None = None
        self._render_rows: list[str] = []
        self._preview_key: tuple[str, bool] | None = None
        self._preview: tuple[str, int] = ("", 0)

    @staticmethod
    def ascii_only() -> bool:
        return os.environ.get("XDOG_TUI_ASCII") == "1" or os.environ.get("TERM") == "dumb"

    def invalidate(self) -> None:
        self._render_key = None
        self._render_rows = []
        self._preview_key = None
        self._preview = ("", 0)

    @staticmethod
    def summarize_args(arguments: Mapping[str, object], tool_name: str = "") -> str:
        """Select a useful action/target, never dump arbitrary parameter bodies."""
        def safe(key: str, default: str = "") -> str:
            return redact_sensitive_text(str(arguments.get(key, default)))

        if tool_name == "bash":
            command = safe("command")
            if "\n" in command:
                first_line = command.splitlines()[0]
                if re.search(r"(?:^|[ /])python(?:\d+(?:\.\d+)*)?(?:\s|$)", first_line):
                    return "Python script"
                return "shell script"
            return command
        if tool_name == "filesystem":
            return f"{safe('action')} {safe('path')}".strip()
        if tool_name == "grep":
            return f"/{safe('pattern')}/ in {safe('path', '.')}"
        if tool_name == "find":
            return f"{safe('pattern')} in {safe('path', '.')}"
        action = safe("action")
        for key in ("path", "query", "pattern", "title", "name"):
            if key in arguments:
                return f"{action} {safe(key)}".strip()
        return action

    @staticmethod
    def make_header(tool_name: str, summary: str, state: str) -> str:
        """A single shared lifecycle vocabulary; success needs no extra badge."""
        name = " ".join(sanitize_terminal_text(tool_name).split())
        summary = " ".join(redact_sensitive_text(summary).split())
        label = summary if name == "bash" and summary else f"{name} {summary}".strip()
        verb = {"running": "Running", "success": "Ran", "error": "Failed", "canceled": "Cancelled"}[state]
        ascii_only = ToolMessage.ascii_only()
        return f"{'*' if ascii_only else '•'} {verb} {label}"

    def update(
        self, *, header: str, summary: str, output: str, running: bool,
        style: Callable[[str], str],
    ) -> None:
        if self._header_input is None or self._header_input[0] != header:
            self._header_input = (header, " ".join(header.split()))
        if self._summary_input is None or self._summary_input[0] != summary:
            self._summary_input = (summary, " ".join(sanitize_terminal_text(summary).split()))
        if self._output_input is None or self._output_input[0] != output:
            self._output_input = (output, sanitize_terminal_text(output))
        self.header = self._header_input[1]
        self.summary = self._summary_input[1]
        self.output = self._output_input[1]
        self.running = running
        self.style = style

    @property
    def detail_body(self) -> str:
        return self.output

    def render(self, width: int) -> list[str]:
        width = max(0, width)
        ascii_only = ToolMessage.ascii_only()
        # Public presentation fields remain mutable: snapshot their actual values.
        key = (width, ascii_only, self.header, self.summary, self.output, self.running, self.style)
        if key == self._render_key:
            return list(self._render_rows)
        # Match user/assistant: marker in column 2, text in column 4.
        inset = " " if width >= 4 else ""
        branch = inset + ("  -> " if ascii_only else "  └ ")
        if self.output:
            preview_key = (self.output, self.running)
            if preview_key != self._preview_key:
                lines = self.output.splitlines()
                self._preview = (
                    " ".join((lines[-1] if self.running else lines[0]).split()),
                    max(0, len(lines) - 1),
                )
                self._preview_key = preview_key
            text, hidden = self._preview
            suffix = (f" · {hidden} hidden lines; Ctrl+O" if width >= 60 else f" [+{hidden}]") if hidden else ""
            if not hidden and string_width(branch + text) > width and width >= 30:
                suffix = " · Ctrl+O"
            available = max(0, width - string_width(branch + suffix))
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
            detail = "(waiting for output)" if self.running else "(no output)"
        self._render_rows = [
            truncate_to_width(inset + self.header, width, "…"),
            self.style(truncate_to_width(branch + detail, width, "…")),
            "",
        ]
        self._render_key = key
        return list(self._render_rows)
