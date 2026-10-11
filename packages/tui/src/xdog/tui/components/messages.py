"""Application-neutral retained messages and transcript composition."""

from __future__ import annotations

from collections.abc import Callable

from xdog.tui.components.details import ReasoningText, reasoning_preview
from xdog.tui.components.markdown import DefaultTextStyle, Markdown, MarkdownTheme
from xdog.tui.components.spacer import Spacer
from xdog.tui.components.text import Text
from xdog.tui.transcript_browser import TranscriptRecord
from xdog.tui.tui import Component, Container
from xdog.tui.utils import sanitize_terminal_text, strip_ansi


class ThinkingMessage(Component):
    """Reasoning owns its preview, full text, and expansion state."""

    def __init__(self, text: str, style: Callable[[str], str], *, expanded: bool = False) -> None:
        self._style = style
        self._expanded = expanded
        self._text = ReasoningText("", 1, 0)
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
        self._text.compact = not self._expanded
        text = ""
        if self._content.strip():
            text = f"Thinking\n{self._content}" if self._expanded else reasoning_preview(self._content)
        self._text.set_text(self._style(text) if text else "")

    def render(self, width: int) -> list[str]:
        return self._text.render(width)

    def invalidate(self) -> None:
        self._text.invalidate()


class NormalMessage(Markdown):
    """Assistant prose, independently updated from reasoning."""

    def __init__(self, text: str, theme: MarkdownTheme) -> None:
        self.source_text = sanitize_terminal_text(text)
        super().__init__(self.source_text, 1, 0, theme)

    def set_text(self, text: str) -> None:
        self.source_text = sanitize_terminal_text(text)
        super().set_text(self.source_text)


class UserMessage(Container):
    """User Markdown and background styling, shared by client adapters."""

    def __init__(self, text: str, theme: MarkdownTheme, style: DefaultTextStyle) -> None:
        super().__init__()
        self.source_text = sanitize_terminal_text(text)
        self.add_child(Spacer(1))
        self.add_child(Markdown(self.source_text, 1, 0, theme, default_text_style=style))


    def set_text(self, text: str) -> None:
        self.source_text = sanitize_terminal_text(text)
        for child in self.children:
            if isinstance(child, Markdown):
                child.set_text(self.source_text)


class Transcript(Container):
    """Own message order and exactly one separator between visible messages."""

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
        for child in self.children:
            rendered = list(child.render(width))
            # Graphics payloads can depend on blank rows reserved above them.
            graphics = any("\x1b_G" in row or "\x1b]1337;" in row for row in rendered)
            if not graphics:
                while rendered and not strip_ansi(rendered[0]).strip():
                    rendered.pop(0)
                while rendered and not strip_ansi(rendered[-1]).strip():
                    rendered.pop()
            if not rendered:
                continue
            if rows:
                rows.append("")
            rows.extend(rendered)
        return rows


class AssistantMessages(Container):
    """Stable update handle for separately retained thinking and prose messages."""

    def __init__(
        self, text: str, thinking: str, theme: MarkdownTheme,
        style: Callable[[str], str], *, expanded: bool = False,
    ) -> None:
        super().__init__()
        self.thinking_message = ThinkingMessage(thinking, style, expanded=expanded)
        self.normal_message = NormalMessage(text, theme)
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
        return ["", *rows] if rows else []
