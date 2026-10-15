"""Chat log component for the interactive TUI."""

from __future__ import annotations

from typing import Any

from xdog.coding.modes.interactive.components.assistant_message import AssistantMessageComponent
from xdog.coding.modes.interactive.components.tool_execution import ToolExecutionComponent
from xdog.coding.modes.interactive.components.user_message import UserMessageComponent
from xdog.coding.modes.interactive.theme import Theme
from xdog.tui.components.bounded_details import DetailRecord
from xdog.tui.components.details import set_details_expanded
from xdog.tui.components.extension_command import ExtensionCommand
from xdog.tui.components.messages import ThinkingMessage, Transcript
from xdog.tui.components.text import Text
from xdog.tui.tui import Component
from xdog.tui.utils import sanitize_terminal_text


class SessionInputComponent(Text):
    """Keyed preview and complete read-only details of an external request."""

    def __init__(self, identifier: str, theme: Theme) -> None:
        super().__init__('', 1, 0)
        self.identifier, self.theme = identifier, theme
        self.detail_body = ''

    def update(self, preview: str, details: str) -> None:
        self.set_text(self.theme.system('  ' + sanitize_terminal_text(preview)))
        self.detail_body = sanitize_terminal_text(details)


class ChatLog(Transcript):
    """Retained chat components plus a mutable streaming tail."""

    def __init__(self, theme: Theme) -> None:
        super().__init__()
        self._theme = theme
        self._streaming: dict[str, AssistantMessageComponent] = {}
        self._last_tool: ToolExecutionComponent | None = None
        self._details_expanded = False
        self._session_inputs: dict[str, SessionInputComponent] = {}

    def add_user(self, text: str) -> None:
        """Add a user message."""
        self._append(UserMessageComponent(text, self._theme))

    def add_assistant(self, text: str, *, thinking: str = "") -> None:
        """Add a completed assistant message, including visible reasoning."""
        self._append(AssistantMessageComponent(text, self._theme, thinking=thinking))

    def add_system(self, text: str) -> None:
        """Add a system/info message."""
        safe_text = sanitize_terminal_text(text)
        self._append(Text(self._theme.system(f"  {safe_text}"), 1, 0))

    def update_session_input(self, identifier: str, preview: str, details: str) -> None:
        component = self._session_inputs.get(identifier)
        if component is None or component not in self.children:
            component = SessionInputComponent(identifier, self._theme)
            self._session_inputs[identifier] = component
            self._append(component)
        component.update(preview, details)

    def remove_session_input(self, identifier: str) -> None:
        component = self._session_inputs.pop(identifier, None)
        if component is not None and component in self.children:
            self.remove_child(component)

    def add_tool(self, tool_name: str, arguments: dict[str, Any] | None = None) -> ToolExecutionComponent:
        """Add a tool execution component."""
        comp = ToolExecutionComponent(tool_name, arguments, self._theme)
        self._last_tool = comp
        self._append(comp)
        return comp

    def get_last_tool(self) -> ToolExecutionComponent | None:
        """Return the most recently added tool execution component."""
        return self._last_tool

    def start_assistant(
        self,
        text: str,
        stream_id: str = "default",
        *,
        thinking: str = "",
    ) -> AssistantMessageComponent:
        """Start a new streaming assistant message."""
        existing = self._streaming.get(stream_id)
        if existing is not None:
            existing.set_content(text, thinking=thinking)
            return existing
        comp = AssistantMessageComponent(text, self._theme, thinking=thinking)
        self._streaming[stream_id] = comp
        self._append(comp)
        return comp

    def update_assistant(
        self,
        text: str,
        stream_id: str = "default",
        *,
        thinking: str = "",
    ) -> None:
        """Update an existing streaming assistant message."""
        existing = self._streaming.get(stream_id)
        if existing is None:
            self.start_assistant(text, stream_id, thinking=thinking)
            return
        existing.set_content(text, thinking=thinking)

    def finalize_assistant(
        self,
        text: str,
        stream_id: str = "default",
        *,
        thinking: str = "",
    ) -> None:
        """Finalize a streaming assistant message."""
        existing = self._streaming.get(stream_id)
        if existing is not None:
            existing.set_content(text, thinking=thinking)
            del self._streaming[stream_id]
            return
        self._append(AssistantMessageComponent(text, self._theme, thinking=thinking))

    def drop_assistant(self, stream_id: str = "default") -> None:
        """Remove a streaming assistant component (e.g. on abort)."""
        existing = self._streaming.get(stream_id)
        if existing is None:
            return
        self.remove_child(existing)
        del self._streaming[stream_id]

    def clear_all(self) -> None:
        """Clear all messages."""
        self.clear()
        self._streaming.clear()
        self._last_tool = None
        self._session_inputs.clear()

    def detail_records(self) -> tuple[DetailRecord, ...]:
        """Snapshot tool and reasoning details without changing transcript state."""
        records: list[DetailRecord] = []
        for child in self.children:
            if isinstance(child, ThinkingMessage) and child.detail_body.strip():
                records.append(DetailRecord(
                    title=child.detail_title,
                    body=child.detail_body,
                    kind="reasoning",
                ))
            elif isinstance(child, ExtensionCommand) and child.detail_body:
                records.append(DetailRecord(child.detail_title, child.detail_body, "command"))
            elif isinstance(child, SessionInputComponent):
                records.append(DetailRecord('External input ' + child.identifier, child.detail_body, 'input'))
            elif isinstance(child, ToolExecutionComponent) and child.detail_body:
                records.append(DetailRecord(
                    title=child.detail_title,
                    body=child.detail_body,
                    kind="tool",
                ))
        return tuple(records)

    def set_details_expanded(self, expanded: bool) -> None:
        """Apply a presentation-only detail mode to retained components."""
        self._details_expanded = expanded
        for child in tuple(self.children):
            set_details_expanded(child, expanded)

    def _append(self, comp: Component) -> None:
        set_details_expanded(comp, self._details_expanded)
        self.add_child(comp)
