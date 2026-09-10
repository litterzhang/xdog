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


class BoundedDetails(Component):
    """Render one detail record in a bounded scrolling panel."""

    focused = False

    def __init__(self, provider: DetailProvider, *, max_rows: int = 8) -> None:
        self._provider = provider
        self._max_rows = max(2, max_rows)
        self._selected_from_end = 0
        self._scroll = 0
        self._budget = self._max_rows
        self._scroll_limit = 0

    def set_render_budget(self, max_rows: int) -> None:
        self._budget = max(0, min(self._max_rows, max_rows))

    def show_latest(self) -> None:
        self._selected_from_end = 0
        self._scroll = 0

    def set_max_rows(self, max_rows: int) -> None:
        self._max_rows = max(2, max_rows)
        self._budget = self._max_rows

    def handle_input(self, event: KeyEvent) -> bool:
        records = tuple(self._provider())
        if event.key in ("left", "right"):
            if records:
                delta = 1 if event.key == "left" else -1
                self._selected_from_end = min(
                    max(0, self._selected_from_end + delta),
                    len(records) - 1,
                )
                self._scroll = 0
            return True
        if event.key in ("pageup", "pagedown"):
            step = max(1, self._budget - 2)
            delta = step if event.key == "pageup" else -step
            self._scroll = min(self._scroll_limit, max(0, self._scroll + delta))
            return True
        return False

    def render(self, width: int) -> list[str]:
        records = tuple(self._provider())
        if not records or not self._budget:
            return []
        selected = min(self._selected_from_end, len(records) - 1)
        record = records[len(records) - 1 - selected]
        body = [row.text for row in layout_editor(sanitize_terminal_text(record.body), max(1, width)).rows]
        body_rows = max(1, self._budget - 1)
        maximum = max(0, len(body) - body_rows)
        self._scroll_limit = maximum
        self._scroll = min(self._scroll, maximum)
        start = maximum - self._scroll
        visible = body[start:start + body_rows]
        position = len(records) - selected
        title = sanitize_terminal_text(record.title).replace("\n", " ")
        header = f"[{record.kind}] {title} ({position}/{len(records)}) • ←/→ entry • PgUp/PgDn scroll"
        rows = (header, *visible) if self._budget > 1 else tuple(visible)
        return [truncate_to_width(line, max(0, width), "") for line in rows]
