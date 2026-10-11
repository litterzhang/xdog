"""Dependency-light plan inspection with revision-bound visible confirmation."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from xdog.tui.keys import KeyEvent, KeyEventType
from xdog.tui.tui import Component
from xdog.tui.utils import sanitize_terminal_text, truncate_to_width, wrap_text_with_ansi


class PlanPanel(Component):
    requires_visible_confirmation = True
    focused = False

    def __init__(self, source: Callable[[], Mapping[str, Any]], action: Callable[[str, int], None],
                 close: Callable[[], None]) -> None:
        self.source, self.action, self.on_close = source, action, close
        self._selected = 0
        self._scroll = 0
        self._follow_selection = False
        self._budget = 8
        self._confirm: tuple[str, int] | None = None
        self._visible_confirm = False
        self._revision = -1

    def set_render_budget(self, rows: int) -> None:
        self._budget = max(0, min(12, rows))

    def _snapshot(self) -> Mapping[str, Any]:
        state = self.source()
        if state.get("busy") or state.get("revision") != self._revision:
            self._revision = int(state.get("revision", 0))
            self._confirm = None
            self._visible_confirm = False
        self._selected = min(self._selected, max(0, len(state.get("steps", ())) - 1))
        return state

    def handle_paste(self, text: str) -> bool:
        return True  # A paste cannot authorize execution.

    def handle_input(self, event: KeyEvent) -> bool:
        if event.event_type == KeyEventType.RELEASE:
            return True
        state = self._snapshot()
        if event.matches("escape") or event.matches("ctrl+c"):
            if self._confirm is not None:
                self._confirm = None
                self._visible_confirm = False
            else:
                self.on_close()
        elif self._confirm is not None:
            if event.matches("enter") and self._visible_confirm:
                action, revision = self._confirm
                self._confirm = None
                self._visible_confirm = False
                self.action(action, revision)
        elif event.matches("e") or event.matches("s"):
            if state.get("busy"):
                return True
            if event.key == "e" and (state.get("mode") != "planning" or not state.get("steps")):
                return True
            self._confirm = ("execute" if event.key == "e" else "stop", self._revision)
            self._visible_confirm = False
        elif event.matches("r") and not state.get("busy"):
            self.action("revise", self._revision)
        elif event.key in {"up", "down"}:
            rows = state.get("steps", ())
            self._selected = min(max(0, self._selected + (-1 if event.key == "up" else 1)), max(0, len(rows) - 1))
            self._follow_selection = True
        elif event.key in {"pageup", "pagedown", "home", "end"}:
            if event.key == "home":
                self._scroll = 0
            elif event.key == "end":
                self._scroll = 100000
            else:
                self._scroll = max(0, self._scroll + (-1 if event.key == "pageup" else 1) * max(1, self._budget - 3))
        return True

    def render(self, width: int) -> list[str]:
        state = self._snapshot()
        self._visible_confirm = False
        if not self._budget:
            return []
        title = f"Plan · {state.get('mode', 'off')} · revision {self._revision}"
        body = []
        for text in (state.get("objective", ""), state.get("explanation", "")):
            if text:
                body.extend(wrap_text_with_ansi(sanitize_terminal_text(str(text)), max(1, width)))
        selected_row = 0
        for index, step in enumerate(state.get("steps", ())):
            if index == self._selected:
                selected_row = len(body)
            text = f"{'▶' if index == self._selected else ' '} {index + 1}. [{step['status']}] {step['description']}"
            body.extend(wrap_text_with_ansi(sanitize_terminal_text(text), max(1, width)))
        budget = max(0, self._budget - 2)
        if self._follow_selection and budget:
            self._scroll = selected_row
            self._follow_selection = False
        self._scroll = min(self._scroll, max(0, len(body) - budget))
        if self._confirm is not None:
            action, _revision = self._confirm
            rows = [title, f"Confirm {action}: return to existing tool policy; no work auto-starts.",
                    "Enter confirms this revision · Esc cancels"]
            self._visible_confirm = self._budget >= 3 and width >= 24
        else:
            rows = [title, *(body[self._scroll:self._scroll + budget] or ["No plan steps; /plan start OBJECTIVE"]),
                    ("Waiting for idle/plan refresh · scroll/inspect only · Esc close" if state.get("busy") else
                     "↑/↓ step · PgUp/PgDn scroll · e execution · r revise · s stop · Esc close")]
        return [truncate_to_width(row, max(0, width), "") for row in rows[:self._budget]]
