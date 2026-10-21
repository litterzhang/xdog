"""Application-neutral retained messages and transcript composition."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from xdog.tui.components.details import ReasoningText, reasoning_preview
from xdog.tui.components.markdown import DefaultTextStyle, Markdown, MarkdownTheme
from xdog.tui.components.text import Text
from xdog.tui.transcript_browser import TranscriptRecord
from xdog.tui.tui import Component, Container
from xdog.tui.utils import apply_background_to_line, sanitize_terminal_text, strip_ansi, truncate_to_width


def _message_rows(render: Callable[[int], list[str]], width: int, marker: str) -> list[str]:
    """One role gutter, aligned wrapped text, and no marker-only streaming tail."""
    if width <= 0:
        return []
    gutter = 3 if width >= 4 else 0  # Preserve readable content on tiny terminals.
    rows = render(width - gutter)
    first = next((index for index, row in enumerate(rows) if strip_ansi(row).strip()), None)
    if first is None:
        return []
    return [
        (f" {marker} " if gutter and index == first else " " * gutter)
        + truncate_to_width(row, width - gutter, ellipsis="")
        for index, row in enumerate(rows)
    ]


class ThinkingMessage(Component):
    """Reasoning owns its preview, full text, and expansion state."""

    def __init__(self, text: str, style: Callable[[str], str], *, expanded: bool = False) -> None:
        self._style = style
        self._expanded = expanded
        self._message_rows_cache: tuple[int, list[str]] | None = None
        self._text = ReasoningText("", 0, 0)
        self.set_text(text)

    @property
    def detail_title(self) -> str:
        return "reasoning"

    @property
    def detail_body(self) -> str:
        return self._content

    def set_text(self, text: str) -> None:
        self._content = sanitize_terminal_text(text)
        self._refresh()

    def set_expanded(self, expanded: bool) -> None:
        self._expanded = expanded
        self._refresh()

    def _refresh(self) -> None:
        self._message_rows_cache = None
        self._text.compact = not self._expanded
        text = ""
        if self._content.strip():
            text = f"Thinking\n{self._content}" if self._expanded else reasoning_preview(self._content)
        self._text.set_text(self._style(text) if text else "")

    def render(self, width: int) -> list[str]:
        if self._message_rows_cache is None or self._message_rows_cache[0] != width:
            self._message_rows_cache = (width, _message_rows(self._text.render, width, self._style("•")))
        return self._message_rows_cache[1]

    def invalidate(self) -> None:
        self._message_rows_cache = None
        self._text.invalidate()


class NormalMessage(Markdown):
    """Assistant prose, independently updated from reasoning."""

    def __init__(self, text: str, theme: MarkdownTheme, *,
                 marker_style: Callable[[str], str] | None = None) -> None:
        self._marker_style = marker_style or (lambda value: value)
        self._message_rows_cache: tuple[int, list[str]] | None = None
        self.source_text = sanitize_terminal_text(text)
        super().__init__(self.source_text, 0, 0, theme, compact_paragraphs=True)

    def set_text(self, text: str) -> None:
        self.source_text = sanitize_terminal_text(text)
        super().set_text(self.source_text)

    def render(self, width: int) -> list[str]:
        if self._message_rows_cache is None or self._message_rows_cache[0] != width:
            self._message_rows_cache = (width, _message_rows(super().render, width, self._marker_style("•")))
        return self._message_rows_cache[1]

    def invalidate(self) -> None:
        super().invalidate()
        self._message_rows_cache = None

    def preferred_height(self, width: int) -> int:
        return len(self.render(width))


class UserMessage(Container):
    """User Markdown and background styling, shared by client adapters."""

    def __init__(self, text: str, theme: MarkdownTheme, style: DefaultTextStyle) -> None:
        super().__init__()
        self.source_text = sanitize_terminal_text(text)
        self._background = style.bg_color
        self._message_rows_cache: tuple[int, list[str]] | None = None
        # The whole message owns its background, including the role gutter/padding.
        self.add_child(Markdown(self.source_text, 0, 0, theme, default_text_style=replace(style, bg_color=None)))

    def render(self, width: int) -> list[str]:
        if self._message_rows_cache is None or self._message_rows_cache[0] != width:
            rows = _message_rows(super().render, width, "›")
            if rows:
                rows = ["", *rows, ""]
                if self._background is not None:
                    rows = [apply_background_to_line(row, width, self._background) for row in rows]
            self._message_rows_cache = (width, rows)
        return self._message_rows_cache[1]

    def invalidate(self) -> None:
        super().invalidate()
        self._message_rows_cache = None

    def set_text(self, text: str) -> None:
        self._message_rows_cache = None
        self.source_text = sanitize_terminal_text(text)
        for child in self.children:
            if isinstance(child, Markdown):
                child.set_text(self.source_text)


class Transcript(Container):
    """Own spacing; user/agent boundaries retain both messages' padding."""

    def __init__(self) -> None:
        super().__init__()
        self._record_ids: dict[int, str] = {}
        self._record_sequence = 0

    def add_child(self, component: Component) -> None:
        if isinstance(component, AssistantMessages):
            for child in component.children:
                self.add_child(child)
        else:
            super().add_child(component)
            self._record_sequence += 1
            self._record_ids[id(component)] = f"m{self._record_sequence}"

    def clear(self) -> None:
        super().clear()
        self._record_ids.clear()

    def transcript_records(self) -> tuple[TranscriptRecord, ...]:
        """Immutable source strings: no rendering, truncation or tool preview."""
        records = []
        for child in self.children:
            if isinstance(child, UserMessage):
                role, title, body = "user", "input", child.source_text
            elif isinstance(child, NormalMessage):
                role, title, body = "assistant", "response", child.source_text
            elif isinstance(child, ThinkingMessage):
                role, title, body = "reasoning", child.detail_title, child.detail_body
            elif isinstance(child, Text):
                role, title, body = "system", "notice", child.text
            elif hasattr(child, "detail_body"):
                role = str(getattr(child, "transcript_role", "tool"))
                title = str(getattr(child, "detail_title", "tool"))
                body = str(getattr(child, "transcript_body", child.detail_body))
            else:
                continue
            if body:
                records.append(TranscriptRecord(self._record_ids[id(child)], role, title, body))
        return tuple(records)

    def transform_user_input(self, original: str, transformed: str, image_count: int = 0) -> bool:
        suffix = f"\n[Images: {image_count}]" if image_count else ""
        for child in reversed(self.children):
            if isinstance(child, UserMessage):
                if child.source_text == sanitize_terminal_text(original + suffix):
                    child.set_text(transformed + suffix)
                    return True
                return False  # Never overwrite an unrelated/newer user echo.
        return False

    def remove_child(self, component: Component) -> None:
        if isinstance(component, AssistantMessages):
            for child in component.children:
                self.remove_child(child)
        else:
            super().remove_child(component)
            self._record_ids.pop(id(component), None)

    def render(self, width: int) -> list[str]:
        rows: list[str] = []
        previous_user = False
        for child in self.children:
            rendered = list(child.render(width))
            # Graphics payloads can depend on blank rows reserved above them.
            graphics = any("\x1b_G" in row or "\x1b]1337;" in row for row in rendered)
            user = isinstance(child, UserMessage)
            if not graphics and not user:
                while rendered and not strip_ansi(rendered[0]).strip():
                    rendered.pop(0)
                while rendered and not strip_ansi(rendered[-1]).strip():
                    rendered.pop()
                if rendered:
                    rendered = ["", *rendered, ""]
            if not rendered:
                continue
            if (not graphics and user == previous_user and rows and not strip_ansi(rows[-1]).strip()
                    and not strip_ansi(rendered[0]).strip()):
                # Prefer the user's colored separator over an uncolored one.
                if user:
                    rows[-1] = rendered[0]
                rows.extend(rendered[1:])
            else:
                if rows and strip_ansi(rows[-1]).strip() and strip_ansi(rendered[0]).strip():
                    rows.append("")
                rows.extend(rendered)
            previous_user = user
        return rows


class AssistantMessages(Container):
    """Stable update handle for separately retained thinking and prose messages."""

    def __init__(
        self, text: str, thinking: str, theme: MarkdownTheme,
        style: Callable[[str], str], *, expanded: bool = False,
    ) -> None:
        super().__init__()
        self.thinking_message = ThinkingMessage(thinking, style, expanded=expanded)
        self.normal_message = NormalMessage(text, theme, marker_style=style)
        self.children.extend((self.thinking_message, self.normal_message))

    @property
    def detail_title(self) -> str:
        return self.thinking_message.detail_title

    @property
    def detail_body(self) -> str:
        return self.thinking_message.detail_body

    def set_text(self, text: str, *, thinking: str | None = None) -> None:
        self.normal_message.set_text(text)
        if thinking is not None:
            self.thinking_message.set_text(thinking)

    def set_content(self, text: str, *, thinking: str = "") -> None:
        self.set_text(text, thinking=thinking)

    def set_expanded(self, expanded: bool) -> None:
        self.thinking_message.set_expanded(expanded)

    def render(self, width: int) -> list[str]:
        # Standalone compatibility; transcript stores the two children directly.
        rows = Transcript.render(self, width)  # type: ignore[arg-type]
        return rows
