"""Shared, grapheme-safe prompt editor for terminal applications."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from wcwidth import wcswidth

if TYPE_CHECKING:
    from xdog.tui.autocomplete import CompletionProvider
from xdog.tui.editor_layout import EditorLayout, grapheme_clusters, layout_editor
from xdog.tui.keys import KeyEvent
from xdog.tui.kill_ring import KillRing
from xdog.tui.tui import CURSOR_MARKER, Component
from xdog.tui.undo_stack import UndoStack
from xdog.tui.utils import strip_ansi, truncate_to_width

_MAX_AUTOCOMPLETE_ROWS = 3
_ESCAPE_SEQUENCE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\)|_[^\x07]*(?:\x07|\x1b\\))")


class InputPanelTheme(Protocol):
    """Small styling surface required by :class:`InputPanel`."""

    @property
    def accent(self) -> Callable[[str], str]: ...

    @property
    def bold(self) -> Callable[[str], str]: ...

    @property
    def dim(self) -> Callable[[str], str]: ...

    @property
    def border(self) -> Callable[[str], str]: ...


@dataclass(frozen=True, slots=True)
class _EditorSnapshot:
    value: str
    cursor: int


class SlashSelectList:
    """Select-list used by slash command autocomplete."""

    def __init__(self, items: list[tuple[str, str]], max_visible: int = _MAX_AUTOCOMPLETE_ROWS) -> None:
        self._items = tuple(items)
        self._selected = 0
        self._max_visible = max(0, min(max_visible, _MAX_AUTOCOMPLETE_ROWS))

    @property
    def selected_value(self) -> str | None:
        if 0 <= self._selected < len(self._items):
            return self._items[self._selected][0]
        return None

    def set_items(self, items: list[tuple[str, str]]) -> None:
        self._items = tuple(items)
        self._selected = min(self._selected, max(0, len(items) - 1))

    def set_max_visible(self, max_visible: int) -> None:
        self._max_visible = max(0, min(max_visible, _MAX_AUTOCOMPLETE_ROWS))

    def move(self, delta: int) -> None:
        if self._items:
            self._selected = (self._selected + delta) % len(self._items)

    def render(self, width: int, theme: InputPanelTheme, max_rows: int | None = None) -> list[str]:
        visible = self._max_visible if max_rows is None else min(self._max_visible, max(0, max_rows))
        if not self._items or visible == 0:
            return []
        count = len(self._items)
        start = max(0, min(self._selected - visible // 2, count - visible))
        lines: list[str] = []
        for index in range(start, min(start + visible, count)):
            value, description = self._items[index]
            line = truncate_to_width(f"{'→' if index == self._selected else ' '} {value:<14s} {description}", width)
            lines.append(theme.accent(line) if index == self._selected else theme.dim(line))
        return lines


class InputPanel(Component):
    """Reusable text editor with history, autocomplete, and bounded rendering."""

    def __init__(
        self,
        theme: InputPanelTheme,
        *,
        command_provider: Callable[[], Mapping[str, str]] | None = None,
        max_rows: int = 8,
    ) -> None:
        self._theme = theme
        self.border_color: Callable[[str], str] | None = theme.border
        self._value = ""
        self._cursor = 0
        self._history: tuple[str, ...] = ()
        self._hist_idx = -1
        self._hist_stash = ""
        self._render_width = 80
        self._preferred_column: int | None = None
        self._focused = False
        self._command_provider = command_provider or (lambda: {})
        self._max_rows = max(1, max_rows)
        self._show_borders = True
        self._autocomplete_max_visible = _MAX_AUTOCOMPLETE_ROWS
        self._select_list: SlashSelectList | None = None
        self._autocomplete_visible = True
        self._undo_stack: UndoStack[_EditorSnapshot] = UndoStack()
        self._kill_ring = KillRing()
        self._last_action = ""
        self._last_yank: tuple[int, int] | None = None
        self.on_submit: Callable[[str], None] | None = None
        self.on_change: Callable[[str], None] | None = None
        self.on_escape: Callable[[], None] | None = None
        self.on_ctrl_c: Callable[[], None] | None = None
        self.on_ctrl_d: Callable[[], None] | None = None

    @property
    def focused(self) -> bool:
        return self._focused

    @focused.setter
    def focused(self, focused: bool) -> None:
        self._focused = focused

    def set_focus(self, focused: bool) -> None:
        """Compatibility alias for hosts that predate the focus property."""
        self.focused = focused

    def get_text(self) -> str:
        return self._value

    def get_expanded_text(self) -> str:
        return self._value

    def set_text(self, value: str) -> None:
        value = _sanitize_editor_text(value)
        changed = value != self._value
        if changed:
            self._save_undo()
        self._value = value
        self._cursor = len(value)
        self._preferred_column = None
        self._hist_idx = -1
        self._update_select_list()
        if changed:
            self._changed()

    def insert_text_at_cursor(self, text: str) -> None:
        self._insert(_sanitize_editor_text(text))

    def add_to_history(self, text: str) -> None:
        text = _sanitize_editor_text(text)
        if text and (not self._history or self._history[-1] != text):
            self._history = (*self._history, text)
        self._hist_idx = -1
        self._hist_stash = ""

    def reset_history(self) -> None:
        self._history = ()
        self._hist_idx = -1
        self._hist_stash = ""

    def set_render_budget(self, max_rows: int, show_borders: bool = True) -> None:
        """Set the total number of rows available to this editor."""
        self._max_rows = max(1, max_rows)
        self._show_borders = show_borders

    @property
    def autocomplete_active(self) -> bool:
        return self._select_list is not None

    def set_autocomplete_visible(self, visible: bool) -> None:
        """Temporarily hide suggestions while another surface owns focus."""
        self._autocomplete_visible = visible

    def set_autocomplete_provider(self, provider: CompletionProvider) -> None:
        """Accept the generic editor protocol hook.

        InputPanel owns command-shaped completion; generic completion providers
        are intentionally left to the full Editor component.
        """

    def set_autocomplete_max_visible(self, max_visible: int) -> None:
        self._autocomplete_max_visible = max(0, min(max_visible, _MAX_AUTOCOMPLETE_ROWS))
        if self._select_list is not None:
            self._select_list.set_max_visible(self._autocomplete_max_visible)

    def set_padding_x(self, padding: int) -> None:
        """Retained for the editor protocol; prompt padding is fixed at two cells."""

    def invalidate(self) -> None:
        pass

    def handle_paste(self, text: str) -> bool:
        normalized = _sanitize_editor_text(text.replace("\r\n", "\n").replace("\r", "\n"))
        if normalized:
            self._insert(normalized)
        return True

    def layout(self, width: int) -> EditorLayout:
        self._render_width = width
        return layout_editor(self._value, max(1, width - 2))

    def render(self, width: int) -> list[str]:
        self._render_width = width
        layout = layout_editor(self._value, max(1, width - 2))
        border_rows = 2 if self._show_borders and len(layout.rows) + 2 <= self._max_rows else 0
        selection_budget = min(
            self._autocomplete_max_visible if self._select_list is not None and self._autocomplete_visible else 0,
            max(0, self._max_rows - border_rows - 1),
        )
        content_budget = max(1, self._max_rows - border_rows - selection_budget)
        cursor_row, cursor_column = layout.position(self._cursor)
        first_row = max(0, cursor_row - content_budget + 1)
        last_row = min(len(layout.rows), first_row + content_budget)
        lines: list[str] = []
        if border_rows:
            lines.append(self._theme.border("─" * width))
        for row_index in range(first_row, last_row):
            prefix = self._prefix(row_index, first_row)
            row = layout.rows[row_index]
            row_text = row.text
            if row_index == cursor_row:
                row_text = _with_cursor(row_text, cursor_column, self._focused)
            lines.append(prefix + row_text)
        if border_rows:
            lines.append(self._theme.border("─" * width))
        if self._select_list is not None:
            lines.extend(self._select_list.render(width, self._theme, selection_budget))
        return lines[: self._max_rows]

    def handle_input(self, event: KeyEvent) -> bool:
        if event.key == "enter" and (event.alt or event.shift or event.ctrl):
            self._insert("\n")
            return True
        if event.key == "escape":
            if self._select_list is not None:
                self._select_list = None
            elif self.on_escape:
                self.on_escape()
            return True
        if event.ctrl and event.key == "c" and self.on_ctrl_c:
            self.on_ctrl_c()
            return True
        if event.ctrl and event.key == "d":
            if not self._value and self.on_ctrl_d:
                self.on_ctrl_d()
            return True
        # Ctrl-Z belongs to the application suspend policy. Alt-Z provides undo.
        if event.ctrl and event.key == "z":
            return False
        if event.alt and event.key == "z":
            self._restore_redo() if event.shift else self._restore_undo()
            return True
        if self._handle_select_list(event):
            return True
        if event.key == "enter":
            self._submit()
            return True
        if event.key == "backspace" and self._cursor > 0:
            previous = max(boundary for boundary in self._safe_boundaries() if boundary < self._cursor)
            self._replace(previous, self._cursor, "", cursor=previous)
            return True
        if event.key == "delete" and self._cursor < len(self._value):
            following = min(boundary for boundary in self._safe_boundaries() if boundary > self._cursor)
            self._replace(self._cursor, following, "", cursor=self._cursor)
            return True
        if event.key == "left":
            self._cursor = max((item for item in self._safe_boundaries() if item < self._cursor), default=0)
            self._reset_preferred_column()
            return True
        if event.key == "right":
            self._cursor = min(
                (item for item in self._safe_boundaries() if item > self._cursor), default=len(self._value)
            )
            self._reset_preferred_column()
            return True
        if event.key == "home":
            self._cursor = self._value.rfind("\n", 0, self._cursor) + 1
            self._reset_preferred_column()
            return True
        if event.key == "end":
            line_end = self._value.find("\n", self._cursor)
            self._cursor = len(self._value) if line_end < 0 else line_end
            self._reset_preferred_column()
            return True
        if event.ctrl and event.key == "a":
            self._cursor = 0
            self._reset_preferred_column()
            return True
        if event.ctrl and event.key == "e":
            self._cursor = len(self._value)
            self._reset_preferred_column()
            return True
        if event.key == "up" and self._move_vertical(-1):
            return True
        if event.key == "down" and self._move_vertical(1):
            return True
        if event.ctrl and event.key in {"k", "u"}:
            self._kill(event.key)
            return True
        if event.ctrl and event.key == "y":
            self._yank()
            return True
        if event.alt and event.key == "y":
            self._yank_pop()
            return True
        if event.key == "up" and self._history:
            self._history_up()
            return True
        if event.key == "down" and self._hist_idx >= 0:
            self._history_down()
            return True
        if len(event.key) == 1 and not event.ctrl and not event.alt:
            inserted = _sanitize_editor_text(event.key)
            if inserted and inserted == event.key and "\n" not in inserted:
                self._insert(inserted)
                return True
        return False

    def _prefix(self, row_index: int, first_row: int) -> str:
        if row_index == 0:
            return self._theme.bold(self._theme.accent("> "))
        if row_index == first_row and first_row > 0:
            return self._theme.dim("… ")
        return "  "

    def _handle_select_list(self, event: KeyEvent) -> bool:
        if self._select_list is None:
            return False
        if event.key in {"up", "down"}:
            self._select_list.move(-1 if event.key == "up" else 1)
            return True
        if event.key in {"tab", "enter"}:
            selected = self._select_list.selected_value
            if selected is not None:
                self._replace(0, len(self._value), selected, cursor=len(selected))
                self._select_list = None
                if event.key == "enter":
                    self._submit()
                else:
                    self._update_select_list()
            return True
        return False

    def _safe_boundaries(self) -> tuple[int, ...]:
        return tuple(sorted({0, len(self._value), *(end for _start, end, _cluster in grapheme_clusters(self._value))}))

    def _move_vertical(self, direction: int) -> bool:
        layout = layout_editor(self._value, max(1, self._render_width - 2))
        row_index, column = layout.position(self._cursor)
        target_index = row_index + direction
        if target_index < 0 or target_index >= len(layout.rows):
            self._preferred_column = None
            return False
        if self._preferred_column is None:
            self._preferred_column = column
        self._cursor = layout.rows[target_index].offset_for_column(self._preferred_column)
        return True

    def _kill(self, key: str) -> None:
        if key == "k":
            end = self._value.find("\n", self._cursor)
            end = len(self._value) if end < 0 else end
            start = self._cursor
        else:
            start = self._value.rfind("\n", 0, self._cursor) + 1
            end = self._cursor
        killed = self._value[start:end]
        if killed:
            if self._last_action == "kill":
                self._kill_ring.append_kill(killed)
            else:
                self._kill_ring.kill(killed)
            self._replace(start, end, "", cursor=start)
            self._last_action = "kill"

    def _yank(self) -> None:
        text = self._kill_ring.yank()
        if text is not None:
            start = self._cursor
            self._insert(text)
            self._last_yank = (start, self._cursor)
            self._last_action = "yank"

    def _yank_pop(self) -> None:
        if self._last_action != "yank" or self._last_yank is None:
            return
        text = self._kill_ring.yank_pop()
        if text is None:
            return
        start, end = self._last_yank
        self._replace(start, end, text, cursor=start + len(text))
        self._last_yank = (start, start + len(text))
        self._last_action = "yank"

    def _history_up(self) -> None:
        if self._hist_idx == -1:
            self._hist_stash = self._value
            self._hist_idx = len(self._history) - 1
        elif self._hist_idx > 0:
            self._hist_idx -= 1
        else:
            return
        self._set_history_value(self._history[self._hist_idx])

    def _history_down(self) -> None:
        if self._hist_idx < len(self._history) - 1:
            self._hist_idx += 1
            value = self._history[self._hist_idx]
        else:
            self._hist_idx = -1
            value = self._hist_stash
        self._set_history_value(value)

    def _set_history_value(self, value: str) -> None:
        self._value = value
        self._cursor = len(value)
        self._reset_preferred_column()
        self._update_select_list()
        self._changed()

    def _insert(self, text: str) -> None:
        if text:
            self._replace(self._cursor, self._cursor, text, cursor=self._cursor + len(text))

    def _replace(self, start: int, end: int, text: str, *, cursor: int) -> None:
        self._save_undo()
        self._value = self._value[:start] + text + self._value[end:]
        self._cursor = cursor
        self._reset_preferred_column()
        self._update_select_list()
        self._changed()
        if self._last_action not in {"kill", "yank"}:
            self._last_yank = None

    def _save_undo(self) -> None:
        self._undo_stack.push(_EditorSnapshot(self._value, self._cursor))

    def _restore_undo(self) -> None:
        state = self._undo_stack.undo(_EditorSnapshot(self._value, self._cursor))
        if state is not None:
            self._restore(state)

    def _restore_redo(self) -> None:
        state = self._undo_stack.redo(_EditorSnapshot(self._value, self._cursor))
        if state is not None:
            self._restore(state)

    def _restore(self, state: _EditorSnapshot) -> None:
        self._value = state.value
        self._cursor = state.cursor
        self._reset_preferred_column()
        self._update_select_list()
        self._changed()

    def _reset_preferred_column(self) -> None:
        self._preferred_column = None
        self._last_action = ""

    def _changed(self) -> None:
        if self.on_change:
            self.on_change(self._value)

    def _update_select_list(self) -> None:
        if not self._value.startswith("/") or any(char.isspace() for char in self._value):
            self._select_list = None
            return
        prefix = self._value[1:]
        normalized = {
            f"/{name.lstrip('/')}": description for name, description in self._command_provider().items()
        }
        matching = [
            (name, description)
            for name, description in sorted(normalized.items())
            if name[1:].startswith(prefix) and name != self._value
        ]
        if not matching:
            self._select_list = None
        elif self._select_list is None:
            self._select_list = SlashSelectList(matching, self._autocomplete_max_visible)
        else:
            self._select_list.set_items(matching)

    def _submit(self) -> None:
        value = self._value.strip()
        if not value:
            return
        self._value = ""
        self._cursor = 0
        self._hist_idx = -1
        self._hist_stash = ""
        self._select_list = None
        self._reset_preferred_column()
        self._undo_stack.clear()
        self._changed()
        if self.on_submit:
            self.on_submit(value)


def _sanitize_editor_text(text: str) -> str:
    without_escapes = strip_ansi(_ESCAPE_SEQUENCE.sub("", text))
    return "".join(
        char
        for char in without_escapes
        if char in {"\n", "\t"} or unicodedata.category(char) != "Cc"
    )


def _with_cursor(text: str, column: int, focused: bool) -> str:
    """Format a whole rendered grapheme cluster at a display-column boundary."""
    current = 0
    for start, end, cluster in grapheme_clusters(text):
        if current >= column:
            marker = CURSOR_MARKER if focused else ""
            return text[:start] + marker + f"\x1b[7m{cluster}\x1b[27m" + text[end:]
        current += max(0, wcswidth(cluster))
    marker = CURSOR_MARKER if focused else ""
    return text + " " * max(0, column - current) + marker + "\x1b[7m \x1b[27m"


__all__ = ["InputPanel", "InputPanelTheme", "SlashSelectList"]
