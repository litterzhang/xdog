"""Tool execution component for the interactive TUI."""

from __future__ import annotations

import base64
import os
import re
import time
from typing import Any

from xdog.ai.types import ImageContent, ToolResultContentPart
from xdog.coding.modes.interactive.theme import Theme
from xdog.tui.components.bounded_details import streaming_preview
from xdog.tui.components.diff import Diff
from xdog.tui.components.image import Image
from xdog.tui.components.text import Text
from xdog.tui.components.tool_message import ToolMessage as ToolMessageView
from xdog.tui.tui import Container
from xdog.tui.utils import redact_sensitive_text, sanitize_terminal_text

# Threshold for collapsing large non-diff output
_COLLAPSE_THRESHOLD = 500


class ToolExecutionComponent(Container):
    """Renders a tool call with its name, parameters, and result.

    Tracks execution state (pending → running → success/error) and renders
    appropriate visual indicators. For edit operations that produce diffs,
    the result is rendered using the Diff component with colored lines and
    intra-line highlighting. Large outputs are collapsed with a summary.
    """

    def __init__(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None,
        theme: Theme,
    ) -> None:
        super().__init__()
        self._theme = theme
        self._view = ToolMessageView()
        self._started = time.monotonic()
        self._elapsed: int | None = None
        self._tool_name = " ".join(sanitize_terminal_text(tool_name).split())
        self._state = "running"  # running → success | error
        self._result_text: Text | None = None
        self._diff_component: Diff | None = None
        self._header_text: Text | None = None
        self._result = ""
        self._is_error = False
        self._expanded = False
        self._argument_summary = " ".join(_summarize_args(arguments or {}, tool_name).split())
        command = redact_sensitive_text(str((arguments or {}).get("command", "")))
        self._command_details = command if tool_name == "bash" and "\n" in command else ""

        # Tool call header with state icon
        header_str = self._make_header()
        self._header_text = Text(header_str, 0, 0)
        self.add_child(self._header_text)

        # Show arguments if present
        if arguments:
            summary = _summarize_args(arguments, tool_name)
            if summary:
                self.add_child(Text(theme.dim(f"    {summary}"), 0, 0))

    def render(self, width: int) -> list[str]:
        """Two stable text rows in compact mode; rich output stays expandable."""
        if self._expanded:
            return [*super().render(width), ""]
        header = self._make_header()
        if self._argument_summary:
            header += self._theme.dim(f" · {self._argument_summary}")
        self._view.update(
            header=header, summary=self._argument_summary, output=self._result,
            running=self._state == "running",
            style=self._theme.error if self._is_error else self._theme.dim,
        )
        rows = self._view.render(width)[:-1]
        for child in self.children:
            if isinstance(child, Image):
                rows.extend(child.render(width))
        return [*rows, ""]

    @property
    def detail_title(self) -> str:
        """Stable title used by bounded detail providers."""
        return self._tool_name

    @property
    def detail_body(self) -> str:
        """Return the complete raw result retained independently of previews."""
        if self._command_details:
            return f"Command:\n{self._command_details}\n\nOutput:\n{self._result}"
        return self._result

    def _make_header(self) -> str:
        """Build header string with state-appropriate icon and color."""
        icons = {
            "running": "⚡",
            "success": "✓",
            "error": "✗",
            "canceled": "■",
        }
        if os.environ.get("XDOG_TUI_ASCII") == "1" or os.environ.get("TERM") == "dumb":
            icons = {"running": "[run]", "success": "[ok]", "error": "[err]", "canceled": "[stop]"}
        icon = icons.get(self._state, "[run]")
        color_fns = {
            "running": self._theme.tool,
            "success": self._theme.success,
            "error": self._theme.error,
            "canceled": self._theme.dim,
        }
        color_fn = color_fns.get(self._state, self._theme.tool)
        elapsed = f" · {self._elapsed}s" if self._elapsed is not None else ""
        return color_fn(f"  {icon} {self._tool_name} · {self._state}{elapsed}")

    def _update_header(self) -> None:
        """Refresh the header text after a state change."""
        if self._header_text is not None:
            self._header_text.set_text(self._make_header())

    def set_streaming(self, text: str) -> None:
        """Show live streaming output preview (e.g. bash stdout)."""
        # Retain full output; the transcript previews the newest complete cells.
        safe_text = sanitize_terminal_text(text)
        self._result = safe_text
        display = streaming_preview(safe_text)
        if self._result_text is not None:
            self._result_text.set_text(self._theme.dim(f"    ⏳ {display}"))
        else:
            self._result_text = Text(
                self._theme.dim(f"    ⏳ {display}"),
                0, 0,
            )
            self.add_child(self._result_text)

    def set_canceled(self) -> None:
        """Mark an active tool terminal without discarding its last preview."""
        self._elapsed = max(0, int(time.monotonic() - self._started))
        self._state = "canceled"
        self._update_header()
        if self._result_text is None:
            self._result_text = Text(
                self._theme.dim("    → Cancelled"),
                0,
                0,
            )
            self.add_child(self._result_text)

    def set_content(self, content: tuple[ToolResultContentPart, ...]) -> None:
        for part in content:
            if isinstance(part, ImageContent):
                try:
                    data = base64.b64decode(part.data, validate=True)
                except (ValueError, TypeError):
                    continue
                self.add_child(Image(data=data, alt=part.mime_type))

    def set_result(self, result: str, *, is_error: bool = False) -> None:
        """Retain a complete tool result and render its selected detail level."""
        self._elapsed = max(0, int(time.monotonic() - self._started))
        self._state = "error" if is_error else "success"
        self._result = sanitize_terminal_text(result)
        self._is_error = is_error
        self._update_header()
        self._render_result()

    def set_expanded(self, expanded: bool) -> None:
        """Show the complete result or a compact summary."""
        if self._expanded == expanded:
            return
        self._expanded = expanded
        self._render_result()

    def _render_result(self) -> None:
        result = self._result
        if not result:
            if self._diff_component is not None:
                self.remove_child(self._diff_component)
                self._diff_component = None
            if self._result_text is not None:
                self._result_text.set_text(self._theme.dim("    → (no output)"))
            return

        if self._diff_component is not None:
            self.remove_child(self._diff_component)
            self._diff_component = None

        if _contains_diff(result) and self._expanded:
            summary = _extract_summary(result)
            display = summary or "diff"
            self._set_result_text(display)
            self._diff_component = Diff(
                _extract_diff(result),
                padding_left=4,
                color_added=self._theme.diff_added,
                color_removed=self._theme.diff_removed,
                color_context=self._theme.diff_context,
                inverse=self._theme.inverse,
            )
            self.add_child(self._diff_component)
            return

        if self._expanded:
            display = result
        elif len(result) > _COLLAPSE_THRESHOLD or len(result.splitlines()) > 3:
            lines = result.splitlines()
            excerpt = "\n".join(lines[:3])[:200]
            preview = excerpt.replace("\n", " ").replace("\r", "")
            hidden = len(lines) - len(excerpt.splitlines())
            display = (f"{preview} [...{len(result) - len(excerpt)} more chars, "
                       f"{hidden} hidden lines; Ctrl+O for details]")
        else:
            display = _truncate(result, 200)
        self._set_result_text(display)

    def _set_result_text(self, display: str) -> None:
        color_fn = self._theme.error if self._is_error else self._theme.dim
        rendered = color_fn(f"    → {display}")
        if self._result_text is None:
            self._result_text = Text(rendered, 0, 0)
            self.add_child(self._result_text)
        else:
            self._result_text.set_text(rendered)


_DIFF_LINE_RE = re.compile(r"^[+\-]\s*\d+\s", re.MULTILINE)


def _contains_diff(text: str) -> bool:
    """Check if the text contains a custom diff (``+linenum`` / ``-linenum`` format)."""
    return _DIFF_LINE_RE.search(text) is not None


def _extract_diff(text: str) -> str:
    """Extract the custom diff portion from the result text.

    The result typically starts with a summary line like
    "Successfully replaced 1 occurrence(s) in /path/to/file"
    followed by a blank line and the diff (lines starting with +/-/space + linenum).
    """
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if _DIFF_LINE_RE.match(line) or line == "...":
            return "\n".join(lines[i:])
    return text


def _extract_summary(text: str) -> str:
    """Extract the summary line before the diff."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if _DIFF_LINE_RE.match(line) or line == "...":
            summary_lines = lines[:i]
            return " ".join(ln.strip() for ln in summary_lines if ln.strip())
    return ""


def _summarize_args(args: dict[str, Any], tool_name: str = "") -> str:
    """Create a tool-aware compact summary of arguments.

    Uses specialized formatting for known tools (bash, filesystem, grep, find)
    and falls back to generic key=value pairs for unknown tools.
    """
    if tool_name == "bash":
        cmd = redact_sensitive_text(str(args.get("command", "")))
        if "\n" in cmd:
            first_line = cmd.splitlines()[0]
            if re.search(r"(?:^|[ /])python(?:\d+(?:\.\d+)*)?(?:\s|$)", first_line):
                return "Run Python script"
            return "Run shell script"
        return cmd if len(cmd) <= 80 else cmd[:77] + "..."
    if tool_name == "filesystem":
        action = sanitize_terminal_text(str(args.get("action", "")))
        path = redact_sensitive_text(str(args.get("path", "")))
        return f"{action} {path}"
    if tool_name == "grep":
        pattern = redact_sensitive_text(str(args.get("pattern", "")))
        path = redact_sensitive_text(str(args.get("path", ".")))
        return f"/{pattern}/ in {path}"
    if tool_name == "find":
        pattern = redact_sensitive_text(str(args.get("pattern", "")))
        path = redact_sensitive_text(str(args.get("path", ".")))
        return f"{pattern} in {path}"
    return _generic_summarize_args(args)


def _generic_summarize_args(args: dict[str, Any]) -> str:
    """Generic key=value argument summary."""
    parts: list[str] = []
    for key, value in args.items():
        if isinstance(value, str):
            safe_value = redact_sensitive_text(value)
            display = safe_value if len(safe_value) <= 60 else safe_value[:57] + "..."
            parts.append(f"{key}={display!r}")
        elif isinstance(value, (int, float, bool)):
            parts.append(f"{key}={value}")
        else:
            parts.append(f"{key}=...")
    result = ", ".join(parts)
    return result[:200] if len(result) > 200 else result


def _truncate(text: str, max_len: int) -> str:
    """Truncate text to max_len, replacing newlines with spaces."""
    text = text.replace("\n", " ").replace("\r", "")
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."
