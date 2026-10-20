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
from xdog.tui.argument_completion import ArgumentCompletionFeed
from xdog.tui.autocomplete import CompletionStatusProvider
from xdog.tui.editor_layout import EditorLayout, grapheme_clusters, layout_editor
from xdog.tui.keys import KeyEvent, KeyEventType
from xdog.tui.kill_ring import KillRing
from xdog.tui.prompt_keymap import EDITOR_EVENTS, PromptKeymap
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

    def __init__(self, items: list[tuple[str, str]], max_visible: int = _MAX_AUTOCOMPLETE_ROWS, *,
                 labels: Mapping[str, str] | None = None) -> None:
        self._items = tuple(items)
        self._labels = dict(labels or {})
        self._selected = 0
        self._max_visible = max(0, min(max_visible, _MAX_AUTOCOMPLETE_ROWS))

    @property
    def selected_value(self) -> str | None:
        if 0 <= self._selected < len(self._items):
            return self._items[self._selected][0]
        return None

    def set_items(self, items: list[tuple[str, str]], *, labels: Mapping[str, str] | None = None) -> None:
        selected = self.selected_value
        self._labels = dict(labels or {})
        self._items = tuple(items)
        self._selected = next(
            (index for index, (value, _detail) in enumerate(items) if value == selected),
            min(self._selected, max(0, len(items) - 1)),
        )

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
            display = self._labels.get(value) or value
            if display != value:
                display += ' [' + value + ']'  # Label is presentation only; insertion identity stays visible.
            line = truncate_to_width(f"{'→' if index == self._selected else ' '} {display:<14s} {description}", width)
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
        self._keymap = PromptKeymap()
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
        self._completion_provider: CompletionProvider | None = None
        self._argument_provider: ArgumentCompletionFeed | None = None
        self._argument_active = False
        self._completion_span: tuple[int, int, str] | None = None
        self._completion_items: tuple[tuple[str, str], ...] = ()
        self._completion_labels: tuple[tuple[str, str], ...] = ()
        self._completion_status = ""
        self._completion_context: tuple[str, int] | None = None
        self._cached_span: tuple[int, int, str] | None = None
        self._dismissed_completion: tuple[str, int] | None = None
        self._max_rows = max(1, max_rows)
        self._show_borders = True
        self._autocomplete_max_visible = _MAX_AUTOCOMPLETE_ROWS
        self._select_list: SlashSelectList | None = None
        self._exact_command_context: tuple[str, int] | None = None
        self._slash_selection_context: tuple[str, int, tuple[tuple[str, str], ...]] | None = None
        self._autocomplete_visible = True
        self._undo_stack: UndoStack[_EditorSnapshot] = UndoStack()
        self._kill_ring = KillRing()
        self._last_action = ""
        self._last_yank: tuple[int, int] | None = None
        self.on_submit: Callable[[str], None] | None = None
        self.allow_empty_submit = False
        self._attachment_summary = ""
        self.on_change: Callable[[str], None] | None = None
        self.on_escape: Callable[[], None] | None = None
        self.on_ctrl_c: Callable[[], None] | None = None
        self.on_ctrl_d: Callable[[], None] | None = None

    @property
    def focused(self) -> bool:
        return self._focused

    @focused.setter
    def focused(self, focused: bool) -> None:
        if self._focused and not focused and self._argument_provider is not None:
            self._argument_provider.invalidate()
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
        if changed:
            self._exact_command_context = None
            self._slash_selection_context = None
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

    def set_panel_theme(self, theme: InputPanelTheme) -> None:
        """Reuse host styles in a nested form without replacing editor state."""
        self._theme = theme
        self.border_color = theme.border

    def set_render_budget(self, max_rows: int, show_borders: bool = True) -> None:
        """Set the total number of rows available to this editor."""
        self._max_rows = max(1, max_rows)
        self._show_borders = show_borders

    @property
    def autocomplete_active(self) -> bool:
        return self._select_list is not None or (
            self._completion_span is not None and self._dismissed_completion != (self._value, self._cursor)
        )

    def set_autocomplete_visible(self, visible: bool) -> None:
        """Temporarily hide suggestions while another surface owns focus."""
        if self._autocomplete_visible and not visible and self._argument_provider is not None:
            self._argument_provider.invalidate()
        self._autocomplete_visible = visible

    def set_attachment_summary(self, summary: str) -> None:
        self._attachment_summary = _sanitize_editor_text(summary).replace("\n", " ")
        self.allow_empty_submit = bool(summary)

    def set_keymap(self, keymap: PromptKeymap) -> None:
        """Configure prompt actions without changing focused dialog permissions."""
        self._keymap = keymap

    def set_autocomplete_provider(self, provider: CompletionProvider | None) -> None:
        """Complete path/reference tokens using a non-blocking host provider."""
        self._completion_provider = provider
        self._select_list = None
        self._completion_items = ()
        self._dismissed_completion = None
        self._update_select_list()

    def set_argument_completion_provider(self, provider: ArgumentCompletionFeed) -> None:
        self._argument_provider = provider
        self._update_select_list()

    def poll_autocomplete(self) -> bool:
        """Consume worker results on the UI thread; unchanged polls do not repaint."""
        previous = (
            self._completion_span, self._completion_items, self._completion_labels,
            self._completion_status, self._select_list is not None,
        )
        self._update_select_list()
        return previous != (
            self._completion_span, self._completion_items, self._completion_labels,
            self._completion_status, self._select_list is not None,
        )

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
        show_status = bool(self._completion_status and self.autocomplete_active and self._autocomplete_visible)
        selection_budget = min(
            self._autocomplete_max_visible if (
                (self._select_list is not None or show_status) and self._autocomplete_visible
            ) else 0,
            max(0, self._max_rows - border_rows - 1),
        )
        attachment_budget = int(bool(self._attachment_summary) and self._max_rows - border_rows - selection_budget > 1)
        content_budget = max(1, self._max_rows - border_rows - selection_budget - attachment_budget)
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
        if attachment_budget:
            lines.append(self._theme.dim(truncate_to_width(self._attachment_summary, width)))
        if self._select_list is not None:
            lines.extend(self._select_list.render(width, self._theme, max(0, selection_budget - int(show_status))))
        if show_status and selection_budget:
            lines.append(self._theme.dim(truncate_to_width(self._completion_status, width)))
        return lines[: self._max_rows]

    def handle_input(self, event: KeyEvent) -> bool:
        if event.event_type == KeyEventType.RELEASE:
            return True
        action = self._keymap.resolve(event)
        canonical = EDITOR_EVENTS.get(action or "")
        if canonical is not None:
            if event.event_type == KeyEventType.REPEAT and action in {"send", "interrupt", "exit"}:
                return True
            return self._handle_editor_input(canonical)
        if action is not None or len(event.key) != 1 or event.ctrl or event.alt:
            return False
        return self._handle_editor_input(event)

    def _handle_editor_input(self, event: KeyEvent) -> bool:
        if event.key == "enter" and (event.alt or event.shift or event.ctrl):
            self._insert("\n")
            return True
        if event.key == "escape":
            if self.autocomplete_active:
                if self._argument_provider is not None and self._argument_active:
                    self._argument_provider.dismiss(self._value, self._cursor)
                self._select_list = None
                self._dismissed_completion = (self._value, self._cursor)
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
        if (event.key == "tab" and self._select_list is None
                and (self._completion_provider is not None or self._argument_provider is not None)):
            self._dismissed_completion = None
            if self._argument_provider is not None:
                self._argument_provider.reopen()
            self._update_select_list()
            return True
        if (self._argument_active and self._select_list is None and event.key == "enter"
                and self._completion_status.startswith("Reading")):
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
            if self._completion_span is None:
                self._slash_selection_context = (self._value, self._cursor, self._completion_items)
            return True
        if event.key in {"tab", "enter"}:
            selected = self._select_list.selected_value
            if self._completion_span is None:
                context = self._value, self._cursor
                explicit = self._slash_selection_context == (*context, self._completion_items)
                normalized = {f"/{name.lstrip('/')}".casefold() for name in self._command_provider()}
                exact = self._value.casefold() in normalized or self._exact_command_context == context
                if event.key == 'enter' and exact and not explicit:
                    # Typing a complete registered command is explicit intent,
                    # not permission to replace it with a longer suggestion.
                    self._select_list = None
                    self._submit()
                    return True
                if selected is not None and selected.casefold() not in normalized:
                    # Dynamic withdrawal cannot submit a stale suggestion or
                    # silently reinterpret it as another newly registered action.
                    self._slash_selection_context = None
                    self._update_select_list()
                    return True
            if selected is not None and self._completion_span is not None:
                if self._argument_active and (self._argument_provider is None
                        or not self._argument_provider.valid(self._value, self._cursor)):
                    self._update_select_list()
                    return True
                start, end, _prefix = self._completion_span
                if self._argument_active and self._argument_provider is not None:
                    self._argument_provider.dismiss(self._value[:start] + selected + self._value[end:],
                                                    start + len(selected))
                self._replace(start, end, selected, cursor=start + len(selected))
                self._select_list = None
                if selected.endswith(("/", '/"')):
                    self._update_select_list()
                else:
                    self._dismissed_completion = (self._value, self._cursor)
                return True  # Choosing a file never submits the conversation.
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
        self._update_select_list()
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
        self._exact_command_context = None
        self._slash_selection_context = None
        self._update_select_list()

    def _changed(self) -> None:
        if self.on_change:
            self.on_change(self._value)

    def _update_select_list(self) -> None:
        if self._dismissed_completion == (self._value, self._cursor):
            return
        self._dismissed_completion = None
        previous_span = self._completion_span
        self._completion_span = None
        self._completion_status = ""
        self._argument_active = False
        is_command = False
        matching: list[tuple[str, str]] = []
        labels: dict[str, str] = {}
        if self._value.startswith("/") and not any(char.isspace() for char in self._value):
            prefix = self._value[1:]
            normalized = {
                f"/{name.lstrip('/')}": description for name, description in self._command_provider().items()
            }
            is_command = self._value.casefold() in {name.casefold() for name in normalized}
            if is_command:
                self._exact_command_context = (self._value, self._cursor)
            matching = [
                (name, description) for name, description in sorted(normalized.items())
                if name[1:].casefold().startswith(prefix.casefold()) and name.casefold() != self._value.casefold()
            ]
        if not matching and not is_command and self._argument_provider is not None:
            arguments = self._argument_provider.lookup(self._value, self._cursor)
            if arguments is not None:
                self._completion_span, rows, self._completion_status = arguments
                self._argument_active = True
                labels = {item.text: item.label for item in rows if item.label}
                matching = [(item.text, item.detail) for item in rows if item.text !=
                            self._value[self._completion_span[0]:self._completion_span[1]]]
        if (not matching and not is_command and not self._argument_active
                and self._completion_provider is not None):
            context = (self._value, self._cursor)
            if context != self._completion_context:
                self._completion_context = context
                self._cached_span = _path_completion_span(self._value, self._cursor)
            span = self._cached_span
            if span is not None:
                self._completion_span = span
                matching = [
                    (item.text, item.detail) for item in self._completion_provider.get_completions(span[2])
                    if item.text != self._value[span[0]:span[1]]
                    and item.text == _sanitize_editor_text(item.text) and "\n" not in item.text
                ]
                if isinstance(self._completion_provider, CompletionStatusProvider):
                    self._completion_status = _sanitize_editor_text(
                        self._completion_provider.get_completion_status(span[2])
                    ).replace("\n", " ")
        items = tuple(matching)
        presentation = tuple(sorted(labels.items()))
        if items != self._completion_items:
            self._slash_selection_context = None
        if not matching:
            self._select_list = None
        elif self._select_list is None or previous_span != self._completion_span:
            self._select_list = SlashSelectList(matching, self._autocomplete_max_visible, labels=labels)
        elif items != self._completion_items or presentation != self._completion_labels:
            self._select_list.set_items(matching, labels=labels)
        self._completion_items = items
        self._completion_labels = presentation

    def _submit(self) -> None:
        value = self._value.strip()
        if not value and not self.allow_empty_submit:
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


def _path_completion_span(value: str, cursor: int) -> tuple[int, int, str] | None:
    """Locate a whitespace-delimited reference/path, including its right suffix."""
    start = value.rfind("\n", 0, cursor) + 1
    line_end = value.find("\n", cursor)
    limit = len(value) if line_end < 0 else line_end
    while start < limit and start < cursor:
        if value[start].isspace():
            start += 1
            continue
        end = start
        quoted = False
        escaped = False
        while end < limit:
            char = value[end]
            if char.isspace() and not quoted:
                break
            if char == '"' and not escaped:
                quoted = not quoted
            escaped = char == "\\" and not escaped
            end += 1
        token = value[start:end]
        if start < cursor <= end and (
            token.startswith(("@", "./", "../", "~/", "/", '"./', '"../', '"~/', '"/'))
        ):
            return start, end, value[start:cursor]
        start = end
    return None


def _sanitize_editor_text(text: str) -> str:
    without_escapes = strip_ansi(_ESCAPE_SEQUENCE.sub("", text))
    return "".join(
        char
        for char in without_escapes
        if char in {"\n", "\t"} or unicodedata.category(char) != "Cc"
    )


def _with_cursor(text: str, column: int, focused: bool) -> str:
    """Mark a whole grapheme boundary for the renderer's hardware cursor.

    Do not paint a second reverse-video cursor, especially in an unfocused
    editor behind a permission/details panel. The terminal owns cursor style.
    """
    if not focused:
        return text
    current = 0
    for start, _end, cluster in grapheme_clusters(text):
        if current >= column:
            return text[:start] + CURSOR_MARKER + text[start:]
        current += max(0, wcswidth(cluster))
    return text + " " * max(0, column - current) + CURSOR_MARKER


__all__ = ["InputPanel", "InputPanelTheme", "SlashSelectList"]
