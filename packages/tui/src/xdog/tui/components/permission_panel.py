"""Interactive tool-permission approval panel."""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Generic, Protocol, TypeVar

from xdog.tui.components.select_list import SelectItem, SelectList
from xdog.tui.editor_layout import layout_editor
from xdog.tui.keys import KeyEvent
from xdog.tui.tui import Component
from xdog.tui.utils import sanitize_terminal_text, string_width, truncate_to_width


class PermissionTheme(Protocol):
    def accent(self, text: str) -> str: ...
    def inverse(self, text: str) -> str: ...
    def bold(self, text: str) -> str: ...
    def dim(self, text: str) -> str: ...
    def border(self, text: str) -> str: ...


Decision = TypeVar("Decision", bound=str)


class PermissionPanel(Component, Generic[Decision]):
    """Focused inline panel that resolves one permission request."""

    def __init__(
        self,
        summary: str,
        tool_name: str,
        choices: list[SelectItem[Decision]],
        theme: PermissionTheme,
        on_decision: Callable[[Decision], None],
        on_cancel: Callable[[], None],
        cancel_label: str = "cancel",
    ) -> None:
        if not choices:
            raise ValueError("A permission panel requires at least one choice.")
        self.summary = summary
        self.tool_name = tool_name
        self._on_decision = on_decision
        self._theme = theme
        self._cancel_label = sanitize_terminal_text(cancel_label)
        self._budget = 12
        self._scroll = 0
        self._summary_rows = 1
        self._summary_length = 1
        self._choices: SelectList[Decision] = SelectList(
            choices, max_visible=len(choices),
            selected_fn=lambda text: theme.inverse(theme.accent(text)),
            description_fn=theme.dim, scroll_fn=theme.dim,
        )
        self._choices.on_select_cb = lambda item: self._on_decision(item.value)
        self._choices.on_cancel_cb = on_cancel

    def set_render_budget(self, max_rows: int) -> None:
        self._budget = max(0, max_rows)

    def render(self, width: int) -> list[str]:
        if not self._budget:
            return []
        framed = self._budget >= 8 and width >= 32
        budget = self._budget - (2 if framed else 0)
        inner_width = width - 4 if framed else width
        # Show every choice when there is also room for the title and summary.
        # In short terminals, keep the selected action reachable.
        selected = self._choices.selected_item
        assert selected is not None
        hint = f"↑/↓ choose · Enter · Esc {self._cancel_label} · PgUp/PgDn summary"
        if budget >= 5:
            actions = self._choices.render(inner_width)
            if budget >= 6:
                actions.append(self._theme.dim(hint))
        else:
            actions = [self._theme.accent(f"→ {selected.label} · {hint}")]
        rows = []
        if budget >= 3:
            rows.append(self._theme.bold("Tool permission required"))
        self._summary_rows = max(0, budget - len(rows) - len(actions))
        summary = layout_editor(sanitize_terminal_text(self.summary), max(1, inner_width))
        self._summary_length = len(summary.rows)
        self._scroll = min(self._scroll, max(0, self._summary_length - self._summary_rows))
        if rows and budget >= 6:
            end = min(self._summary_length, self._scroll + self._summary_rows)
            title = (f"Tool permission required · {self.tool_name} · "
                     f"{self._scroll + 1}-{end}/{self._summary_length}")
            rows[0] = self._theme.bold(sanitize_terminal_text(title))
        if self._summary_rows:
            visible = [row.text for row in summary.rows[self._scroll:self._scroll + self._summary_rows]]
            rows.extend(visible)
        rows.extend(actions)
        if framed:
            ascii_mode = os.environ.get("XDOG_TUI_ASCII") == "1" or os.environ.get("TERM") == "dumb"
            tl, tr, bl, br, horizontal, vertical = ("+", "+", "+", "+", "-", "|") if ascii_mode else (
                "╭", "╮", "╰", "╯", "─", "│",
            )
            border = self._theme.border
            top = border(tl + horizontal * (width - 2) + tr)
            bottom = border(bl + horizontal * (width - 2) + br)
            body = []
            for row in rows:
                row = truncate_to_width(row, inner_width, "")
                padding = " " * (inner_width - string_width(row))
                body.append(border(vertical + " ") + row + padding + border(" " + vertical))
            return [top, *body, bottom]
        return [truncate_to_width(row, max(0, width), "") for row in rows]

    def handle_input(self, event: KeyEvent) -> bool:
        if event.key in ("pageup", "pagedown"):
            # One row of overlap keeps the line carrying the scroll hint reachable.
            step = max(1, self._summary_rows - 1)
            delta = step if event.key == "pagedown" else -step
            self._scroll = min(max(0, self._scroll + delta), max(0, self._summary_length - self._summary_rows))
            return True
        return self._choices.handle_input(event)

    def invalidate(self) -> None:
        pass
