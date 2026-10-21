"""Claw's read-only history browser; Coding keeps its full navigation panel."""
from __future__ import annotations

from collections.abc import Callable

from xdog.tui.components.navigation_panel import NavigationPanel
from xdog.tui.navigation import NavigationEntry, NavigationFeed
from xdog.tui.utils import truncate_to_width


class HistoryPanel(NavigationPanel):
    def __init__(self, provider: NavigationFeed, on_view: Callable[[NavigationEntry], None],
                 on_close: Callable[[], None], *, scope: str = "sessions") -> None:
        super().__init__(provider, on_view, on_close, scope=scope, on_view=on_view)

    def render(self, width: int) -> list[str]:
        rows = super().render(width)
        if rows:
            phase = " · loading" if self._view.busy else ""
            rows[0] = self.panel_theme.header(truncate_to_width(
                f"History · {self._view.total} entries{phase} · Enter/v view (read-only) · → inspect · / search", width,
            ))
        return rows
