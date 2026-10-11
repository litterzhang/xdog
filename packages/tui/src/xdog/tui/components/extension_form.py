"""Typed extension form editor/review. Hosts independently validate all returned data."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable
from typing import Any

from xdog.tui.components.input_panel import InputPanel
from xdog.tui.keys import KeyEvent, KeyEventType
from xdog.tui.tui import Component
from xdog.tui.utils import sanitize_terminal_text, truncate_to_width, wrap_text_with_ansi


class _Theme:
    accent = bold = dim = border = staticmethod(lambda text: text)


def _check(field: dict[str, Any], value: Any) -> None:
    kind = field["kind"]
    if kind == "text":
        if (
            not isinstance(value, str)
            or len(value.encode()) > 16000
            or not field.get("min_length", 0) <= len(value) <= field.get("max_length", 16000)
        ):
            raise ValueError("Text length is outside the declared limits")
    elif kind in ("number", "integer"):
        if (
            type(value) not in (int, float)
            or kind == "integer"
            and type(value) is not int
            or type(value) is float
            and not math.isfinite(value)
            or type(value) is int
            and abs(value) > 2**53 - 1
        ):
            raise ValueError("Enter a finite number/in-range integer; booleans are not numbers")
        if "minimum" in field and value < field["minimum"] or "maximum" in field and value > field["maximum"]:
            raise ValueError("Number is outside the declared limits")
    elif kind == "boolean":
        if type(value) is not bool:
            raise ValueError("Select an explicit boolean value")
    elif kind == "multi_choice":
        if (not isinstance(value, list) or not field.get("min_items", 0) <= len(value) <= field.get(
                "max_items", len(field["choices"]))):
            raise ValueError("Selection count is outside the declared limits")
        for index, item in enumerate(value):
            if (not any(type(item) is type(choice["value"]) and item == choice["value"] for choice in field["choices"])
                    or any(type(item) is type(previous) and item == previous for previous in value[:index])):
                raise ValueError("Select distinct offered typed values")
    elif kind == "choice":
        if not any(type(value) is type(choice["value"]) and value == choice["value"] for choice in field["choices"]):
            raise ValueError("Select an offered choice")


def _constraints(field: dict[str, Any]) -> str:
    if field["kind"] == "text":
        return f"length {field.get('min_length', 0)}..{field.get('max_length', 16000)} chars; 16 KiB UTF-8 cap"
    if field["kind"] in ("integer", "number"):
        return f"range {field.get('minimum', 'unbounded')}..{field.get('maximum', 'unbounded')}"
    if field["kind"] == "multi_choice":
        return (f"select {field.get('min_items', 0)}..{field.get('max_items', len(field['choices']))} "
                "distinct typed choices")
    if field["kind"] == "choice":
        return f"{len(field['choices'])} typed choices"
    return "explicit true/false; unset is omitted"


class ExtensionForm(Component):
    requires_visible_confirmation = True

    def __init__(self, extension: str, dialog: dict[str, Any], answer: Callable[[Any], None]) -> None:
        self.extension, self.dialog, self.answer = extension, dialog, answer
        self.fields: list[dict[str, Any]] = dialog["fields"]
        self.values = {field["name"]: field["default"] for field in self.fields if "default" in field}
        self._field = 0
        self._choice = 0
        self._budget = 8
        self._focused = False
        self._phase = "edit"
        self._error = ""
        self._resolved = False
        self._visible = False
        self._scroll = 0
        self._seen: set[int] = set()
        self._layout = (0, 0, "")
        self._input = InputPanel(_Theme(), max_rows=3)
        self._input.on_change = lambda text: self._change()
        self._load()

    @property
    def field(self) -> dict[str, Any]:
        return self.fields[self._field]

    @property
    def focused(self) -> bool:
        return self._focused

    @focused.setter
    def focused(self, value: bool) -> None:
        if value != self._focused:
            self.invalidate()
        self._focused = value
        self._input.focused = value and self._phase == "edit" and self.field["kind"] in ("text", "integer", "number")

    def _change(self) -> None:
        self._touched = True
        self._error = ""
        self.invalidate()

    def _load(self) -> None:
        self._choice = 0
        value = self.values.get(self.field["name"])
        self._input.set_text(str(value) if value is not None else "")
        self._touched = False
        self.focused = self._focused
        self.invalidate()

    def invalidate(self) -> None:
        self._visible = False
        self._seen.clear()
        self._layout = (0, 0, "")
        self._scroll = 0

    def set_render_budget(self, rows: int) -> None:
        budget = max(0, min(12, rows))
        if budget != self._budget:
            self.invalidate()
        self._budget = budget

    def _finish(self, value: Any) -> None:
        if not self._resolved:
            self._resolved = True
            self.answer(value)

    def _commit(self) -> bool:
        field = self.field
        if field["kind"] not in ("text", "integer", "number") or not self._touched:
            return True
        raw = self._input.get_text()
        try:
            if field["kind"] == "text":
                value: Any = raw
            elif not raw.strip():
                self.values.pop(field["name"], None)
                return True
            elif field["kind"] == "integer":
                if not re.fullmatch(r"[+-]?[0-9]+", raw.strip()):
                    raise ValueError("Enter an integer")
                value = int(raw.strip())
            else:
                try:
                    value = float(raw.strip())
                except ValueError:
                    raise ValueError("Enter a number") from None
            _check(field, value)
            self.values[field["name"]] = value
            return True
        except (ValueError, UnicodeError):
            self._error = "Invalid field value; check type/limits (draft retained)"
            return False

    def _review(self) -> None:
        if not self._commit():
            return
        for index, field in enumerate(self.fields):
            if field.get("required", False) and field["name"] not in self.values:
                self._field = index
                self._load()
                self._error = "Required field is unset; provide a value"
                return
        for index, field in enumerate(self.fields):
            if field['name'] in self.values:
                try:
                    _check(field, self.values[field['name']])
                except ValueError:
                    self._field = index
                    self._load()
                    self._error = "Invalid field selection; check type/count limits"
                    return
        try:
            if len(json.dumps(self.values, ensure_ascii=False, allow_nan=False).encode()) > 65536:
                raise ValueError
        except (ValueError, UnicodeError):
            self._error = "Form answer exceeds its retention limit"
            return
        self._phase = "review"
        self.focused = self._focused
        self.invalidate()

    def _cycle(self, delta: int) -> None:
        field = self.field
        options = [False, True] if field["kind"] == "boolean" else [choice["value"] for choice in field["choices"]]
        name = field["name"]
        index = next(
            (
                i
                for i, value in enumerate(options)
                if name in self.values and type(value) is type(self.values[name]) and value == self.values[name]
            ),
            -1,
        )
        next_index = (index + 1 + delta) % (len(options) + 1) - 1
        if next_index < 0:
            self.values.pop(name, None)
        else:
            self.values[name] = options[next_index]
        self.invalidate()

    def _multi(self, event: KeyEvent) -> None:
        field, name = self.field, self.field['name']
        if event.key in ('up', 'down'):
            self._choice = min(max(0, self._choice + (-1 if event.key == 'up' else 1)), len(field['choices']) - 1)
        elif event.matches('delete'):
            self.values[name] = []  # Explicit empty differs from omission.
        elif event.matches('backspace'):
            self.values.pop(name, None)
        elif event.matches('space'):
            item = field['choices'][self._choice]['value']
            selected = self.values.get(name, [])
            exists = any(type(item) is type(value) and item == value for value in selected)
            if not exists and len(selected) >= field.get('max_items', len(field['choices'])):
                self._error = "Maximum selection count reached"
                return
            next_values = [value for value in selected if not (type(item) is type(value) and item == value)]
            if not exists:
                next_values.append(item)
            self.values[name] = [choice['value'] for choice in field['choices'] if any(
                type(value) is type(choice['value']) and value == choice['value'] for value in next_values)]
        self._error = ""
        self.invalidate()

    def handle_paste(self, text: str) -> bool:
        if self._phase == "edit" and self.field["kind"] in ("text", "integer", "number"):
            self._input.handle_paste(text)
        return True  # Pasted newlines never review or submit a form.

    def handle_input(self, event: KeyEvent) -> bool:
        if event.event_type is not KeyEventType.PRESS or self._resolved:
            return True
        if event.matches("escape") or event.matches("ctrl+c"):
            self._finish(None)
        elif self._phase == "review":
            if event.matches("ctrl+e"):
                self._phase = "edit"
                self.focused = self._focused
                self.invalidate()
            elif event.key in {"up", "down", "pageup", "pagedown", "home", "end"}:
                delta = max(1, self._budget - 3) if event.key in {"pageup", "pagedown"} else 1
                self._scroll = (
                    0
                    if event.key == "home"
                    else 100000
                    if event.key == "end"
                    else max(0, self._scroll + (-delta if event.key in {"up", "pageup"} else delta))
                )
                self._visible = False
            elif event.matches("enter") and self._visible:
                self._finish(dict(self.values))
        elif event.key in {"pageup", "pagedown"}:
            self._scroll = max(0, self._scroll + (-1 if event.key == "pageup" else 1) * 2)
            self._visible = False
        elif event.matches("tab") or event.matches("shift+tab"):
            if self._commit():
                self._field = (self._field + (-1 if event.matches("shift+tab") else 1)) % len(self.fields)
                self._load()
        elif event.matches("ctrl+enter"):
            self._review()
        elif event.matches("enter"):
            if self.field["kind"] == "text" and self.field.get("multiline", False):
                self._input.handle_input(KeyEvent("enter", ctrl=True))
            else:
                self._review()
        elif self.field["kind"] == "multi_choice":
            self._multi(event)
        elif self.field["kind"] in ("boolean", "choice"):
            if event.matches("space") or event.key in ("left", "right"):
                self._cycle(-1 if event.key == "left" else 1)
        else:
            self._input.handle_input(event)
        return True

    def render(self, width: int) -> list[str]:
        self._visible = False
        if not self._budget:
            return []
        if self._phase == "review":
            content = self.dialog["title"] + "\n" + self.dialog.get("message", "")
            for field in self.fields:
                value = (
                    json.dumps(self.values[field["name"]], ensure_ascii=False, allow_nan=False)
                    if field["name"] in self.values
                    else "[unset / omitted]"
                )
                content += f"\n{field['label']} ({field['name']}, {field['kind']}): {value}"
                content += (
                    "\n" + ("Required" if field.get("required", False) else "Optional") + "; " + _constraints(field)
                )
                if field.get("description"):
                    content += "\n" + field["description"]
            body = wrap_text_with_ansi(sanitize_terminal_text(content), max(1, width))
            count = max(0, self._budget - 3)
            layout = width, count, content
            if layout != self._layout:
                self._seen.clear()
                self._scroll = 0
                self._layout = layout
            self._scroll = min(self._scroll, max(0, len(body) - count))
            end = min(len(body), self._scroll + count)
            if width >= 24 and count and self._focused:
                self._seen.update(range(self._scroll, end))
            complete = len(self._seen) == len(body)
            self._visible = complete and width >= 24 and self._budget >= 4 and self._focused
            rows = [
                f"Extension {sanitize_terminal_text(self.extension)} · form review",
                f"Review {self._scroll + 1}-{end}/{len(body)}",
                *body[self._scroll : end],
                "Enter submits reviewed form · Ctrl+E edit · Esc cancels · NOT tool approval"
                if complete
                else "PgUp/PgDn review ALL fields · Ctrl+E edit · Esc cancels · NOT tool approval",
            ]
        else:
            field = self.field
            label = sanitize_terminal_text(field["label"]).replace("\n", " ")
            help_text = (
                self.dialog.get("message", "") + "\n" + field.get("description", "") + "\n" + _constraints(field)
            )
            help_lines = wrap_text_with_ansi(sanitize_terminal_text(help_text), max(1, width))
            help_count = max(0, min(3, self._budget - 6))
            self._scroll = min(self._scroll, max(0, len(help_lines) - help_count))
            rows = [
                f"Extension {sanitize_terminal_text(self.extension)} · form · {self._field + 1}/{len(self.fields)}",
                sanitize_terminal_text(self.dialog["title"]).replace("\n", " "),
                label + f" [{field['kind']}]" + (" required" if field.get("required", False) else " optional"),
                *help_lines[self._scroll : self._scroll + help_count],
            ]
            if field["kind"] in ("text", "integer", "number"):
                self._input.set_render_budget(max(1, min(3, self._budget - 5 - help_count)), show_borders=False)
                rows.extend(self._input.render(width))
            elif field['kind'] == 'multi_choice':
                choice = field['choices'][self._choice]
                selected = self.values.get(field['name'], [])
                marked = any(type(value) is type(choice['value']) and value == choice['value'] for value in selected)
                label = sanitize_terminal_text(choice['label']).replace('\n', ' ')
                rows.append(f"{'[x]' if marked else '[ ]'} {self._choice + 1}/{len(field['choices'])} · " + label
                            + " · " + json.dumps(choice['value'], ensure_ascii=False))
                rows.append(f"Selected {len(selected)} · ↑/↓ item · Space toggle · Del empty · Backspace unset")
            else:
                name = field["name"]
                value = json.dumps(self.values[name], ensure_ascii=False) if name in self.values else "[unset]"
                if field["kind"] == "choice" and name in self.values:
                    selected = next(
                        choice["label"]
                        for choice in field["choices"]
                        if type(choice["value"]) is type(self.values[name]) and choice["value"] == self.values[name]
                    )
                    value = selected + " · " + value
                rows.append(
                    "Space/←/→ selects: " + sanitize_terminal_text(value).replace("\n", " ↵ ").replace("\t", " ")
                )
            rows.append(self._error or "Tab/Shift+Tab field · Enter reviews (multiline adds line) · Ctrl+Enter reviews")
            rows.append("Esc cancels form · answers are data, NOT tool approval")
        return [truncate_to_width(row, max(0, width), "") for row in rows[: self._budget]]
