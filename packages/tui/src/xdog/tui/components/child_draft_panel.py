"""Current-session transient recovery and common exact review/image-preview primitives."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, replace

from xdog.tui.child_drafts import ChildDraft
from xdog.tui.components.attachment_panel import AttachmentPanel
from xdog.tui.components.panel import ThemedPanel
from xdog.tui.keys import KeyEvent, KeyEventType
from xdog.tui.utils import sanitize_terminal_text, truncate_to_width, wrap_text_with_ansi


@dataclass(frozen=True)
class DraftEntry:
    scope: str
    key: tuple[str, str]
    value: ChildDraft
    original: ChildDraft
    label: str
    current: bool

    @property
    def id(self) -> tuple[str, str, str]:
        return self.scope, *self.key


def draft_entries(caches: dict[str, dict[tuple[str, str], ChildDraft]], labels: dict[str, str],
                  current: str) -> tuple[DraftEntry, ...]:
    # A recovery chooser is not a cross-session or saved-file catalog. Callers
    # retain other data separately; filtering never deletes or migrates it.
    if current.startswith('saved-draft:'):
        return ()
    return tuple(DraftEntry(current, key, replace(value, config=json.loads(json.dumps(value.config))), value,
                           labels.get(current, 'Current session'), True)
                 for key, value in tuple(caches.get(current, {}).items())
                 if value.text or value.images or value.config or value.uncertain)



class ChildDraftPanel(ThemedPanel):
    requires_visible_confirmation = True
    requires_current_viewport_input = True

    def __init__(self, source: Callable[[], tuple[DraftEntry, ...]],
                 act: Callable[[str, DraftEntry], None], back: Callable[[], None], *,
                 parent: bool = False, save_only: bool = False) -> None:
        self.source, self.act, self.back = source, act, back
        self.parent = parent
        self.save_only = save_only
        self._budget = 8
        self._focused = False
        self._view_epoch = 0
        self._searching = False  # No child/primary search entrypoint; retained shared review state.
        self._selected: tuple[str, str, str] | None = None
        self._review: tuple[str, DraftEntry] | None = None
        self._reviewed: set[int] = set()
        self._scroll = 0
        self._layout = (0, 0)
        self._body: list[str] = []
        self._visible = False
        self._error = ''
        self._preview: AttachmentPanel | None = None
        self._preview_entry: DraftEntry | None = None
        self._preview_index = 0

    @property
    def focused(self) -> bool:
        return self._focused

    @focused.setter
    def focused(self, value: bool) -> None:
        if self._focused != value:
            self._view_epoch += 1
            self.invalidate()
        self._focused = value

    @property
    def view_lease(self) -> int:
        return self._view_epoch

    def invalidate(self) -> None:
        self._visible = False
        self._reviewed.clear()
        self._layout = (0, 0)
        if self._preview is not None:
            self._preview.invalidate()

    def close(self) -> None:
        self.focused = False
        self.invalidate()
        self._review = None
        self._preview = None
        self._preview_entry = None
        self._body.clear()
        self.source = lambda: ()

    def set_render_budget(self, rows: int) -> None:
        if max(0, rows) != self._budget:
            self.invalidate()
        self._budget = max(0, rows)

    def _rows(self) -> tuple[DraftEntry, ...]:
        rows = self.source()
        return rows if self.parent else tuple(row for row in rows
                                             if row.current and not row.scope.startswith('saved-draft:'))

    def _allowed(self, action: str, entry: DraftEntry) -> bool:
        if self.parent:
            return action == ('save' if self.save_only else 'copy')
        return action == 'reopen' and entry.current and not entry.scope.startswith('saved-draft:')

    def _prepare(self, action: str, entry: DraftEntry) -> None:
        if not self._allowed(action, entry):
            raise ValueError('Only current-session transient reopen is available; no child disk or catalog controls')
        self._review = action, entry
        self._reviewed.clear()
        self._scroll = 0
        self._layout = (0, 0)
        self._visible = False
        self._error = ''

    def _open_preview(self, entry: DraftEntry) -> None:
        current = next((row for row in self._rows() if row.id == entry.id), None)
        if current is None or current.original is not entry.original or current.value != entry.value:
            self._error = 'Draft changed before image preview; refresh without applying an action'
            return
        if not entry.value.images:
            self._error = 'This draft has no images to preview'
            return
        self._preview_entry = entry
        self._preview_index = 0
        self._set_preview()

    def _set_preview(self) -> None:
        assert self._preview_entry is not None
        self._preview = AttachmentPanel(self._preview_entry.value.images[self._preview_index], self._close_preview)
        self._preview.focused = self.focused
        self._visible = False
        # Preview is not receipt painting: require fresh full review on return.
        self._reviewed.clear()

    def _close_preview(self) -> None:
        self._preview = None
        self._preview_entry = None
        self._scroll = 0
        self._visible = False
        self._reviewed.clear()

    def handle_paste(self, text: str) -> bool:
        return True  # Clipboard/newlines never execute or confirm a recovery action.

    def handle_input(self, event: KeyEvent) -> bool:
        if not self.focused or event.event_type is not KeyEventType.PRESS:
            return True
        if self._preview is not None and self._preview_entry is not None:
            if event.key in {'left', 'right'}:
                self._preview_index = min(max(0, self._preview_index + (-1 if event.key == 'left' else 1)),
                                          len(self._preview_entry.value.images) - 1)
                self._set_preview()
            else:
                self._preview.handle_input(event)
            return True
        if event.matches('p'):
            entry = self._review[1] if self._review is not None else next(
                (row for row in self._rows() if row.id == self._selected), None)
            if entry is not None:
                self._open_preview(entry)
            return True
        if self._review is not None:
            if event.matches('escape') or event.matches('ctrl+c'):
                self._review = None
                self._visible = False
            elif event.key in {'up', 'down', 'pageup', 'pagedown', 'home', 'end'}:
                step = max(1, self._budget - 3) if event.key in {'pageup', 'pagedown'} else 1
                self._scroll = (0 if event.key == 'home' else 100000 if event.key == 'end' else
                                max(0, self._scroll + (-step if event.key in {'up', 'pageup'} else step)))
                self._visible = False
            elif event.matches('enter') and self._visible:
                action, entry = self._review
                self._visible = False
                try:
                    current = next((row for row in self._rows() if row.id == entry.id), None)
                    if (not self._allowed(action, entry) or current is None
                            or current.original is not entry.original or current.value != entry.value):
                        raise ValueError('Draft changed while reviewed; refresh/review, no replay')
                    self.act(action, entry)
                except (ValueError, RuntimeError, TypeError) as exc:
                    self._error = sanitize_terminal_text(str(exc))
                    self._review = None
            return True
        if event.matches('escape') or event.matches('ctrl+c'):
            self.back()
        elif event.key in {'up', 'down'}:
            rows = self._rows()
            index = next((index for index, row in enumerate(rows) if row.id == self._selected), 0)
            if rows:
                self._selected = rows[min(max(0, index + (-1 if event.key == 'up' else 1)), len(rows) - 1)].id
            self._visible = False
        elif self._visible and event.matches('enter'):
            row = next((row for row in self._rows() if row.id == self._selected), None)
            if row is not None:
                self._prepare('copy' if self.parent else 'reopen', row)
        return True

    def render(self, width: int) -> list[str]:
        self._visible = False
        if not self.focused or not self._budget:
            return []
        try:
            if self._preview is not None and self._preview_entry is not None:
                current = next((row for row in self._rows() if row.id == self._preview_entry.id), None)
                if (current is None or current.original is not self._preview_entry.original
                        or current.value != self._preview_entry.value):
                    self._close_preview()
                    self._error = 'Draft changed during preview; no action applied'
                    self._review = None
                else:
                    self._preview.set_render_budget(max(0, self._budget - 1))
                    return [self.panel_theme.header(truncate_to_width(
                        f'Draft image {self._preview_index + 1}/{len(current.value.images)} · ←/→ choose · Esc review',
                        max(0, width), '')), *self._render_child(self._preview, width)][:self._budget]
            if self._review is not None:
                action, entry = self._review
                count = max(0, self._budget - 3)
                if self._layout != (width, count):
                    self._layout = width, count
                    self._reviewed.clear()
                    self._body = wrap_text_with_ansi(sanitize_terminal_text(json.dumps({
                        'operation': action, 'source': entry.label, 'scope': entry.scope,
                        'current_context': entry.current, 'action_slot': entry.key,
                        'source_access': entry.value.access, 'source_config': entry.value.config,
                        'uncertain_previous_receipt': entry.value.uncertain,
                        'task': entry.value.text,
                        'images': [{'summary': item.summary, 'sha256': hashlib.sha256(item.data).hexdigest()}
                                   for item in entry.value.images],
                        'effect': ('Copy into EMPTY parent composer only; no submission or permission change'
                                   if self.parent else
                                   'Fresh READ-ONLY draft; source kept; old access/config NOT imported; no execution')
                                  if action == 'copy' else 'Save PRIVATE plaintext snapshot; source kept'
                                  if action == 'save' else
                                  'Reopen CURRENT transient input only; no submission, execution or tool grant',
                    }, ensure_ascii=False, indent=2)), max(1, width))
                self._scroll = min(self._scroll, max(0, len(self._body) - count))
                end = min(len(self._body), self._scroll + count)
                if count and width >= 24:
                    self._reviewed.update(range(self._scroll, end))
                self._visible = bool(count) and width >= 24 and len(self._reviewed) == len(self._body)
                rows = [f'Review saved parent draft {action}' if self.parent else f'Review local child draft {action}',
                        f'{self._scroll + 1}-{end}/{len(self._body)} · NOT execution',
                        *self._body[self._scroll:end],
                        'Enter applies reviewed action · PgDn all fields · p image preview · Esc back']
            else:
                entries = self._rows()
                if self._selected not in {row.id for row in entries}:
                    self._selected = entries[0].id if entries else None
                index = next((index for index, row in enumerate(entries) if row.id == self._selected), 0)
                rows = [f'Current-session child recovery · {len(entries)} unsent inputs · memory only']
                count = max(0, self._budget - len(rows) - 1 - bool(self._error))
                start = max(0, min(index - count // 2, len(entries) - count))
                for row in entries[start:start + count]:
                    rows.append(f'{"▶" if row.id == self._selected else " "} '
                        f'{row.key[0]}/{row.key[1]} · '
                        f'{len(row.value.images)} images · {"UNCERTAIN · " if row.value.uncertain else ""}'
                        + sanitize_terminal_text(row.value.text).replace('\n', ' '))
                if not entries and count:
                    rows.append('No unsent child input in this session')
                if self._error:
                    rows.append(self.panel_theme.error(self._error))
                rows.append('Enter review restore · p preview · Esc back' if self.parent else
                            'Enter reviews reopen · p preview · Esc back')
                self._visible = bool(entries) and width >= 24 and self._budget >= 3
        except (ValueError, TypeError, RuntimeError) as exc:
            rows = ['Current-session child recovery unavailable', sanitize_terminal_text(str(exc)),
                    'Esc back · source kept']
        return self._styled_rows(rows[:self._budget], width)
