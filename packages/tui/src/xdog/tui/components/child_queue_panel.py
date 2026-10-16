"""Reviewed child input controls over immutable host queue snapshots, never a local queue replica."""
from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from typing import Any

from xdog.tui.attachments import Attachment
from xdog.tui.child_drafts import draft_receipt
from xdog.tui.components.input_panel import InputPanel
from xdog.tui.keys import KeyEvent, KeyEventType
from xdog.tui.tui import Component
from xdog.tui.utils import sanitize_terminal_text, truncate_to_width, wrap_text_with_ansi


class _Theme:
    accent = bold = dim = border = staticmethod(lambda text: text)


class ChildQueuePanel(Component):
    requires_visible_confirmation = True

    def __init__(self, child: str, send: Callable[[dict[str, Any]], None], back: Callable[[], None],
                 returned: Callable[[dict[str, Any]], None]) -> None:
        self.owner_token = uuid.uuid4().hex
        self.child, self.send, self.back, self.returned = child, send, back, returned
        self._focused = False
        self.enabled = True
        self.image_recovery_enabled = False
        self.task_recovery_enabled = False
        self._budget = 8
        self._revision = ''
        self._rows: list[dict[str, Any]] = []
        self._selected = 0
        self._busy = True
        self._error = ''
        self._mode = 'list'
        self._confirm: dict[str, Any] | None = None
        self._reviewed: set[int] = set()
        self._scroll = 0
        self._layout = (0, 0)
        self._visible = False
        self._draft: tuple[str, str] | None = None
        self._pending_return: dict[str, Any] | None = None
        self._sent_action = ''
        self._stopped = False
        self._input = InputPanel(_Theme(), max_rows=3)
        self._input.on_submit = self._edit
        self._input.on_escape = self._cancel_edit

    @property
    def focused(self) -> bool:
        return self._focused

    @focused.setter
    def focused(self, value: bool) -> None:
        self._focused = value
        self._input.focused = value and self._mode == 'edit'

    def set_render_budget(self, rows: int) -> None:
        self._budget = max(0, rows)

    def refresh(self) -> None:
        if self.enabled and not self._busy:
            self._busy = True
            self.send({'action': 'list'})

    def publish(self, value: Any, images: tuple[Attachment, ...] = ()) -> None:
        if (not isinstance(value, dict) or value.get('child_id') != self.child
                or not isinstance(value.get('revision'), str) or len(value['revision']) != 64
                or any(char not in '0123456789abcdef' for char in value['revision'])
                or not isinstance(value.get('inputs'), list) or len(value['inputs']) > 65):
            raise ValueError('Invalid child queue scope/revision/size')
        rows = value['inputs']
        for row in rows:
            if (not isinstance(row, dict) or set(row) not in ({'id', 'content', 'delivery', 'selected', 'image_count'},
                                                    {'id', 'content', 'delivery', 'selected', 'image_count',
                                                     'source', 'uncertain'})
                    or not isinstance(row['id'], str) or not 0 < len(row['id']) <= 128
                    or not isinstance(row['content'], str) or len(row['content'].encode()) > 16000
                    or row['delivery'] not in {'steer', 'follow_up'} or type(row['selected']) is not bool
                    or type(row['image_count']) is not int or row['image_count'] < 0):
                raise ValueError('Invalid child queue input')
            if 'source' in row and (row['source'] not in {'initial-task', 'crash-snapshot', 'durable-return'}
                                    or type(row['uncertain']) is not bool
                                    or value.get('stopped') is not True):
                raise ValueError('Invalid initial-task recovery source')
        if (sum(row.get('source') == 'initial-task' for row in rows) > 1
                or sum(row.get('source') not in {'initial-task', 'durable-return'} for row in rows) > 32
                or sum(row.get('source') == 'durable-return' for row in rows) > 32):
            raise ValueError('Multiple initial tasks in child recovery')
        if len({row['id'] for row in rows}) != len(rows):
            raise ValueError('Duplicate child input identity')
        if 'stopped' in value and type(value['stopped']) is not bool:
            raise ValueError('Invalid child queue stopped/recovery marker')
        if any(key in value for key in ('return_prepared', 'return_token')):
            raise ValueError('Live image return is not a queue UI operation')
        recovered = value.get('recovered')
        if recovered is not None:
            if (value.get('stopped') is not True
                    or self._sent_action not in {'recover', 'recover_images', 'recover_task'}
                    or recovered != self._pending_return or recovered not in rows
                    or self._sent_action == 'recover' and recovered.get('image_count') != 0):
                raise ValueError('Recovered draft does not match reviewed retained stopped input')
        if self._sent_action in {'recover', 'recover_images', 'recover_task'} and recovered is None:
            raise ValueError('Stopped child recovery receipt missing; no invented draft')
        if (recovered is not None and self._sent_action in {'recover_images', 'recover_task'}
                and recovered['image_count']):
            if (not self.image_recovery_enabled or not images or len(images) != recovered['image_count']
                    or draft_receipt(recovered['content'], images) != value.get('image_receipt')):
                raise ValueError('Stopped image copy requires complete verified bytes and exact owned receipt')
        elif images or 'image_receipt' in value:
            raise ValueError('Unsolicited image recovery bytes/receipt')
        returned = value.get('returned')
        if returned is not None and recovered is not None:
            raise ValueError('Mixed live return and stopped recovery receipts')
        if returned is not None and (not isinstance(returned, dict) or returned.get('image_count') != 0
                or returned.get('selected') is not False or not isinstance(returned.get('content'), str)
                or len(returned['content'].encode()) > 16000 or returned.get('delivery') not in {'steer', 'follow_up'}):
            raise ValueError('Invalid returned child draft receipt')
        if returned is not None:
            returned_id = returned.get('id')
            if returned != self._pending_return or any(row['id'] == returned_id for row in rows):
                raise ValueError('Returned draft does not match the reviewed removed input')
        if self._sent_action == 'return' and returned is None:
            raise ValueError('Returned child draft receipt missing; no invented recovery')
        if self._sent_action == 'edit':
            self._draft = None
        self._sent_action = ''
        self._pending_return = None
        self._error = ''
        self._stopped = value.get('stopped', False)
        self._revision, self._rows = value['revision'], [dict(row) for row in rows]
        self._busy = False
        self._selected = min(self._selected, max(0, len(rows) - 1))
        self._visible = False
        if returned is not None:
            self.returned(dict(returned))  # Only an actual owner removal receipt restores a live draft.
        elif recovered is not None:
            self.returned({**recovered, 'stopped': True, **({'images': images} if images else {})})
            # Explicit durable-copy receipt, never restart.

    def fail(self, error: str) -> None:
        self._busy = False
        self._error = sanitize_terminal_text(error)
        if self._draft is not None and self._sent_action == 'edit':
            self._mode = 'edit'
            self._input.set_text(self._draft[1])
            self._input.focused = self.focused
            self._visible = False
        self._sent_action = ''
        self._pending_return = None

    def close(self) -> None:
        """No live image-return lease or automatic queue mutation is owned by this UI."""

    def _cancel_edit(self) -> None:
        if self._mode == 'review' and self._confirm is not None and self._confirm['action'] == 'edit':
            self._mode = 'edit'
            self._input.set_text(self._confirm['content'])
            self._input.focused = self.focused
            self._confirm = None
            self._visible = False
            return
        if self._mode == 'edit' and self._rows:
            self._draft = self._rows[self._selected]['id'], self._input.get_text()
        self._mode = 'list'
        self._input.focused = False
        self._visible = False

    def _edit(self, text: str) -> None:
        if not self._visible or not text.strip() or len(text.encode()) > 16000 or not self._rows:
            return
        self._draft = self._rows[self._selected]['id'], text
        self._review('edit', content=text)

    def _review(self, action: str, **fields: Any) -> None:
        if not self.enabled or self._busy or not self._rows:
            return
        row = self._rows[self._selected]
        if action not in {'edit', 'remove', 'move', 'return', 'recover', 'recover_images',
                          'recover_task', 'remove_task'}:
            raise ValueError('Unsupported queue UI action')
        if action == 'return' and row['image_count']:
            return  # Live image return is Agent-tool-only; never remove or copy via a user panel.
        if row.get('source') == 'initial-task' and action in {'recover', 'remove'}:
            if not self.task_recovery_enabled:
                self._error = 'Updated initial-task recovery owner required; no ambiguous queue fallback'
                return
            action = 'recover_task' if action == 'recover' else 'remove_task'
        if action in {'recover', 'recover_task'} and row['image_count']:
            if not self.image_recovery_enabled:
                self._error = 'Updated stopped-image recovery transport required; no lossy text copy'
                return
            action = 'recover_task' if action == 'recover_task' else 'recover_images'
        if row['selected']:
            self._error = 'Input already selected at a checkpoint; refresh before editing'
            return
        self._confirm = {'action': action, 'id': row['id'], 'revision': self._revision, **fields}
        self._mode = 'review'
        self._reviewed.clear()
        self._scroll = 0
        self._layout = (0, 0)
        self._visible = False
        self._input.focused = False

    def handle_paste(self, text: str) -> bool:
        if self._mode == 'edit':
            return self._input.handle_paste(text)
        return True

    def handle_input(self, event: KeyEvent) -> bool:
        if event.event_type is not KeyEventType.PRESS:
            return True
        if self._mode == 'edit':
            if not event.matches('enter') or self._visible and self.enabled:
                self._input.handle_input(event)
            return True
        if self._mode == 'review':
            if event.matches('escape') or event.matches('ctrl+c'):
                self._cancel_edit()
            elif event.key in {'up', 'down', 'pageup', 'pagedown', 'home', 'end'}:
                delta = max(1, self._budget - 3) if event.key in {'pageup', 'pagedown'} else 1
                self._scroll = (0 if event.key == 'home' else 100000 if event.key == 'end' else
                               max(0, self._scroll + (-delta if event.key in {'up', 'pageup'} else delta)))
                self._visible = False
            elif (event.matches('enter') and self.enabled and self._visible
                  and self._confirm is not None and not self._busy):
                command, self._confirm = self._confirm, None
                self._busy, self._mode = True, 'list'
                self._sent_action = command['action']
                if command['action'] in {'return', 'recover', 'recover_images', 'recover_task'}:
                    self._pending_return = dict(self._rows[self._selected])
                self.send(command)
            return True
        if event.matches('escape') or event.matches('ctrl+c'):
            self.close()
            self.back()
        elif event.matches('r'):
            self.refresh()
        elif not self.enabled or self._busy or not self._visible:
            return True
        elif event.key in {'up', 'down'}:
            self._selected = min(max(0, self._selected + (-1 if event.key == 'up' else 1)), max(0, len(self._rows) - 1))
            self._visible = False
        elif (event.matches('e') and not self._stopped and self._rows
              and not self._rows[self._selected]['selected']):
            self._mode = 'edit'
            row = self._rows[self._selected]
            self._input.set_text(self._draft[1] if self._draft is not None and self._draft[0] == row['id']
                                 else row['content'])
            self._input.focused = self.focused
            self._visible = False
        elif event.key in {'x', 'd', 'u', 'j', 'k'}:
            if event.key in {'j', 'k'} and not self._stopped:
                self._review('move', direction=-1 if event.key == 'k' else 1)
            elif event.key not in {'j', 'k'}:
                self._review(('recover' if self._stopped else 'return') if event.key == 'u' else 'remove')
        return True

    def render(self, width: int) -> list[str]:
        self._visible = False
        if not self._budget:
            return []
        title = f'Child input queue {self.child} · ' + (
            'settling' if self._busy else 'saved recovery' if self._stopped else 'captured')
        if self._mode == 'edit':
            self._input.set_render_budget(max(1, min(3, self._budget - 3)), show_borders=False)
            rows = [title, *self._input.render(width), 'Enter reviews edit · Esc cancels · no prompt submission']
            self._visible = self.enabled and self._budget >= 4 and width >= 24
        elif self._mode == 'review' and self._confirm is not None:
            row = next((item for item in self._rows if item['id'] == self._confirm['id']), None)
            body = wrap_text_with_ansi(sanitize_terminal_text(json.dumps(
                {'child': self.child, 'input': row, 'request': self._confirm}, ensure_ascii=False, indent=2)),
                max(1, width))
            count = max(0, self._budget - 3)
            if self._layout != (width, count):
                self._reviewed.clear()
                self._layout = width, count
            self._scroll = min(self._scroll, max(0, len(body) - count))
            end = min(len(body), self._scroll + count)
            if width >= 24 and count:
                self._reviewed.update(range(self._scroll, end))
            rows = [title,
                    f'Review {self._scroll + 1}-{end}/{len(body)} · NOT tool approval',
                    *body[self._scroll:end], 'Enter applies reviewed queue change · PgDn reviews · Esc back']
            self._visible = self.enabled and bool(count) and width >= 24 and len(self._reviewed) == len(body)
        else:
            rows = [title]
            count = max(0, self._budget - 3)
            start = max(0, min(self._selected - count // 2, len(self._rows) - count))
            for index in range(start, min(len(self._rows), start + count)):
                row = self._rows[index]
                rows.append(f'{"▶" if index == self._selected else " "} {row["id"][:12]} '
                    f'{row["delivery"]} · {"selected" if row["selected"] else "queued"} · '
                    + sanitize_terminal_text(row['content']).replace('\n', ' '))
            if not self._rows and count:
                rows.append('No pending child inputs · selected/consumed inputs cannot be edited')
            if self._error:
                rows.append(self._error)
            rows.append('u copy to fresh task draft · x discard saved input · r refresh · Esc back'
                        if self._stopped else
                        'e edit · x remove · k/j move · '
                        + ('u return text to child draft · ' if self._rows
                           and not self._rows[self._selected]['image_count'] else '')
                        + 'r refresh · Esc back')
            self._visible = self.enabled and self._budget >= 4 and width >= 24
        return [truncate_to_width(row, max(0, width), '') for row in rows[:self._budget]]
