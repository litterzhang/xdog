"""Read-only child input/status inspection; Agent/SDK owners retain queue operations."""
from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from typing import Any

from xdog.tui.keys import KeyEvent, KeyEventType
from xdog.tui.tui import Component
from xdog.tui.utils import sanitize_terminal_text, truncate_to_width, wrap_text_with_ansi


class ChildQueuePanel(Component):
    def __init__(self, child: str, refresh: Callable[[], None], back: Callable[[], None]) -> None:
        self.owner_token = uuid.uuid4().hex
        self.child, self._refresh, self.back = child, refresh, back
        self.focused = False
        self.enabled = True
        self._closed = False
        self._budget = 8
        self._revision = ''
        self._rows: list[dict[str, Any]] = []
        self._selected = 0
        self._busy = False
        self._error = ''
        self._stopped = False
        self._details = False
        self._scroll = 0

    def set_render_budget(self, rows: int) -> None:
        self._budget = max(0, rows)

    def refresh(self) -> None:
        if self._closed or not self.focused or not self.enabled or self._busy:
            return
        self._busy = True
        try:
            self._refresh()  # No action, revision, input, draft or binary transport argument.
        except (ValueError, RuntimeError, TypeError) as exc:
            self.fail(str(exc))

    def publish(self, value: Any) -> None:
        if self._closed:
            return  # A retired view cannot adopt a late result or become a new request owner.
        if (not isinstance(value, dict) or set(value) - {'child_id', 'revision', 'inputs', 'stopped'}
                or value.get('child_id') != self.child or not isinstance(value.get('revision'), str)
                or len(value['revision']) != 64 or any(char not in '0123456789abcdef' for char in value['revision'])
                or not isinstance(value.get('inputs'), list) or len(value['inputs']) > 65):
            raise ValueError('Queue inspection accepts scoped list metadata only; no mutation/recovery receipt')
        if 'stopped' in value and type(value['stopped']) is not bool:
            raise ValueError('Invalid child queue stopped/status marker')
        rows = value['inputs']
        for row in rows:
            if (not isinstance(row, dict) or set(row) not in (
                    {'id', 'content', 'delivery', 'selected', 'image_count'},
                    {'id', 'content', 'delivery', 'selected', 'image_count', 'source', 'uncertain'})
                    or not isinstance(row['id'], str) or not 0 < len(row['id']) <= 128
                    or not isinstance(row['content'], str) or len(row['content'].encode()) > 16000
                    or not isinstance(row['delivery'], str) or row['delivery'] not in {'steer', 'follow_up'}
                    or type(row['selected']) is not bool
                    or type(row['image_count']) is not int or row['image_count'] < 0):
                raise ValueError('Invalid child queue input metadata')
            if 'source' in row and (not isinstance(row['source'], str)
                                    or row['source'] not in {'initial-task', 'crash-snapshot', 'durable-return'}
                                    or type(row['uncertain']) is not bool or value.get('stopped') is not True):
                raise ValueError('Invalid child queue recovery provenance')
        if (sum(row.get('source') == 'initial-task' for row in rows) > 1
                or sum(row.get('source') not in {'initial-task', 'durable-return'} for row in rows) > 32
                or sum(row.get('source') == 'durable-return' for row in rows) > 32):
            raise ValueError('Child queue inspection capacity exceeded')
        if len({row['id'] for row in rows}) != len(rows):
            raise ValueError('Duplicate child input identity')
        # Commit a complete metadata snapshot only after every row passed validation.
        selected = self._rows[self._selected]['id'] if self._rows else ''
        self._revision, self._rows = value['revision'], [dict(row) for row in rows]
        self._selected = next((i for i, row in enumerate(rows) if row['id'] == selected), 0)
        self._stopped = value.get('stopped', False)
        self._busy, self._error, self._scroll = False, '', 0
        self._details = self._details and bool(rows)

    def fail(self, error: str) -> None:
        if not self._closed:
            self._busy = False
            self._error = sanitize_terminal_text(error)

    def close(self) -> None:
        self._closed = True
        self.focused = False
        self._rows.clear()
        self._refresh = lambda: None

    def handle_paste(self, text: str) -> bool:
        return True  # There is no editor, submission or draft/recovery adoption route.

    def handle_input(self, event: KeyEvent) -> bool:
        if self._closed or not self.focused or event.event_type is not KeyEventType.PRESS:
            return True
        if event.matches('ctrl+c') or event.matches('escape') and not self._details:
            self.close()
            self.back()
        elif event.matches('escape'):
            self._details, self._scroll = False, 0
        elif event.matches('r'):
            self.refresh()
        elif event.key in {'up', 'down', 'pageup', 'pagedown', 'home', 'end'}:
            step = max(1, self._budget - 3) if event.key in {'pageup', 'pagedown'} else 1
            delta = -step if event.key in {'up', 'pageup'} else step
            if self._details:
                self._scroll = (0 if event.key == 'home' else 100000 if event.key == 'end' else
                                max(0, self._scroll + delta))
            elif self._rows:
                target = (0 if event.key == 'home' else len(self._rows) - 1 if event.key == 'end'
                          else self._selected + delta)
                self._selected = min(max(0, target), len(self._rows) - 1)
        elif event.matches('enter') and self._rows:
            self._details = True  # Full metadata inspection, never an action/permission confirmation.
        return True

    def render(self, width: int) -> list[str]:
        if self._closed or not self._budget:
            return []
        status = 'reading' if self._busy else 'stopped metadata' if self._stopped else 'captured metadata'
        rows = [f'Child input inspection {self.child} · READ ONLY · {status}']
        count = max(0, self._budget - 2 - bool(self._error))
        if self._details and self._rows:
            body = wrap_text_with_ansi(sanitize_terminal_text(json.dumps({
                'child': self.child, 'revision': self._revision, 'stopped': self._stopped,
                'input': self._rows[self._selected], 'effect': 'Inspection only; no recovery, restart or tool grant',
            }, ensure_ascii=False, indent=2)), max(1, width))
            self._scroll = min(self._scroll, max(0, len(body) - count))
            rows.extend(body[self._scroll:self._scroll + count])
            hint = 'Arrows/PgDn inspect · r refresh · Esc list · no action'
        else:
            start = max(0, min(self._selected - count // 2, len(self._rows) - count))
            for index in range(start, min(len(self._rows), start + count)):
                row = self._rows[index]
                rows.append(f'{"▶" if index == self._selected else " "} {row["id"][:12]} · {row["delivery"]} · '
                            f'{row.get("source", "input")} · {row["image_count"]} attachments · '
                            f'{"UNCERTAIN · " if row.get("uncertain") else ""}'
                            f'{"selected" if row["selected"] else "retained"} · '
                            + sanitize_terminal_text(row['content']).replace('\n', ' '))
            if not self._rows and count:
                rows.append('No retained child inputs · no automatic recovery or replay')
            hint = '↑/↓ choose · Enter inspect · r refresh · Esc back · READ ONLY'
        if self._error:
            rows.append(self._error)
        rows.append(hint)
        return [truncate_to_width(row, max(0, width), '') for row in rows[:self._budget]]
