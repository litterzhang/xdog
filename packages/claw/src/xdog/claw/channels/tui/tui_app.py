"""OpenClaw-style TUI for claw — faithful port of openclaw/src/tui.

Uses the same architecture as OpenClaw and the original TypeScript pi-tui:
- Components return ``list[str]`` (ANSI-styled lines)
- Container concatenates children's lines
- TUI diffs string arrays for differential rendering

Component tree (matching OpenClaw exactly):
    TUI
    └── root (Container)
        ├── header (Text)
        ├── chatLog (ChatLog extends Container)
        │   ├── UserMessage (Container: Spacer + Markdown with bg/color)
        │   ├── AssistantMessage (Container: Spacer + Markdown, default fg)
        │   └── SystemMessage (Spacer + Text)
        ├── statusContainer (Container: swaps Loader / Text)
        ├── footer (Text)
        └── editor (CustomEditor)

Event protocol (matching OpenClaw's chat/agent events):
    delta   → streaming tokens (updateAssistant)
    final   → completed response (finalizeAssistant)
    aborted → run was cancelled
    error   → run errored
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import queue
import random
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from xdog.ai.types import ImageContent
from xdog.tui.components.details import set_details_expanded
from xdog.tui.components.details_panel import DetailRecord, DetailsPanel, streaming_preview
from xdog.tui.components.image import Image
from xdog.tui.components.inline_layout import CompactText, InlineLayout
from xdog.tui.components.input_panel import InputPanel, SlashSelectList
from xdog.tui.components.markdown import DefaultTextStyle, MarkdownTheme
from xdog.tui.components.messages import AssistantMessages, ThinkingMessage, Transcript
from xdog.tui.components.messages import UserMessage as SharedUserMessage
from xdog.tui.components.spacer import Spacer
from xdog.tui.components.status_line import StatusLine
from xdog.tui.components.text import Text
from xdog.tui.components.tool_message import ToolMessage as ToolMessageView
from xdog.tui.event_queue import EventQueue, thaw_event
from xdog.tui.keys import KeyEvent
from xdog.tui.tui import TUI, Component, Container
from xdog.tui.utils import sanitize_terminal_text

logger = logging.getLogger(__name__)

# ── ANSI color helpers (matching OpenClaw's chalk usage) ──────────────

_RST = "\x1b[0m"


def _fg(hex_color: str) -> Callable[[str], str]:
    """Return a function that applies foreground color.

    Uses \\x1b[39m (fg-only reset) instead of \\x1b[0m (full reset),
    matching OpenClaw's theme.fg() which does NOT kill background color.
    """
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    prefix = f"\x1b[38;2;{r};{g};{b}m"

    def apply(text: str) -> str:
        return f"{prefix}{text}\x1b[39m"

    return apply


def _bg(hex_color: str) -> Callable[[str], str]:
    """Return a function that applies background color.

    Uses \\x1b[49m (bg-only reset) instead of \\x1b[0m (full reset),
    matching OpenClaw's theme.bg() which does NOT kill foreground color.
    """
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    prefix = f"\x1b[48;2;{r};{g};{b}m"

    def apply(text: str) -> str:
        return f"{prefix}{text}\x1b[49m"

    return apply


def _bold(text: str) -> str:
    return f"\x1b[1m{text}{_RST}"


def _dim(text: str) -> str:
    return f"\x1b[2m{text}{_RST}"


def _italic(text: str) -> str:
    return f"\x1b[3m{text}{_RST}"


def _context_usage_tokens(
    last_turn: dict[str, int],
    session_total: dict[str, int],
) -> int:
    """Tokens currently occupying the model's context window.

    Cached prefix tokens still occupy the window, so the most recent turn's
    ``input + cache_read + cache_write`` is the real occupancy. Session totals
    are cumulative across turns and would over-count, so they are only used as
    a fallback before the first turn of a (re)connected session completes.
    """
    turn_total = (
        last_turn.get("input", 0)
        + last_turn.get("cache_read", 0)
        + last_turn.get("cache_write", 0)
    )
    if turn_total > 0:
        return turn_total
    return session_total.get("input", 0)


def _format_tokens(count: int) -> str:
    """Format token count like OpenClaw's formatTokens().

    < 1,000:       raw number (e.g. "500")
    < 10,000:      one decimal (e.g. "5.2k")
    < 1,000,000:   rounded thousands (e.g. "150k")
    < 10,000,000:  one decimal millions (e.g. "5.2M")
    >= 10,000,000: rounded millions (e.g. "12M")
    """
    if count < 1_000:
        return str(count)
    if count < 10_000:
        return f"{count / 1_000:.1f}k"
    if count < 1_000_000:
        return f"{count // 1_000}k"
    if count < 10_000_000:
        return f"{count / 1_000_000:.1f}M"
    return f"{count // 1_000_000}M"


# ── OpenClaw Dark Palette (from openclaw/src/tui/theme/theme.ts) ──────

PALETTE = {
    "text": "#E8E3D5",
    "dim": "#7B7F87",
    "accent": "#F6C453",
    "accentSoft": "#F2A65A",
    "border": "#3C414B",
    "userBg": "#2B2F36",
    "userText": "#F3EEE0",
    "systemText": "#9BA3B2",
    "quote": "#8CC8FF",
    "quoteBorder": "#3B4D6B",
    "code": "#F0C987",
    "codeBlock": "#1E232A",
    "codeBorder": "#343A45",
    "link": "#7DD3A5",
    "error": "#F97066",
    "success": "#7DD3A5",
}

# Build theme functions (like OpenClaw's theme object)
theme_fg = _fg(PALETTE["text"])
theme_dim = _fg(PALETTE["dim"])
theme_accent = _fg(PALETTE["accent"])
theme_accent_soft = _fg(PALETTE["accentSoft"])
def theme_header(t: str) -> str:
    return _bold(_fg(PALETTE["accent"])(t))
theme_system = _fg(PALETTE["systemText"])
theme_user_bg = _bg(PALETTE["userBg"])
theme_user_text = _fg(PALETTE["userText"])
theme_error = _fg(PALETTE["error"])
theme_border = _fg(PALETTE["border"])

# Markdown theme (matching OpenClaw's markdownTheme)
MD_THEME = MarkdownTheme(
    heading=lambda t: _bold(_fg(PALETTE["accent"])(t)),
    link=_fg(PALETTE["link"]),
    link_url=lambda t: _dim(t),
    code=_fg(PALETTE["code"]),
    code_block=_fg(PALETTE["code"]),
    code_block_border=_fg(PALETTE["codeBorder"]),
    quote=_fg(PALETTE["quote"]),
    quote_border=_fg(PALETTE["quoteBorder"]),
    hr=theme_border,
    list_bullet=_fg(PALETTE["accentSoft"]),
    bold=_bold,
    italic=_italic,
)

# Editor theme
EDITOR_BORDER = theme_border

# ── Waiting phrases (from OpenClaw tui-waiting.ts) ────────────────────

WAITING_PHRASES = [
    "flibbertigibbeting",
    "kerfuffling",
    "dillydallying",
    "twiddling thumbs",
    "noodling",
    "bamboozling",
    "moseying",
    "hobnobbing",
    "pondering",
    "conjuring",
]

# ── Slash Commands ────────────────────────────────────────────────────

SLASH_COMMANDS = {
    "/quit": "Exit the TUI",
    "/exit": "Exit the TUI",
    "/reset": "Reset chat session",
    "/status": "Show gateway status",
    "/clear": "Clear screen and chat",
}

# ── Ctrl+C handling (matching OpenClaw's resolveCtrlCAction) ──────────


def _resolve_ctrl_c_action(
    has_input: bool, now: float, last_ctrl_c_at: float, exit_window: float = 1.0
) -> tuple[str, float]:
    """Resolve Ctrl+C action: 'clear', 'warn', or 'exit'.

    Matches OpenClaw's resolveCtrlCAction exactly:
    - If editor has input → clear it
    - If double-press within window → exit
    - Otherwise → warn (press again to exit)

    Returns (action, next_last_ctrl_c_at).
    """
    if has_input:
        return ("clear", now)
    if now - last_ctrl_c_at <= exit_window:
        return ("exit", last_ctrl_c_at)
    return ("warn", now)


# ── Shimmer (matching OpenClaw's shimmerText) ─────────────────────────


def _shimmer_text(text: str, tick: int) -> str:
    """Sweep a bold highlight across text (matching OpenClaw tui-waiting.ts)."""
    width = 6
    pos = tick % (len(text) + width)
    start = max(0, pos - width)
    end = min(len(text) - 1, pos)
    chars = []
    for i, ch in enumerate(text):
        if start <= i <= end:
            chars.append(_bold(theme_accent_soft(ch)))
        else:
            chars.append(theme_dim(ch))
    return "".join(chars)


def _pick_waiting_phrase(tick: int, phrases: list[str] | None = None) -> str:
    """Pick phrase rotating every 10 ticks (matching OpenClaw)."""
    ps = phrases or WAITING_PHRASES
    idx = (tick // 10) % len(ps)
    return ps[idx]


def _build_waiting_status(tick: int, elapsed: str, conn_status: str, phrase: str) -> str:
    """Build waiting status message (matching OpenClaw's buildWaitingStatusMessage)."""
    cute = _shimmer_text(f"{phrase}…", tick)
    return f"{cute} • {elapsed} | {conn_status}"


# Known prefixes of internal prompts injected by the goal runner and
# scheduler.  Used as a content-based fallback to filter old transcript
# entries that lack a ``channel`` tag.
_INTERNAL_PROMPT_PREFIXES = (
    "Continue working on your active goals:",
    "[Goal:",      # goal system task instructions
    "Goal completed:",
)


def _is_internal_prompt(content: str) -> bool:
    """Return True if *content* looks like a goal_runner/scheduler prompt."""
    for prefix in _INTERNAL_PROMPT_PREFIXES:
        if content.startswith(prefix):
            return True
    return False


# ── Message Components (matching OpenClaw components exactly) ─────────


class UserMessage(SharedUserMessage):
    """Claw style adapter for shared user messages."""

    def __init__(self, text: str) -> None:
        super().__init__(text, MD_THEME, DefaultTextStyle(color=theme_user_text, bg_color=theme_user_bg))


class AssistantMessage(AssistantMessages):
    """Claw style adapter; transcript retains thinking and prose separately."""

    def __init__(self, text: str, *, thinking: str = "") -> None:
        super().__init__(text, thinking, MD_THEME, theme_dim)


class ToolMessage(Container):
    """ID-keyed tool lifecycle retaining full output for expansion."""

    def __init__(self, name: str, arguments: dict[str, Any] | None, *, tool_call_id: str = "") -> None:
        super().__init__()
        self._view = ToolMessageView()
        self._name = sanitize_terminal_text(name)
        self._tool_call_id = sanitize_terminal_text(tool_call_id)
        self._arguments = {
            sanitize_terminal_text(str(key)): sanitize_terminal_text(str(value))
            for key, value in (arguments or {}).items()
        }
        self._result = ""
        self._state = "running"
        self._is_error = False
        self._completed = False
        self._expanded = False
        self._header = Text("", 1, 0)
        self._body = Text("", 1, 0)
        self.add_child(Spacer(1))
        self.add_child(self._header)
        self.add_child(self._body)
        self.add_child(Spacer(1))
        self._render()

    def set_image(self, image: ImageContent) -> None:
        try:
            data = base64.b64decode(image.data, validate=True)
        except (ValueError, TypeError):
            return
        self.add_child(Image(data=data, alt=image.mime_type))

    def render(self, width: int) -> list[str]:
        if self._expanded:
            return super().render(width)
        summary = " ".join(", ".join(f"{key}={value}" for key, value in self._arguments.items()).split())
        header = self._header.text
        if summary:
            header += theme_dim(f" · {summary}")
        self._view.update(
            header=header, summary=summary, output=self._result,
            running=self._state == "running",
            style=theme_error if self._is_error else theme_dim,
        )
        rows = self._view.render(width)[:-1]
        for child in self.children:
            if isinstance(child, Image):
                rows.extend(child.render(width))
        return [*rows, ""]

    def set_streaming(self, result: str) -> None:
        if self._state != "running":
            return
        self._result = sanitize_terminal_text(result)
        self._render()

    def set_canceled(self) -> None:
        if self._state != "running":
            return
        self._state = "canceled"
        self._completed = True
        self._render()

    def set_result(self, result: str, *, is_error: bool = False) -> None:
        if self._state == "canceled":
            return
        self._result = sanitize_terminal_text(result)
        self._is_error = is_error
        self._state = "error" if is_error else "success"
        self._completed = True
        self._render()

    @property
    def detail_body(self) -> str:
        return self._result

    @property
    def detail_title(self) -> str:
        return f"{self._name} [{self._tool_call_id}]" if self._tool_call_id else self._name

    def set_expanded(self, expanded: bool) -> None:
        if self._expanded == expanded:
            return
        self._expanded = expanded
        self._render()

    def _render(self) -> None:
        icons = {"running": "⚡", "success": "✓", "error": "✗", "canceled": "■"}
        icon = icons[self._state]
        header = f"  {icon} {self._name}"
        if self._state == "error":
            self._header.set_text(theme_error(header))
        elif self._state == "canceled":
            self._header.set_text(theme_dim(header))
        else:
            self._header.set_text(theme_accent(header))
        if not self._result:
            if self._completed:
                self._body.set_text(theme_dim("    → (no output)"))
            else:
                arguments = ", ".join(f"{key}={value}" for key, value in self._arguments.items())
                self._body.set_text(theme_dim(f"    {arguments}" if arguments else ""))
            return
        result_lines = self._result.splitlines()
        if self._state == "running" and not self._expanded:
            display = streaming_preview(self._result)
        elif self._expanded or (len(self._result) <= 500 and len(result_lines) <= 3):
            display = self._result
        else:
            excerpt = "\n".join(result_lines[:3])[:200]
            preview = excerpt.replace("\n", " ").replace("\r", "")
            remaining = len(self._result) - len(excerpt)
            line_count = len(result_lines)
            display = f"{preview} [...{remaining} more chars, {line_count} lines; Ctrl+O for details]"
        self._body.set_text(theme_error(f"    → {display}") if self._is_error else theme_dim(f"    → {display}"))


# ── ChatLog (matching OpenClaw's ChatLog exactly) ─────────────────────


class ChatLog(Transcript):
    """Retained chat history with ID-keyed streaming and tool state."""

    def __init__(self) -> None:
        super().__init__()
        self._streaming_runs: dict[str, AssistantMessage] = {}
        self._tools: dict[str, ToolMessage] = {}
        self._details_expanded = False

    def add_user(self, text: str) -> None:
        self._append(UserMessage(text))

    def add_assistant(self, text: str, *, thinking: str = "") -> None:
        """Add a completed assistant message (for history replay)."""
        self._append(AssistantMessage(text, thinking=thinking))

    def add_system(self, text: str) -> None:
        self._append(Spacer(1))
        self._append(Text(theme_system(sanitize_terminal_text(text)), 1, 0))

    def start_assistant(self, text: str, run_id: str = "default") -> AssistantMessage:
        """Start a new assistant message for streaming (matching OpenClaw)."""
        existing = self._streaming_runs.get(run_id)
        if existing is not None:
            existing.set_text(text)
            return existing
        comp = AssistantMessage(text)
        self._streaming_runs[run_id] = comp
        self._append(comp)
        return comp

    def update_assistant(self, text: str, run_id: str = "default") -> None:
        """Update an existing streaming assistant message (matching OpenClaw)."""
        existing = self._streaming_runs.get(run_id)
        if existing is None:
            self.start_assistant(text, run_id)
            return
        existing.set_text(text)

    def finalize_assistant(self, text: str, run_id: str = "default") -> None:
        """Finalize a streaming assistant message (matching OpenClaw)."""
        existing = self._streaming_runs.get(run_id)
        if existing is not None:
            existing.set_text(text)
            del self._streaming_runs[run_id]
            return
        self._append(AssistantMessage(text))

    def drop_assistant(self, run_id: str = "default") -> None:
        """Remove a streaming assistant component (matching OpenClaw)."""
        existing = self._streaming_runs.get(run_id)
        if existing is None:
            return
        self.remove_child(existing)
        del self._streaming_runs[run_id]

    def add_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None,
        *,
        tool_call_id: str,
    ) -> ToolMessage:
        existing = self._tools.get(tool_call_id)
        if existing is not None:
            return existing
        component = ToolMessage(name, arguments, tool_call_id=tool_call_id)
        self._tools[tool_call_id] = component
        self._append(component)
        return component

    def finish_tool(
        self,
        tool_call_id: str,
        name: str,
        result: str,
        *,
        is_error: bool = False,
    ) -> ToolMessage:
        component = self._tools.get(tool_call_id)
        if component is None:
            component = self.add_tool(name, None, tool_call_id=tool_call_id)
        component.set_result(result, is_error=is_error)
        self._tools.pop(tool_call_id, None)
        return component

    def set_details_expanded(self, expanded: bool) -> None:
        self._details_expanded = expanded
        for child in tuple(self.children):
            set_details_expanded(child, expanded)

    def close_assistant(self, run_id: str = "default") -> None:
        self._streaming_runs.pop(run_id, None)

    def clear_all(self) -> None:
        self.clear()
        self._streaming_runs.clear()
        self._tools.clear()

    def detail_records(self) -> tuple[DetailRecord, ...]:
        """Return immutable full-detail snapshots in transcript order."""
        records: list[DetailRecord] = []
        for child in self.children:
            if isinstance(child, ThinkingMessage) and child.detail_body.strip():
                records.append(DetailRecord(child.detail_title, child.detail_body, "reasoning"))
            elif isinstance(child, ToolMessage) and child.detail_body:
                records.append(DetailRecord(child.detail_title, child.detail_body, "tool"))
        return tuple(records)

    def _append(self, comp: Component) -> None:
        set_details_expanded(comp, self._details_expanded)
        self.add_child(comp)


# ── CustomEditor ──────────────────────────────────────────────────────


class _EditorTheme:
    """Adapt Claw's color callables to the shared prompt-editor theme."""

    accent = staticmethod(theme_accent)
    bold = staticmethod(_bold)
    dim = staticmethod(theme_dim)
    border = staticmethod(theme_border)


_EDITOR_THEME = _EditorTheme()

# Compatibility alias retained for callers that imported the former private type.
_SelectList = SlashSelectList


class CustomEditor(InputPanel):
    """Claw configuration for the shared grapheme-safe prompt editor."""

    def __init__(self) -> None:
        super().__init__(
            _EDITOR_THEME,
            command_provider=lambda: SLASH_COMMANDS,
            max_rows=8,
        )


# ── ChatApp — main application (matching OpenClaw's runTui) ──────────


class ChatApp:
    """Main TUI application matching OpenClaw's architecture exactly.

    Uses TUI (string-based differential renderer) with Container component
    tree: header, chatLog, statusContainer, footer, editor.

    Implements the same patterns as OpenClaw's runTui():
    - Editor submit handler with slash commands and history
    - Ctrl+C double-press logic (clear input → warn → exit)
    - Escape to abort active request
    - Event-based protocol (delta/final/aborted/error)
    - Status management with busy/idle state transitions
    - Waiting shimmer animation
    """

    def __init__(self, socket_path: str, group_id: str = "main") -> None:
        self.socket_path = socket_path
        self.group_id = group_id

        self._state: dict[str, Any] = {
            "model": "unknown",
            "group_id": group_id,
            "session_id": "",
            "connection_status": "connecting",
            "activity_status": "idle",
            "socket_url": f"unix://{socket_path}",
        }

        self._send_queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self._recv_queue = EventQueue()

        # Active run tracking (matching OpenClaw)
        self._active_run_id: str | None = None
        self._pending_messages: deque[str] = deque()
        self._last_ctrl_c_at: float = 0.0
        self._exit_requested = False
        self._has_connected = False
        self._details_expanded = False
        self._details_open = False
        self._history_format = 1
        self._history_tools: dict[str, ToolMessage] = {}
        self._replaying_history = False

        # Token usage tracking (matching OpenClaw's footer)
        self._usage = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
        self._last_turn_usage = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
        self._context_window = 0
        self._streaming_output_chars = 0  # tracks chars during streaming for live status
        self._turn_input_chars = 0  # chars of input context sent this turn
        self._active_text: str | None = None
        self._aborting_run_id: str | None = None
        self._restored_run_ids: set[str] = set()

        # Todo checklist state (ephemeral, within-turn only)
        self._todo_text: Text | None = None
        self._last_todos: list[dict[str, Any]] = []

        # Goal widget state (persistent across turns)
        self._goal_text: Text | None = None
        self._last_goal: dict[str, Any] | None = None

        # Waiting shimmer state (matching OpenClaw)
        self._waiting = False
        self._waiting_tick = 0
        self._waiting_phrase: str | None = None
        self._status_started: float | None = None
        self._last_activity_status = "idle"

        # Build component tree (matching OpenClaw tui.ts exactly)
        self._tui = TUI()

        # Public/private compatibility handles remain available, but header and
        # the former status container are no longer duplicate rendered surfaces.
        self._header = Text("", 1, 0)
        self._chat_log = ChatLog()
        self._todo_container = Container()
        self._goal_container = Container()
        self._work_container = Container()
        self._work_summary = CompactText("", 0, 0)
        self._work_container.add_child(self._work_summary)
        self._status_container = Container()
        self._footer = StatusLine(theme_dim)
        self._editor = CustomEditor()
        self._queue_container = Container()
        self._queue_summary = CompactText("", 0, 0)
        self._queue_container.add_child(self._queue_summary)
        self._details_panel = DetailsPanel(self._detail_records, max_rows=8)
        self._hints = CompactText("Enter send · Ctrl+Enter newline · Ctrl+O details · /status", 0, 0)
        self._banner_added = False

        self._status_text: Text | None = self._footer
        self._status_loader = None
        self._layout = InlineLayout(
            transcript=self._chat_log,
            work=self._work_container,
            status=self._footer,
            editor=self._editor,
            details=None,
            queue=None,
            hints=self._hints,
        )

        self._tui.add_child(self._layout)
        self._tui.set_focus(self._editor)
        self._tui.add_input_listener(self._handle_global_input)

        self._io_thread = threading.Thread(target=self._io_loop, daemon=True)

        # Wire up editor callbacks (matching OpenClaw's editor event wiring)
        self._editor.on_submit = self._handle_submit
        self._editor.on_ctrl_c = self._handle_ctrl_c
        self._editor.on_ctrl_d = self._request_exit
        self._editor.on_escape = self._handle_escape

    def _handle_global_input(self, event: KeyEvent) -> dict[str, object] | None:
        """Route bounded detail navigation before cancellation."""
        if event.matches("ctrl+z"):
            if not self._tui.suspend():
                self._set_activity_status("suspend is not supported on this platform")
            return {"consume": True}
        if self._editor.autocomplete_active:
            return None
        if self._details_open:
            if event.matches("escape") or event.matches("ctrl+c"):
                self._close_details()
                return {"consume": True}
            if any(event.matches(key) for key in ("left", "right", "pageup", "pagedown", "home", "end")):
                self._details_panel.handle_input(event)
                self._tui.request_render()
                return {"consume": True}
        if event.matches("escape") and self._active_run_id is not None:
            self._handle_escape()
            return {"consume": True}
        if not event.matches("ctrl+o"):
            return None
        self._details_open = not self._details_open
        if self._details_open:
            self._details_panel.show_latest(from_start=True)
        self._editor.set_focus(not self._details_open)
        self._tui.set_focus(self._details_panel if self._details_open else self._editor)
        self._details_expanded = self._details_open
        self._layout.details = self._details_panel if self._details_open else None
        self._update_hints()
        self._tui.request_render()
        return {"consume": True}

    def _close_details(self) -> None:
        self._details_open = False
        self._details_expanded = False
        self._layout.details = None
        self._tui.set_focus(self._editor)
        self._editor.set_focus(True)
        self._update_hints()
        self._tui.request_render()

    def _update_hints(self) -> None:
        self._hints.set_text(
            "Details focused · ←/→ entry · PgUp/PgDn scroll · End latest · Esc input"
            if self._details_open else "Enter send · Ctrl+Enter newline · Ctrl+O details · /status"
        )

    def _detail_records(self) -> tuple[DetailRecord, ...]:
        return self._chat_log.detail_records()

    def run(self) -> None:
        self._io_thread.start()
        # Initial ping is sent by _io_loop_async on first connect

        # Register tick callback for polling responses + shimmer
        self._tui.on_tick(self._poll)

        # Start TUI (blocking — handles raw mode, rendering)
        self._tui.start()

    # ── Ctrl+C handling (matching OpenClaw's resolveCtrlCAction) ──────

    def _handle_ctrl_c(self) -> None:
        """Handle Ctrl+C with double-press logic (matching OpenClaw exactly)."""
        now = time.monotonic()
        action, next_at = _resolve_ctrl_c_action(
            has_input=len(self._editor.get_text().strip()) > 0,
            now=now,
            last_ctrl_c_at=self._last_ctrl_c_at,
        )
        self._last_ctrl_c_at = next_at

        if action == "clear":
            self._editor.set_text("")
            self._set_activity_status("cleared input; press ctrl+c again to exit")
            self._tui.request_render()
        elif action == "exit":
            self._request_exit()
        else:  # warn
            self._set_activity_status("press ctrl+c again to exit")
            self._tui.request_render()

    def _handle_escape(self) -> None:
        """Abort once, restore pending text, and quarantine late run events."""
        if self._details_open:
            self._close_details()
            return
        run_id = self._active_run_id
        if run_id is None or self._aborting_run_id == run_id:
            return
        self._aborting_run_id = run_id
        self._send_queue.put({
            "type": "abort",
            "group_id": self._state.get("group_id", "main"),
            "run_id": run_id,
        })
        self._restore_pending_draft(run_id)
        self._chat_log.add_system("cancelling run")
        self._chat_log.drop_assistant(run_id)
        for tool in self._chat_log._tools.values():
            tool.set_canceled()
        self._update_queue_summary()
        self._set_activity_status("cancelling")
        self._tui.request_render()

    def _restore_pending_draft(self, run_id: str) -> None:
        """Restore active, queued, and currently typed text exactly once."""
        if run_id in self._restored_run_ids:
            return
        queued = tuple(self._pending_messages)
        self._pending_messages.clear()
        draft = self._editor.get_text()
        restored = "\n\n".join(
            text for text in (self._active_text or "", *queued, draft) if text.strip()
        )
        if restored:
            self._editor.set_text(restored)
        self._restored_run_ids.add(run_id)
        self._update_queue_summary()

    def _request_exit(self) -> None:
        """Request clean exit (matching OpenClaw's requestExit)."""
        if self._exit_requested:
            return
        self._exit_requested = True
        self._tui.stop()

    # ── Status management ─────────────────────────────────────────────

    _BUSY_STATES = frozenset({"sending", "waiting", "streaming", "running"})

    def _render_status(self) -> None:
        """Render activity and metadata into one stable bounded row."""
        activity = str(self._state.get("activity_status", "idle"))
        conn = str(self._state.get("connection_status", "connecting"))
        if activity in self._BUSY_STATES:
            if self._status_started is None:
                self._status_started = time.monotonic()
            self._update_busy_status()
        else:
            self._status_started = None
            self._set_status_line(activity, conn)
        self._last_activity_status = activity

    def _set_status_line(self, activity: str, connection: str) -> None:
        metadata = self._status_metadata()
        parts = [f"{connection} | {activity}" if activity else connection]
        if self._pending_messages:
            parts.append(f"queued {len(self._pending_messages)}")
        if metadata:
            parts.append(metadata)
        self._footer.set_text(" | ".join(parts))

    def _status_metadata(self) -> str:
        st = self._state
        session_short = str(st.get("session_id", "?"))[:12]
        model = str(st.get("model", "unknown"))
        group = str(st.get("group_id", "main"))
        usage = self._usage
        stats = [f"↑{_format_tokens(usage['input'])}", f"↓{_format_tokens(usage['output'])}"]
        if usage["cache_read"]:
            stats.append(f"R{_format_tokens(usage['cache_read'])}")
        if usage["cache_write"]:
            stats.append(f"W{_format_tokens(usage['cache_write'])}")
        if self._context_window > 0:
            context_tokens = _context_usage_tokens(self._last_turn_usage, usage)
            pct = min(100.0, context_tokens / self._context_window * 100) if context_tokens else 0
            stats.append(f"{pct:.0f}%/{_format_tokens(self._context_window)}")
        return " | ".join((f"agent {group}", f"session {session_short}", model, " ".join(stats)))

    def _set_activity_status(self, status: str) -> None:
        """Set activity status and re-render status bar (matching OpenClaw)."""
        self._state["activity_status"] = status
        self._render_status()

    def _set_connection_status(self, status: str) -> None:
        """Set connection status and re-render status bar."""
        self._state["connection_status"] = status
        self._render_status()

    def _set_waiting(self, waiting: bool) -> None:
        self._waiting = waiting
        if waiting:
            self._waiting_tick = 0
            self._waiting_phrase = random.choice(WAITING_PHRASES)
            self._set_activity_status("waiting")
        else:
            self._waiting_phrase = None
            self._set_activity_status("idle")

    def _format_elapsed(self) -> str:
        if self._status_started is None:
            return "0s"
        total = max(0, int(time.monotonic() - self._status_started))
        if total < 60:
            return f"{total}s"
        m, s = divmod(total, 60)
        return f"{m}m {s}s"

    def _format_live_tokens(self) -> str:
        """Format THIS TURN's live token stats for the status bar."""
        # Estimate input for this turn from context sent
        turn_input = max(1, self._turn_input_chars // 4) if self._turn_input_chars else 0
        # Estimate output so far from streaming chars
        turn_output = max(1, self._streaming_output_chars // 4) if self._streaming_output_chars else 0

        parts: list[str] = []
        if turn_input:
            parts.append(f"↑{_format_tokens(turn_input)}")
        if turn_output:
            parts.append(f"↓{_format_tokens(turn_output)}")
        return " ".join(parts)

    def _update_busy_status(self) -> None:
        """Update the busy activity in the single status row."""
        if self._status_started is None:
            return
        activity = str(self._state.get("activity_status", ""))
        conn = str(self._state.get("connection_status", "connecting"))
        elapsed = self._format_elapsed()
        tokens = self._format_live_tokens()
        token_part = f" {tokens}" if tokens else ""
        if activity == "waiting":
            self._waiting_tick += 1
            phrase = self._waiting_phrase or _pick_waiting_phrase(self._waiting_tick)
            label = f"{phrase}… • {elapsed}{token_part}"
        else:
            label = f"{activity} • {elapsed}{token_part}"
        self._set_status_line(label, conn)

    # ── Todo checklist rendering ──────────────────────────────────────

    _TODO_ICONS = {
        "pending": "☐",
        "in_progress": "⧖",
        "completed": "☑",
    }

    def _render_todos(self, todos: list[dict[str, Any]]) -> None:
        """Render a todo checklist into the dedicated todo container."""
        if not todos:
            self._clear_todos()
            return

        self._last_todos = list(todos)
        lines: list[str] = []
        for item in todos:
            status = item.get("status", "pending")
            content = item.get("content", "")
            icon = self._TODO_ICONS.get(status, "☐")

            if status == "completed":
                lines.append(theme_dim(f"  {icon} {content}"))
            elif status == "in_progress":
                lines.append(theme_accent(f"  {icon} {content}"))
            else:
                lines.append(f"  {icon} {content}")

        display = "\n".join(lines)
        if self._todo_text is None:
            self._todo_text = Text(display, 0, 0)
            self._todo_container.add_child(self._todo_text)
        else:
            self._todo_text.set_text(display)
        self._refresh_work_summary()

    def _clear_todos(self) -> None:
        """Clear the todo checklist display."""
        if self._todo_text is not None:
            self._todo_container.clear()
            self._todo_text = None
        self._last_todos = []
        self._refresh_work_summary()

    def _finalize_todos(self) -> None:
        """Mark all todo items as completed on turn end (keep visible)."""
        if not self._last_todos:
            return
        completed = [
            {**item, "status": "completed"} for item in self._last_todos
        ]
        self._last_todos = completed
        self._render_todos(completed)

    # ── Goal widget rendering ──────────────────────────────────────────

    _GOAL_ICONS = {
        "pending": "☐",
        "in_progress": "⧖",
        "completed": "☑",
        "skipped": "☒",
    }

    def _render_goal(self, goal: dict[str, Any]) -> None:
        """Render a goal with task statuses into the dedicated goal container."""
        self._last_goal = dict(goal)

        title = goal.get("title", "")
        goal_id = goal.get("id", "")
        goal_status = goal.get("status", "active")
        tasks = goal.get("tasks", [])

        lines: list[str] = []
        # Title line
        if goal_status in ("completed", "abandoned"):
            lines.append(theme_dim(f"  {title} [{goal_id}] — {goal_status}"))
        else:
            lines.append(theme_accent(f"  {title} [{goal_id}]"))

        # Task lines
        for task in tasks:
            status = task.get("status", "pending")
            desc = task.get("description", "")
            icon = self._GOAL_ICONS.get(status, "☐")

            if status == "completed":
                lines.append(theme_dim(f"    {icon} {desc}"))
            elif status == "in_progress":
                lines.append(theme_accent(f"    {icon} {desc}"))
            elif status == "skipped":
                lines.append(theme_dim(f"    {icon} {desc}"))
            else:
                lines.append(f"    {icon} {desc}")

        display = "\n".join(lines)
        if self._goal_text is None:
            self._goal_text = Text(display, 0, 0)
            self._goal_container.add_child(self._goal_text)
        else:
            self._goal_text.set_text(display)

    def _clear_goal(self) -> None:
        """Clear the goal widget display."""
        if self._goal_text is not None:
            self._goal_container.clear()
            self._goal_text = None
        self._last_goal = None
        self._refresh_work_summary()

    def _finalize_goal(self) -> None:
        """On turn end, clear goal widget if completed/abandoned, otherwise keep."""
        if self._last_goal is None:
            return
        status = self._last_goal.get("status", "active")
        if status in ("completed", "abandoned"):
            self._clear_goal()

    def _refresh_work_summary(self) -> None:
        """Show bounded goal/todo progress without rendering full checklists."""
        lines: list[str] = []
        if self._last_goal is not None:
            tasks = self._last_goal.get("tasks", [])
            done = sum(1 for task in tasks if task.get("status") in {"completed", "skipped"})
            title = sanitize_terminal_text(str(self._last_goal.get("title", "goal"))).replace("\n", " ")
            lines.append(f"goal: {title} ({done}/{len(tasks)})")
        if self._last_todos:
            done = sum(1 for item in self._last_todos if item.get("status") == "completed")
            active = next(
                (sanitize_terminal_text(str(item.get("content", ""))).replace("\n", " ")
                 for item in self._last_todos if item.get("status") == "in_progress"),
                "",
            )
            suffix = f" • {active}" if active else ""
            lines.append(f"todos: {done}/{len(self._last_todos)}{suffix}")
        self._work_summary.set_text(theme_dim("\n".join(lines)) if lines else "")

    def _update_queue_summary(self) -> None:
        """Render queue count and next-message preview in the auxiliary slot."""
        self._render_status()
        if not self._pending_messages:
            self._layout.queue = None
            return
        preview = sanitize_terminal_text(self._pending_messages[0]).replace("\n", " ↵ ")
        self._queue_summary.set_text(theme_system(f"queued {len(self._pending_messages)} • next: {preview}"))
        self._layout.queue = self._queue_container

    # ── Header/footer (matching OpenClaw's updateHeader/updateFooter) ─

    def _update_header(self) -> None:
        """Append the startup identity to durable transcript once."""
        st = self._state
        session_short = str(st.get("session_id", "?"))[:12]
        text = (
            f"claw tui - {st.get('socket_url', '')} "
            f"- agent {st.get('group_id', 'main')} "
            f"- session {session_short}"
        )
        self._header.set_text(theme_header(text))
        if not self._banner_added:
            self._chat_log.add_child(self._header)
            self._banner_added = True

    def _update_footer(self) -> None:
        """Refresh metadata in the single status row."""
        self._render_status()

    # ── Input submission (matching OpenClaw's createEditorSubmitHandler) ─

    def _handle_submit(self, text: str) -> None:
        """Handle editor submit (matching OpenClaw's submit handler).

        Flow: clear editor → add to history → handle slash/message.
        """
        value = text.strip()
        if not value:
            return

        # Add to editor history (matching OpenClaw)
        self._editor.add_to_history(value)

        # Slash commands
        lower = value.lower()
        if lower in ("/quit", "/exit"):
            self._request_exit()
            return
        if lower == "/reset":
            self._send_queue.put({
                "type": "reset",
                "group_id": self._state.get("group_id", "main"),
            })
            self._set_waiting(True)
            return
        if lower == "/clear":
            self._chat_log.clear_all()
            self._tui.request_render()
            return
        if lower == "/status":
            self._send_queue.put({"type": "status"})
            self._set_waiting(True)
            return
        if value.startswith("/"):
            # Unknown slash command
            self._chat_log.add_system(f"Unknown command: {value}")
            self._tui.request_render()
            return

        if self._active_run_id is not None:
            if self._aborting_run_id == self._active_run_id:
                self._editor.set_text(value)
                self._tui.request_render()
                return
            self._pending_messages.append(value)
            self._update_queue_summary()
            self._tui.request_render()
            return

        # Regular message (matching OpenClaw's sendMessage flow)
        self._clear_todos()  # clear any finalized todos from previous turn
        self._clear_goal()   # clear goal widget from previous turn
        self._chat_log.add_user(value)
        self._active_run_id = f"run-{uuid.uuid4().hex}"
        self._active_text = value
        self._aborting_run_id = None
        self._set_waiting(True)
        self._streaming_output_chars = 0
        # Estimate input for this turn: all prior session context + this message
        self._turn_input_chars = self._usage["input"] * 4 + len(value)
        self._send_queue.put({
            "type": "message",
            "group_id": self._state.get("group_id", "main"),
            "content": value,
            "run_id": self._active_run_id,
        })

    def _dispatch_next_pending(self) -> None:
        if self._active_run_id is not None or not self._pending_messages:
            return
        next_message = self._pending_messages.popleft()
        self._update_queue_summary()
        self._handle_submit(next_message)

    # ── Per-frame polling (matching OpenClaw's event loop) ──

    def _poll(self) -> None:
        changed = False

        while True:
            try:
                msg = self._recv_queue.get_nowait()
                self._handle_response(msg)
                changed = True
            except queue.Empty:
                break

        if self._waiting and self._status_started is not None:
            previous_status = self._footer.text
            self._update_busy_status()
            changed = changed or self._footer.text != previous_status

        if changed:
            self._tui.request_render()

    # ── Response handling (matching OpenClaw's event handler patterns) ─

    def _replay_history_entries(self, entries: list[Any]) -> None:
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            role = entry.get("role")
            raw_content = entry.get("content", "")
            channel = entry.get("channel", "")
            parts = raw_content if self._history_format >= 2 and isinstance(raw_content, list) else []
            content = raw_content if isinstance(raw_content, str) else "".join(
                str(part.get("text", ""))
                for part in parts
                if isinstance(part, dict) and part.get("type") == "text"
            )
            if channel in ("goal_runner", "scheduler"):
                continue
            if role == "user" and _is_internal_prompt(content):
                continue
            if role == "user" and content:
                self._chat_log.add_user(content)
                self._editor.add_to_history(content)
            elif role == "assistant":
                thinking = "".join(
                    str(part.get("thinking", ""))
                    for part in parts
                    if isinstance(part, dict) and part.get("type") == "thinking"
                )
                if content or thinking:
                    self._chat_log.add_assistant(content, thinking=thinking)
                for part in parts:
                    if isinstance(part, dict) and part.get("type") == "toolCall":
                        call_id = str(part.get("id", ""))
                        self._history_tools[call_id] = self._chat_log.add_tool(
                            str(part.get("name", "tool")),
                            part.get("arguments") if isinstance(part.get("arguments"), dict) else None,
                            tool_call_id=call_id,
                        )
            elif role == "toolResult":
                call_id = str(entry.get("tool_call_id", ""))
                result = "".join(
                    str(part.get("text", ""))
                    for part in parts
                    if isinstance(part, dict) and part.get("type") == "text"
                )
                tool = self._history_tools.get(call_id)
                if tool is None:
                    tool = self._chat_log.add_tool(
                        str(entry.get("tool_name", "tool")),
                        None,
                        tool_call_id=call_id,
                    )
                tool.set_result(result, is_error=bool(entry.get("is_error", False)))

    def _handle_response(self, msg: Mapping[str, Any]) -> None:
        msg = thaw_event(msg)
        msg_type = msg.get("type")
        if msg_type in {"todo", "goal"} and msg.get("run_id"):
            run_id = str(msg["run_id"])
            if run_id != self._active_run_id or run_id == self._aborting_run_id:
                return

        if msg_type == "pong":
            # Use session ID from gateway if available (resume existing session),
            # otherwise create a new one
            gateway_session_id = msg.get("session_id")
            gateway_turn_count = msg.get("turn_count", 0)

            if gateway_session_id:
                self._state["session_id"] = gateway_session_id
            elif not self._state.get("session_id"):
                import uuid
                self._state["session_id"] = str(uuid.uuid4())

            # Pick up model info + context window from gateway
            if "model" in msg:
                self._state["model"] = msg["model"]
            if "context_window" in msg:
                self._context_window = msg["context_window"]

            # Load cumulative usage from session transcript
            gw_usage = msg.get("usage")
            if gw_usage:
                self._usage = {
                    "input": gw_usage.get("input", 0),
                    "output": gw_usage.get("output", 0),
                    "cache_read": gw_usage.get("cache_read", 0),
                    "cache_write": gw_usage.get("cache_write", 0),
                }

            self._state["connection_status"] = "connected"
            gid = self._state["group_id"]
            sid = self._state["session_id"][:12]

            # Only show session/history info on first connect (not reconnect)
            if not self._has_connected:
                self._has_connected = True
                self._chat_log.add_system(f"session agent:{gid}:{sid}")

                # Replay chat history from transcript (matching OpenClaw's
                # renderInitialMessages / renderSessionContext)
                history = msg.get("history", [])
                history_count = int(msg.get("history_count", len(history)))
                self._replaying_history = history_count > 0
                if history_count:
                    self._chat_log.add_system(
                        f"resumed session: {sid} ({gateway_turn_count} turns)"
                    )
                    self._history_format = int(msg.get("history_format", 1))
                    if history:
                        self._replay_history_entries(history)
                else:
                    self._chat_log.add_system(f"new session: {sid}")

            self._update_header()
            self._update_footer()
            self._set_connection_status("connected")
            self._set_activity_status("idle")
            return

        if msg_type == "history_chunk":
            entries = msg.get("entries")
            if self._replaying_history and isinstance(entries, list):
                self._replay_history_entries(entries)
            return

        if msg_type == "history_end":
            self._replaying_history = False
            self._history_tools.clear()
            return

        # Reconnect status update (internal, not from gateway)
        if msg_type == "_reconnect_status":
            attempt = msg.get("attempt", 1)
            delay = msg.get("delay", 1)
            self._set_connection_status("reconnecting")
            self._set_activity_status(
                f"reconnecting (attempt {attempt}, {delay:.0f}s)"
            )
            return

        # Successfully reconnected after disconnection
        if msg_type == "internal_reconnected":
            self._chat_log.add_system("reconnected to gateway")
            self._set_connection_status("connected")
            self._set_activity_status("idle")
            return

        if msg_type == "quit_ack":
            self._tui.stop()
            return

        # Todo checklist update (ephemeral, within-turn progress)
        if msg_type == "todo":
            todos = msg.get("todos", [])
            self._render_todos(todos)
            return

        # Goal widget update (persistent across turns)
        if msg_type == "goal":
            goal = msg.get("goal")
            if goal:
                self._render_goal(goal)
            return

        run_scoped = {
            "tool_call", "tool_update", "tool_result", "delta",
            "response", "final", "aborted", "abort_ack", "error",
        }
        if msg_type in run_scoped:
            run_id = str(msg.get("run_id", ""))
            if self._active_run_id is None or run_id != self._active_run_id:
                return
            if run_id == self._aborting_run_id:
                if msg_type in {"response", "final", "aborted", "error"}:
                    self._chat_log.drop_assistant(run_id)
                    self._active_run_id = None
                    self._active_text = None
                    self._aborting_run_id = None
                    self._set_waiting(False)
                    if msg_type == "aborted":
                        self._chat_log.add_system("run aborted")
                return

        if msg_type in ("busy", "queued"):
            run_id = str(msg.get("run_id", ""))
            if run_id == self._active_run_id:
                self._restore_pending_draft(run_id)
                self._active_run_id = None
                self._active_text = None
                self._aborting_run_id = None
                self._set_waiting(False)
                self._chat_log.add_system("group is busy; message restored to editor")
            return

        if msg_type == "run_ack":
            return

        if msg_type == "abort_ack":
            return

        if msg_type == "tool_call":
            self._chat_log.close_assistant(str(msg.get("run_id", "default")))
            self._chat_log.add_tool(
                str(msg.get("name", "tool")),
                msg.get("arguments") if isinstance(msg.get("arguments"), dict) else None,
                tool_call_id=str(msg.get("id", "")),
            )
            self._set_activity_status("running")
            return

        if msg_type == "tool_update":
            tool_call_id = str(msg.get("id", ""))
            tool = self._chat_log._tools.get(tool_call_id)
            if tool is not None:
                tool.set_streaming(str(msg.get("result", "")))
            return

        if msg_type == "tool_result":
            tool_call_id = str(msg.get("id", ""))
            tool = self._chat_log._tools.get(tool_call_id)
            if tool is not None:
                for image in msg.get("images", []):
                    if isinstance(image, dict):
                        tool.set_image(ImageContent(
                            data=str(image.get("data", "")),
                            mime_type=str(image.get("mime_type", "image/png")),
                        ))
            self._chat_log.finish_tool(
                str(msg.get("id", "")),
                str(msg.get("name", "tool")),
                str(msg.get("result", "")),
                is_error=bool(msg.get("is_error", False)),
            )
            self._set_activity_status("running")
            return

        # Streaming delta (matching OpenClaw's chat event: state=delta)
        if msg_type == "delta":
            run_id = msg.get("run_id", "default")
            content = msg.get("content", "")
            if not self._active_run_id:
                self._active_run_id = run_id
                self._streaming_output_chars = 0
            self._chat_log.update_assistant(content, run_id)
            # content is accumulated text so far — use its length directly
            self._streaming_output_chars = len(content)
            self._update_busy_status()
            self._set_activity_status("streaming")
            return

        # Final response (matching OpenClaw's chat event: state=final)
        if msg_type == "response" or msg_type == "final":
            run_id = msg.get("run_id", "default")
            content = msg.get("content", "")
            self._set_waiting(False)
            self._active_run_id = None
            self._active_text = None
            self._aborting_run_id = None
            self._streaming_output_chars = 0
            self._finalize_todos()
            # Clear goal widget only if goal is completed/abandoned;
            # active goals persist across turns
            self._finalize_goal()

            if content:
                # Non-streamed response — finalize with full content
                self._chat_log.finalize_assistant(content, run_id)
            else:
                # Streamed deltas already delivered the content — just
                # close the streaming run without appending a new component.
                self._chat_log.close_assistant(run_id)
                # If no existing run found, nothing to finalize — the
                # content was already rendered via deltas.

            # Accumulate per-turn usage into session totals
            turn_usage = msg.get("usage")
            if turn_usage:
                self._last_turn_usage = {
                    "input": turn_usage.get("input", 0),
                    "output": turn_usage.get("output", 0),
                    "cache_read": turn_usage.get("cache_read", 0),
                    "cache_write": turn_usage.get("cache_write", 0),
                }
                self._usage["input"] += turn_usage.get("input", 0)
                self._usage["output"] += turn_usage.get("output", 0)
                self._usage["cache_read"] += turn_usage.get("cache_read", 0)
                self._usage["cache_write"] += turn_usage.get("cache_write", 0)

            # Update session info if provided
            if "model" in msg:
                self._state["model"] = msg["model"]
            self._update_footer()
            self._dispatch_next_pending()
            return

        # Aborted (matching OpenClaw's chat event: state=aborted)
        if msg_type == "aborted":
            run_id = msg.get("run_id", "default")
            self._set_waiting(False)
            self._active_run_id = None
            self._active_text = None
            self._aborting_run_id = None
            self._clear_todos()
            self._chat_log.add_system("run aborted")
            self._chat_log.drop_assistant(run_id)
            return

        # Error (matching OpenClaw's chat event: state=error)
        if msg_type in ("error", "internal_error"):
            run_id = str(msg.get("run_id", "default"))
            if msg_type == "internal_error" and self._active_run_id is not None:
                run_id = self._active_run_id
                self._restore_pending_draft(run_id)
                self._active_text = None
                for tool in self._chat_log._tools.values():
                    tool.set_canceled()
                self._update_queue_summary()
            if msg_type == "error" and self._active_run_id == run_id:
                self._restore_pending_draft(run_id)
                self._active_text = None
            self._set_waiting(False)
            self._active_run_id = None
            self._aborting_run_id = None
            self._clear_todos()
            err = msg.get("message", msg.get("content", "Unknown error"))
            self._chat_log.add_system(theme_error(f"Error: {err}"))
            self._chat_log.drop_assistant(run_id)
            return

        if msg_type == "reset_ack":
            self._set_waiting(False)
            # Use session ID from gateway if provided
            gateway_session_id = msg.get("session_id")
            if gateway_session_id:
                self._state["session_id"] = gateway_session_id
            else:
                import uuid
                self._state["session_id"] = str(uuid.uuid4())
            self._chat_log.clear_all()
            self._usage = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
            self._last_turn_usage = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
            sid = self._state["session_id"][:12]
            self._chat_log.add_system(f"new session: {sid}")
            self._update_header()
            self._update_footer()
            return

        if msg_type == "status":
            self._set_waiting(False)
            pid = msg.get("pid", "?")
            groups = ", ".join(msg.get("groups", []))
            self._chat_log.add_system(
                f"Gateway running (PID: {pid}) — groups: {groups}"
            )
            return

    # ── Socket I/O thread with auto-reconnect ──

    # Reconnect settings (matching OpenClaw's auto-retry pattern)
    _RECONNECT_BASE_DELAY = 1.0   # seconds
    _RECONNECT_MAX_DELAY = 30.0   # cap
    _RECONNECT_MAX_RETRIES = 0    # 0 = unlimited

    def _io_loop(self) -> None:
        try:
            asyncio.run(self._io_loop_async())
        except asyncio.CancelledError:
            if not self._exit_requested:
                self._recv_queue.put({
                    "type": "internal_error",
                    "message": "Gateway connection task was cancelled",
                })
        except Exception as e:
            # Never let IO thread exceptions spill into the TUI terminal
            self._recv_queue.put({
                "type": "internal_error",
                "message": f"IO thread error: {e}",
            })

    async def _io_loop_async(self) -> None:
        """Connection loop with exponential backoff reconnect.

        Matches OpenClaw's auto-retry pattern:
        - Detects disconnection when read returns empty or raises
        - Exponential backoff: base_delay * 2^(attempt-1), capped
        - Updates TUI status during reconnect attempts
        - Re-sends ping on reconnect to sync session state
        """
        attempt = 0

        while not self._exit_requested:
            sock_path = Path(self.socket_path)

            # ── Connect ──
            if not sock_path.exists():
                if attempt == 0:
                    self._recv_queue.put({
                        "type": "internal_error",
                        "message": f"Socket not found: {sock_path}. Is gateway running?",
                    })
                else:
                    self._set_reconnect_status(attempt)
                attempt += 1
                delay = self._reconnect_delay(attempt)
                await asyncio.sleep(delay)
                continue

            try:
                reader, writer = await asyncio.open_unix_connection(
                    str(sock_path),
                    limit=4 * 1024 * 1024,
                )
            except Exception as e:
                if attempt == 0:
                    self._recv_queue.put({
                        "type": "internal_error",
                        "message": f"Connection error: {e}",
                    })
                else:
                    self._set_reconnect_status(attempt)
                attempt += 1
                delay = self._reconnect_delay(attempt)
                await asyncio.sleep(delay)
                continue

            # ── Connected — reset attempt counter ──
            if attempt > 0:
                # We just reconnected after a disconnection
                self._recv_queue.put({
                    "type": "internal_reconnected",
                })
            attempt = 0

            # Re-send ping to sync session state
            self._send_queue.put({"type": "ping", "group_id": self.group_id})

            # ── Run read/write until disconnect ──
            disconnected = await self._run_connection(reader, writer)

            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

            if not disconnected or self._exit_requested:
                break

            # Connection lost — show status and retry
            attempt = 1
            self._set_reconnect_status(attempt)
            delay = self._reconnect_delay(attempt)
            await asyncio.sleep(delay)

    def _reconnect_delay(self, attempt: int) -> float:
        """Exponential backoff delay (matching OpenClaw's baseDelayMs * 2^(attempt-1))."""
        delay = self._RECONNECT_BASE_DELAY * (2 ** (attempt - 1))
        return float(min(delay, self._RECONNECT_MAX_DELAY))

    def _set_reconnect_status(self, attempt: int) -> None:
        """Update UI status during reconnect attempts."""
        delay = self._reconnect_delay(attempt)
        self._state["connection_status"] = "reconnecting"
        self._state["activity_status"] = f"reconnecting (attempt {attempt}, {delay:.0f}s)"
        self._recv_queue.put({
            "type": "_reconnect_status",
            "attempt": attempt,
            "delay": delay,
        })

    async def _run_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> bool:
        """Run read/write loops on an established connection.

        Returns True if disconnected (should retry), False if clean exit.
        """
        disconnected = False

        async def _read() -> None:
            nonlocal disconnected
            while not self._exit_requested:
                try:
                    line = await reader.readline()
                    if not line:
                        disconnected = True
                        self._recv_queue.put({
                            "type": "internal_error",
                            "message": "Gateway closed connection. Reconnecting...",
                        })
                        break
                    self._recv_queue.put(
                        json.loads(line.decode("utf-8").strip())
                    )
                except json.JSONDecodeError:
                    pass
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    disconnected = True
                    self._recv_queue.put({
                        "type": "internal_error",
                        "message": f"Read error: {e}. Reconnecting...",
                    })
                    break

        async def _write() -> None:
            while not self._exit_requested:
                try:
                    req = self._send_queue.get_nowait()
                    if req.get("type") == "quit":
                        self._recv_queue.put({"type": "quit_ack"})
                        break
                    if "group_id" not in req:
                        req["group_id"] = self.group_id
                    writer.write(
                        (json.dumps(req) + "\n").encode("utf-8")
                    )
                    await writer.drain()
                except queue.Empty:
                    await asyncio.sleep(0.05)
                except Exception as e:
                    self._recv_queue.put({
                        "type": "internal_error",
                        "message": f"Write error: {e}. Reconnecting...",
                    })
                    break

        read_task = asyncio.create_task(_read())
        write_task = asyncio.create_task(_write())
        await asyncio.wait(
            [read_task, write_task], return_when=asyncio.FIRST_COMPLETED
        )

        # Cancel the remaining task
        for task in [read_task, write_task]:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        return disconnected


def run_tui_app(
    socket_path: str, group_id: str = "main", *, model: str = ""
) -> None:
    app = ChatApp(socket_path, group_id)
    if model:
        app._state["model"] = model
    app.run()
