"""Explicit named-argument completion; suggestions remain inert editable data."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from xdog.tui.components.input_panel import InputPanel
from xdog.tui.keys import KeyEvent, KeyEventType
from xdog.tui.tui import Component
from xdog.tui.utils import sanitize_terminal_text, truncate_to_width


class _Theme:
    accent = bold = dim = border = staticmethod(lambda text: text)


class McpCompletionPanel(Component):
    def __init__(
        self,
        server: str,
        kind: str,
        name: str,
        names: tuple[str, ...],
        context: dict[str, str],
        request: Callable[[dict[str, str]], None],
        cancel: Callable[[], None],
        insert: Callable[[str, str], None],
        close: Callable[[], None],
    ) -> None:
        self.server, self.kind, self.name, self.names = server, kind, name, names
        self.request, self.cancel, self.insert, self.on_close = request, cancel, insert, close
        self.drafts = dict(context)
        self._argument = next((i for i, key in enumerate(names) if key not in context), 0)
        self._values: tuple[str, ...] = ()
        self._selected = 0
        self._receipt: tuple[str, str, dict[str, str]] | None = None
        self._budget = 8
        self._visible = False
        self._focused = False
        self._message = "Explicit suggestions only; no prompt/resource retrieval or tool call"
        self._input = InputPanel(_Theme(), max_rows=1)
        self._input.focused = False
        self._input.set_text(self.drafts.get(self.argument, ""))
        self._input.on_change = lambda text: self.invalidate_values()

    @property
    def focused(self) -> bool:
        return self._focused

    @focused.setter
    def focused(self, value: bool) -> None:
        if value != self._focused:
            self.invalidate()
        self._focused = value
        self._input.focused = value

    @property
    def argument(self) -> str:
        return self.names[self._argument]

    def invalidate_values(self) -> None:
        if self._receipt is not None:
            self.cancel()
        self._receipt = None
        self._values = ()
        self._selected = 0
        self._visible = False
        self._message = "Draft changed; Tab requests fresh suggestions"

    def invalidate(self) -> None:
        self._visible = False

    def close(self) -> None:
        self.invalidate_values()

    def set_render_budget(self, rows: int) -> None:
        if rows != self._budget:
            self._visible = False
        self._budget = max(0, min(12, rows))

    def publish(self, data: dict[str, Any], error: str = "") -> None:
        receipt = self._receipt
        self._receipt = None
        if receipt is None:
            return
        if error:
            self._message = sanitize_terminal_text(error)
            return
        try:
            context = dict(data["context"])
            if (data["server"], data["kind"], data["name"]) != (self.server, self.kind, self.name) or (
                data["argument"],
                data["value"],
                context,
            ) != receipt:
                raise ValueError("Completion scope/draft changed; request again")
            values = data["values"]
            total, more = data.get("total"), data.get("has_more", False)
            if (
                not isinstance(values, (list, tuple))
                or len(values) > 100
                or any(not isinstance(value, str) or len(value.encode()) > 8192 for value in values)
                or total is not None
                and (type(total) is not int or not len(values) <= total <= 2**53 - 1)
                or type(more) is not bool
            ):
                raise ValueError("Invalid completion values/count")
            self._values = tuple(values)
            self._selected = 0
            self._visible = False
            self._message = f"{len(values)} suggestions · total {total if total is not None else 'unknown'}" + (
                " · more available: narrow prefix and request again" if more else ""
            )
        except (KeyError, ValueError, TypeError):
            self._values = ()
            self._message = "Completion scope/draft or response invalid; request again"

    def handle_paste(self, text: str) -> bool:
        if self._focused:
            self._input.handle_paste(text)
        return True

    def handle_input(self, event: KeyEvent) -> bool:
        if event.event_type != KeyEventType.PRESS:
            return True
        if event.matches("escape") or event.matches("ctrl+c"):
            self.on_close()
        elif not self._focused:
            return True  # Background suggestions remain inert, never capture another owner's edits.
        elif event.matches("ctrl+n") or event.matches("ctrl+p"):
            self.drafts[self.argument] = self._input.get_text()
            self.invalidate_values()
            self._argument = (self._argument + (1 if event.matches("ctrl+n") else -1)) % len(self.names)
            self._input.set_text(self.drafts.get(self.argument, ""))
        elif event.matches("tab") and self._receipt is None:
            prefix = self._input.get_text()
            context = {key: value for key, value in self.drafts.items() if key != self.argument}
            if len(prefix.encode()) > 8192:
                self._message = "Completion prefix exceeds its limit"
                return True
            self._values = ()
            self._visible = False
            self._receipt = self.argument, prefix, context
            self._message = "Suggestions loading; editing/cancel fences this request"
            self.request(
                {"argument": self.argument, "value": prefix, "context": json.dumps(context, ensure_ascii=False)}
            )
        elif self._values and event.key in {"up", "down", "pageup", "pagedown", "home", "end"}:
            step = max(1, self._budget - 5) if event.key in {"pageup", "pagedown"} else 1
            index = self._selected + (-step if event.key in {"up", "pageup"} else step)
            self._selected = (
                0
                if event.key == "home"
                else len(self._values) - 1
                if event.key == "end"
                else min(max(0, index), len(self._values) - 1)
            )
            self._visible = False
        elif event.matches("enter"):
            if self._visible and self._receipt is None:
                value = self._values[self._selected] if self._values else self._input.get_text()
                self.insert(self.argument, value)
        else:
            self._input.handle_input(event)
        return True

    def render(self, width: int) -> list[str]:
        self._visible = False
        if not self._budget:
            return []
        self._input.set_render_budget(1, show_borders=False)
        count = max(0, self._budget - 5)
        top = max(0, self._selected - count + 1)
        choices = [
            f"{'▶' if index == self._selected else ' '} {value or '(empty string)'}"
            for index, raw in enumerate(self._values[top : top + count], top)
            for value in [sanitize_terminal_text(raw).replace("\n", " ↵ ").replace("\t", " ")]
        ]
        self._visible = (self._focused and width >= 24 and self._budget >= 5
                         and (not self._values or count > 0))
        label = sanitize_terminal_text(self.argument).replace("\n", " ↵ ").replace("\t", " ")
        rows = [
            f"MCP completion · {self.kind} · {label} ({self._argument + 1}/{len(self.names)})",
            sanitize_terminal_text(self._message).replace("\n", " ↵ "),
            *self._input.render(width),
            *(choices or ["No suggestions; edit prefix or use typed value"]),
            "Ctrl+N/P variable · Tab requests · ↑/↓ choose · Enter inserts draft only · Esc back",
        ]
        return [
            truncate_to_width(sanitize_terminal_text(row) if "\x1b_pi:c" not in row else row, max(0, width), "")
            for row in rows[: self._budget]
        ]
