"""Focused extension select/input/confirm forms; no host tool-policy authority."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from xdog.tui.components.input_panel import InputPanel
from xdog.tui.components.panel import ThemedPanel
from xdog.tui.keys import KeyEvent, KeyEventType
from xdog.tui.utils import sanitize_terminal_text, wrap_text_with_ansi


class ExtensionDialog(ThemedPanel):
    requires_visible_confirmation = True

    def __init__(self, extension: str, dialog: dict[str, Any], answer: Callable[[Any], None]) -> None:
        self.extension, self.dialog, self.answer = extension, dialog, answer
        if (dialog.get("kind") not in {"input", "select", "confirm"}
                or {"operation", "fields", "parent_id", "view_id", "fullscreen",
                    "key_events", "overlay", "live_overlay"}
                & dialog.keys()):
            raise ValueError("Only basic extension dialogs are supported")
        self._focused = False
        self._budget = 8
        self._selected = 0
        self._scroll = 0
        self._visible = False
        self._resolved = False
        self._input = InputPanel(self.panel_theme, max_rows=3)
        self._input.set_text(dialog.get("default", ""))
        self._input.on_submit = self._submit
        self._input.on_escape = lambda: self._finish(None)

    @property
    def focused(self) -> bool:
        return self._focused

    @focused.setter
    def focused(self, value: bool) -> None:
        self._focused = value
        self._input.focused = value and self.dialog["kind"] == "input"

    def set_render_budget(self, rows: int) -> None:
        self._budget = max(0, min(12, rows))

    def invalidate(self) -> None:
        self._visible = False

    def _finish(self, value: Any) -> None:
        if self._resolved:
            return
        self._resolved = True
        self.answer(value)

    def _submit(self, value: str) -> None:
        if self._visible and len(value.encode("utf-8")) <= 16000:
            self._finish(value)

    def handle_paste(self, text: str) -> bool:
        if self.dialog["kind"] == "input":
            self._input.handle_paste(text)
        return True  # Select/confirm pastes never confirm.

    def handle_input(self, event: KeyEvent) -> bool:
        if event.event_type is not KeyEventType.PRESS:
            return True
        if event.matches("escape") or event.matches("ctrl+c"):
            self._finish(None)
        elif self.dialog["kind"] == "input":
            if event.matches("enter") and not self._visible:
                return True
            self._input.handle_input(event)
        elif event.matches("up") or event.matches("down"):
            choices = 2 if self.dialog["kind"] == "confirm" else len(self.dialog["choices"])
            self._selected = min(max(0, self._selected + (-1 if event.matches("up") else 1)), choices - 1)
            self._visible = False
        elif event.matches("pageup") or event.matches("pagedown"):
            self._scroll = max(0, self._scroll + (-1 if event.matches("pageup") else 1) * max(1, self._budget - 3))
            self._visible = False
        elif event.matches("enter") and self._visible:
            self._finish(bool(self._selected) if self.dialog["kind"] == "confirm"
                         else self.dialog["choices"][self._selected])
        return True

    def render(self, width: int) -> list[str]:
        self._visible = False
        if self._budget == 0:
            return []
        title = f"Extension {self.extension} · {self.dialog['kind']}"
        body = wrap_text_with_ansi(sanitize_terminal_text(
            self.dialog["title"] + "\n" + self.dialog.get("message", "")), max(1, width))
        budget = max(0, self._budget - 3)
        self._scroll = min(self._scroll, max(0, len(body) - budget))
        rows = [sanitize_terminal_text(title), *body[self._scroll:self._scroll + budget]]
        if self.dialog["kind"] == "input":
            self._input.set_render_budget(max(1, min(3, self._budget - len(rows) - 1)), show_borders=False)
            rows.extend(self._render_child(self._input, width))
        else:
            choices = ["No", "Yes"] if self.dialog["kind"] == "confirm" else self.dialog["choices"]
            rows.append(f"▶ {self._selected + 1}/{len(choices)} · {sanitize_terminal_text(choices[self._selected])}")
        rows.append("Enter answers this dialog only · Esc cancels · PgUp/PgDn details")
        self._visible = self._budget >= 4 and budget >= 1 and width >= 24 and len(rows) <= self._budget
        return self._styled_rows(rows[:self._budget], width)
