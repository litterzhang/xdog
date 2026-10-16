"""Private exact-JSON review and bounded snapshots shared by retained native approvals."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any, cast

from xdog.tui.keys import KeyEvent, KeyEventType
from xdog.tui.tui import Component
from xdog.tui.utils import sanitize_terminal_text, truncate_to_width, wrap_text_with_ansi

_MAX_BYTES = 256 * 1024


def _json(value: Any) -> str:
    # Shape walk avoids a hostile deeply nested document consuming the renderer.
    stack = [(value, 0)]
    count = 0
    while stack:
        item, depth = stack.pop()
        count += 1
        if depth > 64 or count > 50000:
            raise ValueError('Review JSON shape exceeds capacity')
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                raise ValueError('JSON review object keys must be strings')
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)
        elif item is not None and type(item) not in (str, int, float, bool):
            raise ValueError('JSON review must contain JSON data')
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
        if len(encoded.encode()) > _MAX_BYTES:
            raise ValueError('Review JSON exceeds capacity')
        return encoded
    except (TypeError, UnicodeError, RecursionError) as exc:
        raise ValueError('Invalid review JSON') from exc


class _JsonReview(Component):
    """Private whole-data confirmation; hosts retain schema, ownership and IPC."""
    requires_visible_confirmation = True
    requires_current_viewport_input = True

    def __init__(self, event: dict[str, Any],
                 answer: Callable[[dict[str, Any]], None], cancel: Callable[[], None]) -> None:
        # Snapshot before inspecting any externally mutable event object.
        value = json.loads(_json(event))
        if (not isinstance(value, dict) or value.get('type') != 'json_review'
                or value.get('phase') != 'review' or not isinstance(value.get('body'), dict)
                or not isinstance(value.get('notice'), str) or len(value['notice'].encode()) > 8192
                or any(ord(c) < 0x20 and c != '\n' or ord(c) == 0x7f for c in value['notice'])):
            raise ValueError('Invalid JSON review envelope')
        for key in ('id', 'receipt'):
            item = value.get(key)
            if (not isinstance(item, str) or len(item) != 32
                    or any(c not in '0123456789abcdef' for c in item)):
                raise ValueError('Invalid JSON review identity')
        for key in ('server', 'lease'):
            if key in value and (not isinstance(value[key], str) or not 0 < len(value[key].encode()) <= 128
                                 or any(ord(c) < 0x20 or ord(c) == 0x7f for c in value[key])):
                raise ValueError('Invalid JSON review host identity')
        body = _json(value['body'])
        if hashlib.sha256(body.encode()).hexdigest() != value.get('sha256'):
            raise ValueError('JSON review digest mismatch')
        self._event = value
        self.answer, self.on_cancel = answer, cancel
        self._phase = value['phase']
        self._focused = False
        self._used = False
        self._disabled = False
        self._budget = 8
        self._scroll = 0
        self._seen: set[int] = set()
        self._layout = (0, 0, '')
        self._lines: list[str] = []
        self._visible = False
        self._message = ''

    @property
    def event(self) -> dict[str, Any]:
        return cast(dict[str, Any], json.loads(_json(self._event)))

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
        self._layout = (0, 0, '')
        self._scroll = 0

    def disable(self, message: str) -> None:
        """Host withdrawal permanently revokes this panel; late repaint cannot reenable."""
        self._disabled = True
        self._message = sanitize_terminal_text(message)
        self.invalidate()

    def set_render_budget(self, rows: int) -> None:
        rows = max(0, min(12, rows))
        if rows != self._budget:
            self.invalidate()
        self._budget = rows

    def _cancel(self) -> None:
        if not self._used:
            self._used = True
            self.on_cancel()


    def handle_paste(self, text: str) -> bool:
        return True  # Review paste never approves or changes exact data.

    def handle_input(self, event: KeyEvent) -> bool:
        if not self.focused or self._used or event.event_type != KeyEventType.PRESS:
            return True
        if event.matches('escape') or event.matches('ctrl+c'):
            self._cancel()
            return True
        if self._disabled:
            return True
        if event.matches('pagedown') or event.matches('down'):
            self._scroll += max(1, self._budget - 2) if event.matches('pagedown') else 1
        elif event.matches('pageup') or event.matches('up'):
            self._scroll = max(0, self._scroll - (max(1, self._budget - 2) if event.matches('pageup') else 1))
        elif event.matches('enter') and self._visible:
            self._used = True
            self.answer({'id': self._event['id'], 'phase': self._phase, 'receipt': self._event['receipt'],
                         'sha256': self._event['sha256'], 'approve': True})
        return True

    def render(self, width: int) -> list[str]:
        if self._budget < 3 or width < 12:
            self.invalidate()
            return []
        title = 'Review · ' + self._event.get('server', 'MCP peer')
        if self._disabled:
            return [truncate_to_width(title, width), truncate_to_width(self._message, width), 'Esc cancels']
        encoded = _json(self._event['body'])
        layout = (width, self._budget, encoded)
        if self._layout != layout:
            self.invalidate()
            self._layout = layout
            self._lines = wrap_text_with_ansi(sanitize_terminal_text(self._event['notice']), width)
            # JSON escapes controls instead of removing them; all byte metadata
            # remains inspectable, including image/audio data, without rendering it.
            # Word wrapping strips boundary spaces: inappropriate for exact
            # approval JSON. ASCII escaping also prevents C1/bidi controls from
            # becoming terminal instructions while preserving original values.
            displayed = json.dumps(json.loads(encoded), ensure_ascii=True, allow_nan=False,
                                   separators=(',', ':'))
            self._lines.extend(displayed[offset:offset + width] for offset in range(0, len(displayed), width))
        count = self._budget - 2
        self._scroll = min(self._scroll, max(0, len(self._lines) - count))
        if self.focused and not self._used:
            self._seen.update(range(self._scroll, min(len(self._lines), self._scroll + count)))
            self._visible = len(self._seen) == len(self._lines)
        footer = ('Enter approves exact ' + self._phase + ' · Esc denies' if self._visible else
                  'Read all pages · PgDown/PgUp · Esc denies')
        return [truncate_to_width(title, width), *self._lines[self._scroll:self._scroll + count],
                truncate_to_width(footer, width)]
