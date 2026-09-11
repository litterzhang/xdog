"""Directory-scoped TUI session selection before starting the agent."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import click
from xdog.coding.core.session_manager import SessionManager, SessionMeta
from xdog.tui.components.select_list import SelectItem, SelectList
from xdog.tui.keys import KeyEvent
from xdog.tui.tui import TUI
from xdog.tui.utils import sanitize_terminal_text, truncate_to_width


def _format_session(meta: SessionMeta, idx: int) -> str:
    ts = datetime.fromtimestamp(meta.updated_at, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
    summary = " ".join(sanitize_terminal_text(meta.summary).split()) or "(no summary)"
    model = " ".join(sanitize_terminal_text(meta.model).split())
    scope = "Current dir" if meta.working_dir else "Unknown dir"
    return f"[{scope}] {idx}. {ts} · {meta.session_id[:8]} · {model} · {meta.message_count} msgs · {summary}"


class SessionPicker(SelectList[str]):
    """Scrollable sessions with an explicit, terminal-height-bounded selection."""

    transient_start = 0

    def __init__(self, sessions: list[SessionMeta], directory: Path) -> None:
        # Preserve newest-first ordering within each group, without claiming legacy ownership.
        sessions = sorted(sessions, key=lambda meta: not bool(meta.working_dir))
        super().__init__([SelectItem(label=_format_session(meta, idx), value=meta.session_id)
                          for idx, meta in enumerate(sessions, 1)])
        self.directory = directory
        self.height = 24

    def set_height(self, height: int) -> None:
        self.height = max(1, height)

    def render(self, width: int) -> list[str]:
        title = [f"Resume session · {sanitize_terminal_text(str(self.directory))}"] if self.height >= 4 else []
        hints = ["↑/↓ choose · PgUp/PgDn page · Enter resume · Esc cancel"] if self.height >= 3 else []
        self._max_visible = max(1, self.height - len(title) - len(hints) - 1)
        rows = title + super().render(width)[:self.height - len(title) - len(hints)] + hints
        return [truncate_to_width(row, max(0, width), "…") for row in rows]

    def handle_input(self, event: KeyEvent) -> bool:
        if event.key in ("pageup", "pagedown", "home", "end"):
            if event.key == "home":
                self._selected_index = 0
            elif event.key == "end":
                self._selected_index = max(0, len(self._items) - 1)
            else:
                delta = self._max_visible * (-1 if event.key == "pageup" else 1)
                self._selected_index = max(0, min(len(self._items) - 1, self._selected_index + delta))
            return True
        return super().handle_input(event)


def pick_session_command(working_dir: Path | None = None) -> str | None:
    """Select a session belonging to the effective current directory."""
    directory = (working_dir or Path.cwd()).resolve()
    sessions = SessionManager().list_sessions(limit=None, working_dir=directory, include_unknown=True)
    if not sessions:
        click.echo(f"No sessions found in {directory}.")
        return None
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise click.ClickException("Session selection requires a terminal; use --resume-id for non-interactive runs.")

    selected: str | None = None
    tui = TUI()
    picker = SessionPicker(sessions, directory)

    def choose(item: SelectItem[str]) -> None:
        nonlocal selected
        selected = item.value
        tui.stop()

    picker.on_select_cb = choose
    picker.on_cancel_cb = tui.stop
    tui.add_child(picker)
    tui.set_focus(picker)
    tui.start()
    return selected
