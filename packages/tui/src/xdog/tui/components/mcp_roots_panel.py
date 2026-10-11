"""Policy-free full-paint roots disclosure review; hosts own leases and transport."""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, cast

from xdog.tui.keys import KeyEvent, KeyEventType
from xdog.tui.tui import Component
from xdog.tui.utils import sanitize_terminal_text, truncate_to_width, wrap_text_with_ansi


class McpRootsReview(Component):
    requires_visible_confirmation = True

    def __init__(self, review: dict[str, Any], commit: Callable[[str], None], cancel: Callable[[], None]) -> None:
        identifier, roots = review.get('review_id'), review.get('roots')
        if (review.get('state') != 'review' or not isinstance(identifier, str) or len(identifier) != 32
                or any(c not in '0123456789abcdef' for c in identifier) or not isinstance(roots, list)
                or len(roots) > 16 or not isinstance(review.get('notice'), str)
                or len(review['notice'].encode()) > 8192
                or not isinstance(review.get('server'), str) or not 0 < len(review['server'].encode()) <= 128
                or any(ord(c) < 0x20 or ord(c) == 0x7f for c in review['server'])
                or any(ord(c) < 0x20 and c != '\n' or ord(c) == 0x7f for c in review['notice'])
                or any(not isinstance(row, dict) or set(row) - {'uri', 'name'}
                       or not isinstance(row.get('uri'), str) or len(row['uri'].encode()) > 4096
                       or not isinstance(row.get('name', ''), str) or len(row.get('name', '').encode()) > 256
                       or any(ord(c) < 0x20 or ord(c) == 0x7f for c in row['uri'] + row.get('name', ''))
                       for row in roots)):
            raise ValueError('Invalid roots disclosure review')
        self._review = json.loads(json.dumps(review))
        self.commit, self.cancel = commit, cancel
        self._focused = False
        self._budget = 8
        self._scroll = 0
        self._seen: set[int] = set()
        self._layout = (0, 0)
        self._visible = False
        self._used = False

    @property
    def review(self) -> dict[str, Any]:
        """Inspection snapshot cannot mutate the bytes covered by paint proof."""
        return cast(dict[str, Any], json.loads(json.dumps(self._review)))

    @property
    def focused(self) -> bool:
        return self._focused

    @focused.setter
    def focused(self, value: bool) -> None:
        if value != self._focused:
            self.invalidate()
        self._focused = value

    def invalidate(self) -> None:
        self._seen.clear()
        self._visible = False
        self._layout = (0, 0)

    def set_render_budget(self, rows: int) -> None:
        rows = max(0, min(12, rows))
        if self._budget != rows:
            self.invalidate()
        self._budget = rows

    def handle_input(self, event: KeyEvent) -> bool:
        if not self.focused or self._used or event.event_type != KeyEventType.PRESS:
            return True
        if event.matches('escape') or event.matches('ctrl+c'):
            self._used = True
            self.cancel()
        elif event.matches('pagedown') or event.matches('down'):
            self._scroll += max(1, self._budget - 2) if event.matches('pagedown') else 1
        elif event.matches('pageup') or event.matches('up'):
            self._scroll = max(0, self._scroll - (max(1, self._budget - 2) if event.matches('pageup') else 1))
        elif event.matches('enter') and self._visible:
            self._used = True
            self.commit(self._review['review_id'])
        return True

    def render(self, width: int) -> list[str]:
        if self._budget < 3 or width < 12:
            self.invalidate()
            return []
        body = []
        for text in [self._review['notice'], *(f"{row.get('name', '')} · {row['uri']}"
                     for row in self._review['roots']),
                     *(['EMPTY: withdraw all roots'] if not self._review['roots'] else [])]:
            body.extend(wrap_text_with_ansi(sanitize_terminal_text(text), width))
        layout = (width, self._budget)
        if layout != self._layout:
            self.invalidate()
            self._layout = layout
            self._scroll = 0
        count = self._budget - 2
        self._scroll = min(self._scroll, max(0, len(body) - count))
        if self.focused and not self._used:
            self._seen.update(range(self._scroll, min(len(body), self._scroll + count)))
            self._visible = len(self._seen) == len(body)
        footer = ('Enter confirms fully reviewed roots · Esc cancel' if self._visible else
                  'Read all pages · PgDown/PgUp · Esc cancel')
        return [truncate_to_width('MCP root disclosure · ' + self._review['server'], width),
                *body[self._scroll:self._scroll + count], truncate_to_width(footer, width)]
