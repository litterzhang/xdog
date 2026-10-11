"""Remote metadata browser and exact recovery review, no disk or execution policy."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from xdog.tui.components.mcp_sampling_panel import McpSamplingPanel
from xdog.tui.keys import KeyEvent, KeyEventType
from xdog.tui.tui import Component
from xdog.tui.utils import sanitize_terminal_text, truncate_to_width


class RemoteParentDraftPanel(Component):
    requires_visible_confirmation = True
    requires_current_viewport_input = True

    def __init__(self, read: Callable[[dict[str, Any]], None], back: Callable[[], None], *,
                 discard: Callable[[dict[str, Any]], None] | None = None) -> None:
        self.read, self.back = read, back
        self.discard = discard
        self.rows: tuple[dict[str, Any], ...] = ()
        self.review: McpSamplingPanel | None = None
        self.message = 'Remote parent catalog loading'
        self.busy = True
        self._focused = False
        self._budget = 8
        self._selected = 0
        self._painted = False

    @property
    def focused(self) -> bool:
        return self._focused

    @focused.setter
    def focused(self, value: bool) -> None:
        if value != self._focused:
            self._painted = False
        self._focused = value
        if self.review is not None:
            self.review.focused = value

    def set_render_budget(self, rows: int) -> None:
        if rows != self._budget:
            self._painted = False
        self._budget = max(0, min(12, rows))
        if self.review is not None:
            self.review.set_render_budget(self._budget)

    def handle_paste(self, text: str) -> bool:
        return True  # Paste cannot choose/adopt a snapshot.

    def handle_input(self, event: KeyEvent) -> bool:
        if self.review is not None:
            return self.review.handle_input(event)
        if event.event_type is not KeyEventType.PRESS or not self.focused:
            return True
        if event.matches('escape') or event.matches('ctrl+c'):
            self.back()
        elif not self.busy and self.rows:
            if event.key in {'up', 'down'}:
                self._selected = min(max(0, self._selected + (-1 if event.key == 'up' else 1)), len(self.rows) - 1)
                self._painted = False
            elif event.matches('x') and self._painted and self.discard is not None:
                self.busy = True
                self.message = 'Preparing remote DISK discard review; composer unchanged'
                self.discard(self.rows[self._selected])
            elif event.matches('enter') and self._painted:
                self.busy = True
                self.message = 'Receiving exact remote parent snapshot; no adoption yet'
                self.read(self.rows[self._selected])
        return True

    def render(self, width: int) -> list[str]:
        if self.review is not None:
            return self.review.render(width)
        self._painted = bool(self.rows) and width >= 24 and self._budget >= 4
        start = min(self._selected, max(0, len(self.rows) - max(1, self._budget - 3)))
        rows = ['Remote saved parent drafts · metadata only']
        for index in range(start, min(len(self.rows), start + max(0, self._budget - 3))):
            row = self.rows[index]
            rows.append(f"{'▶' if index == self._selected else ' '} {row['id']} · {row['label']} · "
                        f"{row['image_count']} images")
        rows.extend([self.message, 'Enter recovery · x reviews DISK delete · Esc closes · never submits'])
        return [truncate_to_width(sanitize_terminal_text(row), max(0, width), '') for row in rows[:self._budget]]

    def close(self) -> None:
        if self.review is not None:
            self.review.disable('Remote parent view closed; no adoption')
        self.rows = ()
        self.busy = False
