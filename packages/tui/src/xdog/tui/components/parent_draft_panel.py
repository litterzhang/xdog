"""Small primary-only save/restore surface using existing exact review and image preview."""
from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from xdog.tui.components.child_draft_panel import ChildDraftPanel, DraftEntry
from xdog.tui.keys import KeyEvent, KeyEventType
from xdog.tui.utils import sanitize_terminal_text, truncate_to_width


class ParentDraftPanel(ChildDraftPanel):
    """No child browser, directory management, delete, execution or automatic restore."""
    requires_current_viewport_input = True

    def __init__(self, source: Callable[[], tuple[DraftEntry, ...]], act: Callable[[str, DraftEntry], None],
                 cancel: Callable[[], None], *, context: str, mode: Literal['loading', 'save', 'restore']) -> None:
        self._focused = False
        self._owner_epoch = 0
        super().__init__(source, act, cancel, parent=True, save_only=mode == 'save')
        self.context, self.mode = context, mode

    @property
    def focused(self) -> bool:
        return self._focused

    @focused.setter
    def focused(self, value: bool) -> None:
        if self._focused != value:
            self._owner_epoch += 1
            self.invalidate()
        self._focused = value

    @property
    def view_lease(self) -> int:
        return self._owner_epoch

    def invalidate(self) -> None:
        self._visible = False
        self._reviewed.clear()
        self._layout = (0, 0)
        if self._preview is not None:
            self._preview.invalidate()

    def close(self) -> None:
        """Release dismissed review/preview bytes without touching any admitted disk worker."""
        self.focused = False
        self.invalidate()
        self._preview = None
        self._preview_entry = None
        self._review = None
        self._body.clear()
        self.source = lambda: ()

    def set_render_budget(self, rows: int) -> None:
        if max(0, rows) != self._budget:
            self.invalidate()
        super().set_render_budget(rows)

    def prepare_save(self, entry: DraftEntry) -> None:
        if self.mode != 'save':
            raise ValueError('Not a primary save review')
        self._prepare('save', entry)

    def handle_input(self, event: KeyEvent) -> bool:
        if not self.focused or event.event_type is not KeyEventType.PRESS:
            return True
        if self.mode == 'loading':
            if event.matches('escape') or event.matches('ctrl+c'):
                self.back()  # Revoke adoption, never abandon an admitted disk worker.
            return True
        if self.mode == 'save' and self._preview is None and (event.matches('escape') or event.matches('ctrl+c')):
            self.back()
            return True
        if self.mode == 'save' and self._preview is None and self._review is None:
            return True  # A refused exact save must be explicitly reopened, never replayed.
        if self._preview is None and self._review is None and event.key in {'x', 's', 'r', '/', 'delete'}:
            return True  # Primary recovery has no catalog/delete/reopen management controls.
        return super().handle_input(event)

    def render(self, width: int) -> list[str]:
        if not self.focused:
            self.invalidate()
            return []
        if self.mode == 'loading':
            self._visible = False
            return [truncate_to_width(row, max(0, width), '') for row in [
                'Reading primary snapshots · nothing restored', 'Esc cancels read adoption · disk work still settles',
            ][:self._budget]]
        if self._preview is not None or self._review is not None:
            rows = super().render(width)
            if rows:
                if self._preview is not None:
                    rows[0] = truncate_to_width(
                        f'Primary image {self._preview_index + 1} · ←/→ choose · Esc review', max(0, width), '')
                else:
                    rows[0] = truncate_to_width(
                        'Review primary draft ' + ('save' if self.mode == 'save' else 'restore'), max(0, width), '')
            return rows
        self._visible = False
        if not self._budget:
            return []
        if self.mode == 'save':
            return [truncate_to_width(row, max(0, width), '') for row in [
                'Primary save not admitted · composer kept', self._error,
                'Esc closes · reopen /draft save to review current composer',
            ][:self._budget]]
        entries = self.source()
        if len(entries) > 32:
            raise ValueError('Primary snapshot selector exceeds capacity')
        if self._selected not in {entry.id for entry in entries}:
            self._selected = entries[0].id if entries else None
        index = next((index for index, entry in enumerate(entries) if entry.id == self._selected), 0)
        count = max(0, self._budget - 2 - bool(self._error))
        start = max(0, min(index - count // 2, len(entries) - count))
        rows = [f'Restore primary draft · {len(entries)} snapshots · select exact source']
        for entry in entries[start:start + count]:
            rows.append(('▶ ' if entry.id == self._selected else '  ') + sanitize_terminal_text(entry.label))
        if not entries and count:
            rows.append('No saved primary snapshots')
        if self._error:
            rows.append(self._error)
        rows.append('Enter reviews restore · p image preview · Esc cancel')
        self._visible = self.focused and bool(entries) and width >= 24 and self._budget >= 3
        return [truncate_to_width(row, max(0, width), '') for row in rows[:self._budget]]
