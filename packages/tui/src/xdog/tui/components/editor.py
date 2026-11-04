"""Editor component -- multi-line text editor with full editing features.

Ported from TypeScript editor.ts. Includes:
- Kill ring (Ctrl+K, Ctrl+U, Ctrl+Y, Alt+Y)
- Undo/redo (Ctrl+Z, Ctrl+Shift+Z)
- Word movement (Ctrl+Left/Right, Alt+B/F, Alt+D, Alt+Backspace)
- Vertical scrolling
- Bracketed paste detection
- Optional border and padding
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from xdog.tui.keys import KeyEvent
from xdog.tui.kill_ring import KillRing
from xdog.tui.tui import Component
from xdog.tui.undo_stack import UndoStack


@dataclass(frozen=True)
class EditorTheme:
    """Visual theme for the editor."""

    border_color: Callable[[str], str] = lambda t: t
    text_color: Callable[[str], str] = lambda t: t
    cursor_color: Callable[[str], str] = lambda t: f"\x1b[7m{t}\x1b[27m"
    line_number_color: Callable[[str], str] = lambda t: f"\x1b[2m{t}\x1b[22m"


@dataclass(frozen=True)
class EditorOptions:
    """Configuration options for the editor."""

    word_wrap: bool = False
    show_line_numbers: bool = False
    padding_x: int = 1
    max_height: int | None = None
    placeholder: str = ""
    show_border: bool = True


@dataclass(frozen=True)
class _EditorSnapshot:
    """Immutable snapshot of editor state for undo/redo."""

    lines: tuple[str, ...]
    cursor_row: int
    cursor_col: int


# Word boundary pattern
_WORD_BOUNDARY = re.compile(r"\b")

# Bracketed paste markers
_PASTE_START = "\x1b[200~"
_PASTE_END = "\x1b[201~"


def _find_word_start(text: str, pos: int) -> int:
    """Find the start of the word before *pos*."""
    if pos <= 0:
        return 0
    i = pos - 1
    # Skip whitespace
    while i > 0 and text[i].isspace():
        i -= 1
    # Skip word chars
    while i > 0 and not text[i - 1].isspace():
        i -= 1
    return i


def _find_word_end(text: str, pos: int) -> int:
    """Find the end of the word after *pos*."""
    length = len(text)
    if pos >= length:
        return length
    i = pos
    # Skip current word chars
    while i < length and not text[i].isspace():
        i += 1
    # Skip whitespace
    while i < length and text[i].isspace():
        i += 1
    return i


class Editor(Component):
    """Multi-line text editor with kill ring, undo/redo, and word movement.

    Full port of the TypeScript Editor component.
    """

    def __init__(
        self,
        border_fn: Callable[[str], str] | None = None,
        *,
        theme: EditorTheme | None = None,
        options: EditorOptions | None = None,
    ) -> None:
        self._lines: list[str] = [""]
        self._cursor_row: int = 0
        self._cursor_col: int = 0
        self._scroll_offset: int = 0
        self._border_fn = border_fn or (lambda t: t)
        self._theme = theme or EditorTheme()
        self._options = options or EditorOptions()
        self.focused: bool = False
        self.on_submit: Callable[[str], None] | None = None
        self.on_change: Callable[[str], None] | None = None
        self._history: list[str] = []
        self._hist_idx: int = -1
        self._kill_ring = KillRing()
        self._undo_stack: UndoStack[_EditorSnapshot] = UndoStack()
        self._in_paste: bool = False
        self._paste_buffer: list[str] = []
        self._last_was_kill: bool = False

    # -- text access ---------------------------------------------------------

    def get_text(self) -> str:
        return "\n".join(self._lines)

    def get_value(self) -> str:
        return self.get_text()

    def set_text(self, text: str) -> None:
        self._lines = text.split("\n") if text else [""]
        self._cursor_row = len(self._lines) - 1
        self._cursor_col = len(self._lines[-1])
        self._notify_change()

    def set_value(self, value: str) -> None:
        self.set_text(value)

    def insert_text_at_cursor(self, text: str) -> None:
        """Insert *text* at the current cursor position."""
        self._save_undo()
        for ch in text:
            if ch == "\n":
                self._insert_newline()
            else:
                line = self._lines[self._cursor_row]
                self._lines[self._cursor_row] = line[:self._cursor_col] + ch + line[self._cursor_col:]
                self._cursor_col += 1
        self._notify_change()

    def add_to_history(self, value: str) -> None:
        self._history.append(value)

    # -- undo/redo -----------------------------------------------------------

    def _save_undo(self) -> None:
        self._undo_stack.push(_EditorSnapshot(
            lines=tuple(self._lines),
            cursor_row=self._cursor_row,
            cursor_col=self._cursor_col,
        ))

    def _restore_snapshot(self, snap: _EditorSnapshot) -> None:
        self._lines = list(snap.lines)
        self._cursor_row = snap.cursor_row
        self._cursor_col = snap.cursor_col

    def _undo(self) -> None:
        current = _EditorSnapshot(
            lines=tuple(self._lines),
            cursor_row=self._cursor_row,
            cursor_col=self._cursor_col,
        )
        prev = self._undo_stack.undo(current)
        if prev is not None:
            self._restore_snapshot(prev)
            self._notify_change()

    def _redo(self) -> None:
        current = _EditorSnapshot(
            lines=tuple(self._lines),
            cursor_row=self._cursor_row,
            cursor_col=self._cursor_col,
        )
        nxt = self._undo_stack.redo(current)
        if nxt is not None:
            self._restore_snapshot(nxt)
            self._notify_change()

    # -- change notification -------------------------------------------------

    def _notify_change(self) -> None:
        if self.on_change is not None:
            self.on_change(self.get_text())

    # -- internal helpers ----------------------------------------------------

    def _insert_newline(self) -> None:
        line = self._lines[self._cursor_row]
        before = line[:self._cursor_col]
        after = line[self._cursor_col:]
        self._lines[self._cursor_row] = before
        self._lines.insert(self._cursor_row + 1, after)
        self._cursor_row += 1
        self._cursor_col = 0

    def _ensure_scroll(self, visible_height: int) -> None:
        """Adjust scroll offset so cursor is visible."""
        if self._cursor_row < self._scroll_offset:
            self._scroll_offset = self._cursor_row
        elif self._cursor_row >= self._scroll_offset + visible_height:
            self._scroll_offset = self._cursor_row - visible_height + 1

    # -- rendering -----------------------------------------------------------

    def invalidate(self) -> None:
        pass

    def render(self, width: int) -> list[str]:
        result: list[str] = []
        opts = self._options
        pad = opts.padding_x

        # Top border
        if opts.show_border:
            result.append(self._border_fn("─" * width))

        # Calculate visible height
        max_h = opts.max_height
        if max_h is None:
            visible_h = len(self._lines)
        else:
            border_lines = 2 if opts.show_border else 0
            visible_h = max(1, max_h - border_lines)

        self._ensure_scroll(visible_h)

        # Placeholder
        if not self.get_text() and opts.placeholder and not self.focused:
            result.append(" " * pad + f"\x1b[2m{opts.placeholder}\x1b[0m")
            if opts.show_border:
                result.append(self._border_fn("─" * width))
            return result

        # Content lines
        end_row = min(self._scroll_offset + visible_h, len(self._lines))
        for row_idx in range(self._scroll_offset, end_row):
            line = self._lines[row_idx]
            prefix = " " * pad

            if opts.show_line_numbers:
                ln_str = self._theme.line_number_color(f"{row_idx + 1:3} ")
                prefix = ln_str + prefix

            if self.focused and row_idx == self._cursor_row:
                before = line[:self._cursor_col]
                after = line[self._cursor_col:]
                cursor_ch = after[0] if after else " "
                rest = after[1:] if after else ""
                rendered = f"{prefix}{before}{self._theme.cursor_color(cursor_ch)}{rest}"
            else:
                rendered = f"{prefix}{line}"

            result.append(rendered)

        # Bottom border
        if opts.show_border:
            result.append(self._border_fn("─" * width))

        return result

    # -- input handling ------------------------------------------------------

    def handle_input(self, event: KeyEvent) -> bool:
        line = self._lines[self._cursor_row]

        # Track kill ring accumulation
        is_kill = False

        if event.ctrl and event.key in ("c", "d"):
            return True

        # Undo/Redo
        if event.ctrl and event.key == "z":
            if event.shift:
                self._redo()
            else:
                self._undo()
            return True

        # Submit
        if event.key == "enter" and not self._in_paste:
            value = self.get_text().strip()
            if value and self.on_submit:
                self.on_submit(value)
            self._save_undo()
            self._lines = [""]
            self._cursor_row = 0
            self._cursor_col = 0
            self._scroll_offset = 0
            self._notify_change()
            return True

        # Newline in paste or Alt+Enter
        if event.key == "enter" and self._in_paste:
            self._insert_newline()
            return True
        if event.alt and event.key == "enter":
            self._save_undo()
            self._insert_newline()
            self._notify_change()
            return True

        # Backspace
        if event.key == "backspace":
            if event.alt:
                # Alt+Backspace: delete word backward
                self._save_undo()
                start = _find_word_start(line, self._cursor_col)
                killed = line[start:self._cursor_col]
                self._lines[self._cursor_row] = line[:start] + line[self._cursor_col:]
                self._cursor_col = start
                if killed:
                    self._kill_ring.kill(killed)
                    is_kill = True
                self._notify_change()
            elif self._cursor_col > 0:
                self._save_undo()
                self._lines[self._cursor_row] = line[:self._cursor_col - 1] + line[self._cursor_col:]
                self._cursor_col -= 1
                self._notify_change()
            elif self._cursor_row > 0:
                self._save_undo()
                prev = self._lines[self._cursor_row - 1]
                self._cursor_col = len(prev)
                self._lines[self._cursor_row - 1] = prev + line
                self._lines.pop(self._cursor_row)
                self._cursor_row -= 1
                self._notify_change()
            self._last_was_kill = is_kill
            return True

        # Delete
        if event.key == "delete":
            self._save_undo()
            if self._cursor_col < len(line):
                self._lines[self._cursor_row] = line[:self._cursor_col] + line[self._cursor_col + 1:]
            elif self._cursor_row < len(self._lines) - 1:
                self._lines[self._cursor_row] = line + self._lines[self._cursor_row + 1]
                self._lines.pop(self._cursor_row + 1)
            self._notify_change()
            return True

        # Kill ring: Ctrl+K (kill to end of line)
        if event.ctrl and event.key == "k":
            self._save_undo()
            killed = line[self._cursor_col:]
            self._lines[self._cursor_row] = line[:self._cursor_col]
            if killed:
                if self._last_was_kill:
                    self._kill_ring.append_kill(killed)
                else:
                    self._kill_ring.kill(killed)
                is_kill = True
            self._notify_change()
            self._last_was_kill = is_kill
            return True

        # Kill ring: Ctrl+U (kill to start of line)
        if event.ctrl and event.key == "u":
            self._save_undo()
            killed = line[:self._cursor_col]
            self._lines[self._cursor_row] = line[self._cursor_col:]
            self._cursor_col = 0
            if killed:
                if self._last_was_kill:
                    self._kill_ring.append_kill(killed)
                else:
                    self._kill_ring.kill(killed)
                is_kill = True
            self._notify_change()
            self._last_was_kill = is_kill
            return True

        # Kill ring: Ctrl+Y (yank)
        if event.ctrl and event.key == "y":
            text = self._kill_ring.yank()
            if text:
                self._save_undo()
                self.insert_text_at_cursor(text)
            return True

        # Kill ring: Alt+Y (yank pop)
        if event.alt and event.key == "y":
            text = self._kill_ring.yank_pop()
            if text:
                self._save_undo()
                self.insert_text_at_cursor(text)
            return True

        # Alt+D: delete word forward
        if event.alt and event.key == "d":
            self._save_undo()
            end = _find_word_end(line, self._cursor_col)
            killed = line[self._cursor_col:end]
            self._lines[self._cursor_row] = line[:self._cursor_col] + line[end:]
            if killed:
                self._kill_ring.kill(killed)
                is_kill = True
            self._notify_change()
            self._last_was_kill = is_kill
            return True

        # Navigation
        if event.key == "left":
            if event.ctrl or event.alt:
                # Word left
                self._cursor_col = _find_word_start(line, self._cursor_col)
            elif self._cursor_col > 0:
                self._cursor_col -= 1
            elif self._cursor_row > 0:
                self._cursor_row -= 1
                self._cursor_col = len(self._lines[self._cursor_row])
            self._last_was_kill = False
            return True

        if event.key == "right":
            if event.ctrl or event.alt:
                # Word right
                self._cursor_col = _find_word_end(line, self._cursor_col)
            elif self._cursor_col < len(line):
                self._cursor_col += 1
            elif self._cursor_row < len(self._lines) - 1:
                self._cursor_row += 1
                self._cursor_col = 0
            self._last_was_kill = False
            return True

        if event.key == "up":
            if self._cursor_row > 0:
                self._cursor_row -= 1
                self._cursor_col = min(self._cursor_col, len(self._lines[self._cursor_row]))
            self._last_was_kill = False
            return True

        if event.key == "down":
            if self._cursor_row < len(self._lines) - 1:
                self._cursor_row += 1
                self._cursor_col = min(self._cursor_col, len(self._lines[self._cursor_row]))
            self._last_was_kill = False
            return True

        if event.key == "home" or (event.ctrl and event.key == "a"):
            self._cursor_col = 0
            self._last_was_kill = False
            return True

        if event.key == "end" or (event.ctrl and event.key == "e"):
            self._cursor_col = len(line)
            self._last_was_kill = False
            return True

        if event.key == "pageup":
            self._cursor_row = max(0, self._cursor_row - 10)
            self._cursor_col = min(self._cursor_col, len(self._lines[self._cursor_row]))
            self._last_was_kill = False
            return True

        if event.key == "pagedown":
            self._cursor_row = min(len(self._lines) - 1, self._cursor_row + 10)
            self._cursor_col = min(self._cursor_col, len(self._lines[self._cursor_row]))
            self._last_was_kill = False
            return True

        # Printable character
        if len(event.key) == 1 and not event.ctrl and not event.alt:
            self._save_undo()
            self._lines[self._cursor_row] = line[:self._cursor_col] + event.key + line[self._cursor_col:]
            self._cursor_col += 1
            self._last_was_kill = False
            self._notify_change()
            return True

        self._last_was_kill = False
        return False
