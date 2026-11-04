"""Input component -- single-line text input with full editing features.

Ported from TypeScript input.ts. Includes:
- Kill ring (Ctrl+K, Ctrl+U, Ctrl+Y, Alt+Y)
- Undo/redo (Ctrl+Z, Ctrl+Shift+Z)
- Word movement (Ctrl+Left/Right, Alt+B/F, Alt+D, Alt+Backspace)
- History navigation (Up/Down)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from xdog.tui.keys import KeyEvent
from xdog.tui.kill_ring import KillRing
from xdog.tui.tui import Component
from xdog.tui.undo_stack import UndoStack


@dataclass(frozen=True)
class _InputSnapshot:
    """Undo snapshot for input state."""

    value: str
    cursor: int


def _find_word_start(text: str, pos: int) -> int:
    """Find the start of the word before *pos*."""
    if pos <= 0:
        return 0
    i = pos - 1
    while i > 0 and text[i].isspace():
        i -= 1
    while i > 0 and not text[i - 1].isspace():
        i -= 1
    return i


def _find_word_end(text: str, pos: int) -> int:
    """Find the end of the word after *pos*."""
    length = len(text)
    if pos >= length:
        return length
    i = pos
    while i < length and not text[i].isspace():
        i += 1
    while i < length and text[i].isspace():
        i += 1
    return i


class Input(Component):
    """Single-line text input with kill ring, undo/redo, and word movement."""

    def __init__(self, prompt: str = "", placeholder: str = "") -> None:
        self._value: str = ""
        self._cursor: int = 0
        self._prompt = prompt
        self._placeholder = placeholder
        self.focused: bool = False
        self.on_submit: Callable[[str], None] | None = None
        self.on_escape: Callable[[], None] | None = None
        self.on_change: Callable[[str], None] | None = None
        self._history: list[str] = []
        self._hist_idx: int = -1
        self._hist_stash: str = ""
        self._kill_ring = KillRing()
        self._undo_stack: UndoStack[_InputSnapshot] = UndoStack()
        self._last_was_kill: bool = False

    def get_value(self) -> str:
        return self._value

    def set_value(self, value: str) -> None:
        self._value = value
        self._cursor = min(self._cursor, len(value))

    def invalidate(self) -> None:
        pass

    def _save_undo(self) -> None:
        self._undo_stack.push(_InputSnapshot(value=self._value, cursor=self._cursor))

    def _notify_change(self) -> None:
        if self.on_change is not None:
            self.on_change(self._value)

    def render(self, width: int) -> list[str]:
        prompt = self._prompt

        if not self._value and self._placeholder and not self.focused:
            line = prompt + f"\x1b[2m{self._placeholder}\x1b[0m"
            return [line]

        # Show value with cursor
        before = self._value[:self._cursor]
        after = self._value[self._cursor:]
        cursor_ch = after[0] if after else " "
        rest = after[1:] if after else ""

        if self.focused:
            line = prompt + before + f"\x1b[7m{cursor_ch}\x1b[27m" + rest
        else:
            line = prompt + self._value

        return [line]

    def handle_input(self, event: KeyEvent) -> bool:
        is_kill = False

        if event.ctrl and event.key in ("c", "d"):
            if self.on_escape:
                self.on_escape()
            return True

        # Undo/Redo
        if event.ctrl and event.key == "z":
            current = _InputSnapshot(value=self._value, cursor=self._cursor)
            if event.shift:
                snap = self._undo_stack.redo(current)
            else:
                snap = self._undo_stack.undo(current)
            if snap is not None:
                self._value = snap.value
                self._cursor = snap.cursor
                self._notify_change()
            return True

        if event.key == "enter":
            if self.on_submit:
                self.on_submit(self._value)
            return True

        if event.key == "escape":
            if self.on_escape:
                self.on_escape()
            return True

        # Backspace
        if event.key == "backspace":
            if event.alt:
                # Alt+Backspace: delete word backward
                start = _find_word_start(self._value, self._cursor)
                killed = self._value[start:self._cursor]
                if killed:
                    self._save_undo()
                    self._value = self._value[:start] + self._value[self._cursor:]
                    self._cursor = start
                    self._kill_ring.kill(killed)
                    is_kill = True
                    self._notify_change()
            elif self._cursor > 0:
                self._save_undo()
                self._value = self._value[:self._cursor - 1] + self._value[self._cursor:]
                self._cursor -= 1
                self._notify_change()
            self._last_was_kill = is_kill
            return True

        if event.key == "delete" and self._cursor < len(self._value):
            self._save_undo()
            self._value = self._value[:self._cursor] + self._value[self._cursor + 1:]
            self._notify_change()
            return True

        # Navigation
        if event.key == "left":
            if event.ctrl or event.alt:
                self._cursor = _find_word_start(self._value, self._cursor)
            else:
                self._cursor = max(0, self._cursor - 1)
            self._last_was_kill = False
            return True

        if event.key == "right":
            if event.ctrl or event.alt:
                self._cursor = _find_word_end(self._value, self._cursor)
            else:
                self._cursor = min(len(self._value), self._cursor + 1)
            self._last_was_kill = False
            return True

        if event.key == "home" or (event.ctrl and event.key == "a"):
            self._cursor = 0
            self._last_was_kill = False
            return True

        if event.key == "end" or (event.ctrl and event.key == "e"):
            self._cursor = len(self._value)
            self._last_was_kill = False
            return True

        # Kill ring
        if event.ctrl and event.key == "k":
            killed = self._value[self._cursor:]
            if killed:
                self._save_undo()
                self._value = self._value[:self._cursor]
                if self._last_was_kill:
                    self._kill_ring.append_kill(killed)
                else:
                    self._kill_ring.kill(killed)
                is_kill = True
                self._notify_change()
            self._last_was_kill = is_kill
            return True

        if event.ctrl and event.key == "u":
            killed = self._value[:self._cursor]
            if killed:
                self._save_undo()
                self._value = self._value[self._cursor:]
                self._cursor = 0
                if self._last_was_kill:
                    self._kill_ring.append_kill(killed)
                else:
                    self._kill_ring.kill(killed)
                is_kill = True
                self._notify_change()
            self._last_was_kill = is_kill
            return True

        if event.ctrl and event.key == "y":
            text = self._kill_ring.yank()
            if text:
                self._save_undo()
                self._value = self._value[:self._cursor] + text + self._value[self._cursor:]
                self._cursor += len(text)
                self._notify_change()
            return True

        if event.alt and event.key == "y":
            text = self._kill_ring.yank_pop()
            if text:
                self._save_undo()
                self._value = self._value[:self._cursor] + text + self._value[self._cursor:]
                self._cursor += len(text)
                self._notify_change()
            return True

        # Alt+D: delete word forward
        if event.alt and event.key == "d":
            end = _find_word_end(self._value, self._cursor)
            killed = self._value[self._cursor:end]
            if killed:
                self._save_undo()
                self._value = self._value[:self._cursor] + self._value[end:]
                self._kill_ring.kill(killed)
                is_kill = True
                self._notify_change()
            self._last_was_kill = is_kill
            return True

        # History
        if event.key == "up" and self._history:
            if self._hist_idx == -1:
                self._hist_stash = self._value
                self._hist_idx = len(self._history) - 1
            elif self._hist_idx > 0:
                self._hist_idx -= 1
            else:
                return True
            self._value = self._history[self._hist_idx]
            self._cursor = len(self._value)
            return True

        if event.key == "down" and self._hist_idx >= 0:
            if self._hist_idx < len(self._history) - 1:
                self._hist_idx += 1
                self._value = self._history[self._hist_idx]
            else:
                self._hist_idx = -1
                self._value = self._hist_stash
            self._cursor = len(self._value)
            return True

        # Printable
        if len(event.key) == 1 and not event.ctrl and not event.alt:
            self._save_undo()
            self._value = self._value[:self._cursor] + event.key + self._value[self._cursor:]
            self._cursor += 1
            self._last_was_kill = False
            self._notify_change()
            return True

        self._last_was_kill = False
        return False
