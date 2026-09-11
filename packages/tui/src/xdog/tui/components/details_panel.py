"""Bounded, navigable detail records for inline terminal applications."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from xdog.tui.editor_layout import grapheme_clusters, layout_editor
from xdog.tui.keys import KeyEvent
from xdog.tui.tui import Component
from xdog.tui.utils import sanitize_terminal_text, string_width, truncate_to_width


def streaming_preview(text: str, *, max_cells: int = 200, max_lines: int = 3) -> str:
    """Keep the newest complete graphemes and report omitted output."""
    lines = text.splitlines()
    tail = " ".join(lines[-max_lines:])
    clusters = grapheme_clusters(tail)
    used = 0
    start = len(tail)
    for left, _right, cluster in reversed(clusters):
        cells = string_width(cluster)
        if used + cells > max_cells:
            break
        used += cells
        start = left
    hidden = max(0, len(lines) - max_lines)
    prefix = f"[{hidden} hidden lines"
    if start:
        prefix += "; earlier text omitted"
    return prefix + "] " + tail[start:] if hidden or start else tail


@dataclass(frozen=True, slots=True)
class DetailRecord:
    """Immutable source content for one tool or reasoning detail entry."""

    title: str
    body: str
    kind: str = "detail"


DetailProvider = Callable[[], Sequence[DetailRecord]]


class DetailsPanel(Component):
    """Render one detail record in a bounded scrolling panel."""

    focused = False

    def __init__(self, provider: DetailProvider, *, max_rows: int = 8) -> None:
        self._provider = provider
        self._max_rows = max(2, max_rows)
        self._selected_from_end = 0
        self._scroll = 0
        self._budget = self._max_rows
        self._scroll_limit = 0
        self._top: int | None = None
        self._entry_index: int | None = None
        self._entry_key: tuple[str, str] | None = None
        self._start_at_top = False

    def set_render_budget(self, max_rows: int) -> None:
        self._budget = max(0, min(self._max_rows, max_rows))

    def show_latest(self, *, from_start: bool = False) -> None:
        """Select the latest entry, optionally opening at its first line."""
        self._selected_from_end = 0
        self._scroll = 0
        self._top = None
        self._entry_index = None
        self._entry_key = None
        self._start_at_top = from_start
        if from_start:
            records = self._provider()
            self._entry_index = len(records) - 1 if records else None

    def set_max_rows(self, max_rows: int) -> None:
        self._max_rows = max(2, max_rows)
        self._budget = self._max_rows

    def handle_input(self, event: KeyEvent) -> bool:
        records = tuple(self._provider())
        if event.key in ("left", "right"):
            if records:
                index = self._entry_index if self._entry_index is not None else len(records) - 1
                self._entry_index = min(max(0, index + (-1 if event.key == "left" else 1)), len(records) - 1)
                self._top = None
                self._entry_key = None
            return True
        if event.key in ("pageup", "pagedown", "home", "end"):
            if records and self._entry_index is None:
                self._entry_index = len(records) - 1
            if event.key == "end":
                self.show_latest()
                # End explicitly follows the newest output, including reasoning.
                self._top = None
                self._entry_key = ("", "")
            elif event.key == "home":
                self._top = 0
            else:
                step = max(1, self._budget - 2)
                top = self._scroll_limit if self._top is None else self._top
                delta = -step if event.key == "pageup" else step
                self._top = min(self._scroll_limit, max(0, top + delta))
            return True
        return False

    def render(self, width: int) -> list[str]:
        if not self._budget:
            return []
        records = tuple(self._provider())
        if not records:
            return [truncate_to_width("No details available · Esc return to input", max(0, width), "")]
        index = len(records) - 1 if self._entry_index is None else min(self._entry_index, len(records) - 1)
        record = records[index]
        key = (record.kind, record.title)
        if self._entry_key != key:
            explicit_end = self._entry_key == ("", "")
            self._entry_key = key
            self._top = 0 if (self._start_at_top or record.kind == "reasoning") and not explicit_end else None
        body = [row.text for row in layout_editor(sanitize_terminal_text(record.body), max(1, width)).rows]
        body_rows = max(1, self._budget - 1)
        maximum = max(0, len(body) - body_rows)
        self._scroll_limit = maximum
        if self._top is not None:
            self._top = min(self._top, maximum)
        start = maximum if self._top is None else self._top
        visible = body[start:start + body_rows]
        title = sanitize_terminal_text(record.title).replace("\n", " ")
        mode = "follow" if self._top is None else "paused"
        header = (
            f"[{record.kind}] {title} ({index + 1}/{len(records)}) "
            f"• {start + 1}-{start + len(visible)}/{len(body)} {mode} "
            "• ←/→ entry · PgUp/PgDn · End latest · Esc close"
        )
        rows = (header, *visible) if self._budget > 1 else tuple(visible)
        return [truncate_to_width(line, max(0, width), "") for line in rows]
