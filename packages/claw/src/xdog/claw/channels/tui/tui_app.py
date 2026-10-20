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
import hashlib
import json
import logging
import os
import queue
import random
import re
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from xdog.agent.extensions.artifact_export import ArtifactLoader, export_snapshot
from xdog.agent.extensions.command_result import ExtensionCommandResult
from xdog.agent.extensions.dialogs import validate_dialog
from xdog.agent.extensions.runtime import validate_snapshot
from xdog.agent.mcp.context import McpDocument, McpEntry
from xdog.agent.mcp.oauth import normalize_auth_reply
from xdog.agent.supervision_config import ChildConfig, ChildPresets, parse_child_options
from xdog.ai.types import ImageContent
from xdog.claw.channels.tui.history_panel import HistoryPanel
from xdog.claw.channels.tui.mcp_data import McpDataReceiver
from xdog.claw.channels.tui.source_reader import SourceReceiver
from xdog.claw.channels.tui.submission import TERMINAL, NativeSubmission, valid_namespace, validate_receipt
from xdog.claw.config import get_config_dir, get_data_dir
from xdog.claw.core.command_names import SLASH_COMMANDS
from xdog.tui.attachments import Attachment, AttachmentComposer
from xdog.tui.child_browser import child_entries
from xdog.tui.child_drafts import ChildDraft
from xdog.tui.components.attachment_panel import AttachmentPanel
from xdog.tui.components.child_panel import ChildPanel
from xdog.tui.components.details import set_details_expanded
from xdog.tui.components.details_panel import DetailRecord, DetailsPanel, streaming_preview
from xdog.tui.components.extension_command import ExtensionCommand
from xdog.tui.components.extension_dialog import ExtensionDialog
from xdog.tui.components.extension_stack import ExtensionDialogStack
from xdog.tui.components.extension_view import ExtensionView
from xdog.tui.components.image import Image
from xdog.tui.components.inline_layout import CompactText, InlineLayout
from xdog.tui.components.input_panel import InputPanel, SlashSelectList
from xdog.tui.components.markdown import DefaultTextStyle, MarkdownTheme
from xdog.tui.components.mcp_panel import McpPanel
from xdog.tui.components.messages import AssistantMessages, ThinkingMessage, Transcript
from xdog.tui.components.messages import UserMessage as SharedUserMessage
from xdog.tui.components.output_panel import OutputPanel
from xdog.tui.components.panel import PanelTheme
from xdog.tui.components.parent_draft_panel import ParentDraftPanel
from xdog.tui.components.spacer import Spacer
from xdog.tui.components.status_line import StatusLine
from xdog.tui.components.text import Text
from xdog.tui.components.tool_inventory import ToolInventoryPanel, inventory_records
from xdog.tui.components.tool_message import ToolMessage as ToolMessageView
from xdog.tui.components.transcript_panel import TranscriptPanel
from xdog.tui.event_queue import EventQueue, thaw_event
from xdog.tui.extension_shortcuts import ExtensionShortcuts
from xdog.tui.external_editor import edit_draft
from xdog.tui.file_completion import WorkspaceFileCompletionProvider
from xdog.tui.keys import KeyEvent, KeyEventType
from xdog.tui.navigation import NavigationEntry, NavigationFeed, NavigationSnapshot
from xdog.tui.parent_draft_presenter import ParentDraftPresenter
from xdog.tui.parent_drafts import ParentDrafts
from xdog.tui.prompt_keymap import PromptKeymap
from xdog.tui.queue_controls import QUEUE_HELP, LocalQueueIds, QueueEntry, parse_queue_command
from xdog.tui.terminal_logging import capture_terminal_logs
from xdog.tui.transcript_actions import TranscriptActions
from xdog.tui.transcript_browser import TranscriptRecord
from xdog.tui.transcript_transfer import TransferOptions, parse_transfer
from xdog.tui.tui import TUI, Component, Container
from xdog.tui.url_open import ReviewedUrlOpener
from xdog.tui.utils import sanitize_terminal_text

logger = logging.getLogger(__name__)

# Native panel operations only; protocol completion remains available to backend callers.
_MCP_PANEL_ACTIONS = frozenset({
    "list", "connect", "disconnect", "refresh", "resources", "templates", "prompts", "read", "prompt",
})

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

def _panel_theme() -> PanelTheme:
    if ('NO_COLOR' in os.environ or os.environ.get('TERM') == 'dumb'
            or os.environ.get('XDOG_TUI_THEME', '').lower() == 'plain'):
        return PanelTheme()
    return PanelTheme(fg=theme_fg, header=theme_header, accent=theme_accent, dim=theme_dim,
                      border=theme_border, error=theme_error, success=_fg(PALETTE['success']), bold=_bold,
                      inverse=lambda text: f"\x1b[7m{text}\x1b[27m")


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
    def transcript_body(self) -> str:
        return f"Arguments:\n{json.dumps(self._arguments, ensure_ascii=False)}\n\nOutput:\n{self._result}"

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

    def retain_assistant(self, run_id: str = "default") -> None:
        """Retire the streaming owner but retain already displayed partial data."""
        self._streaming_runs.pop(run_id, None)

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
            elif isinstance(child, ExtensionCommand) and child.detail_body:
                records.append(DetailRecord(child.detail_title, child.detail_body, "command"))
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
        self._native_submission: NativeSubmission | None = None
        self._native_namespace = ''
        self._native_supported = False
        self._native_query: tuple[str, int] | None = None
        self._pending_messages: deque[str] = deque()
        self._pending_ids = LocalQueueIds()
        self._transformed_inputs: dict[str, str] = {}
        self._routed_inputs: dict[str, QueueEntry] = {}
        self._input_requests: dict[str, tuple[str, str, str, str]] = {}
        self._input_sequence = 0
        self._input_controls_supported = False
        self._last_ctrl_c_at: float = 0.0
        self._exit_requested = False
        self._draft_quit_failures: tuple[int, ...] | None = None
        self._has_connected = False
        self._connection_ready = False
        self._connection_generation = 0
        self._history_loading = False
        self._pending_command: str | None = None
        self._access_supported = False
        self._access_pending: tuple[str, str, str, float] | None = None
        self._access_read_only: bool | None = None
        self._access_revision = -1
        self._access_session = ''
        # Transport ownership is separate from UI state. Only immutable events
        # cross back to the UI; these fields belong to the I/O worker.
        self._io_generation = 0
        self._io_session_ready = False
        self._io_stop = threading.Event()
        self._io_lock = threading.Lock()
        self._io_loop_ref: asyncio.AbstractEventLoop | None = None
        self._io_task: asyncio.Task[None] | None = None
        self._details_expanded = False
        self._details_open = False
        self._navigation_panel: HistoryPanel | None = None
        self._navigation_provider: NavigationFeed | None = None
        self._navigation_requests: dict[str, tuple[NavigationFeed | None, int, str]] = {}
        self._session_mutation_id: str | None = None
        self._session_cancel_supported = False
        self._session_controls_supported = False
        self._session_rename_supported = False
        self._name_read_supported = False
        self._name_read: tuple[str, str, str, int] | None = None
        self._gateway_session_id = ""
        self._session_context_stale = False
        self._extension_snapshot: dict[str, Any] = {}
        self._extension_dialog_queue: list[dict[str, Any]] = []
        self._extension_stack = ExtensionDialogStack()
        self._extension_dialog_panel: ExtensionDialog | None = None
        self._extension_supported = False
        self._extension_models_supported = False
        self._native_models_supported = False
        self._reasoning_supported = False
        self._reasoning_pending: str | None = None
        self._extension_model_pending: str | None = None
        self._extension_shortcuts_supported = False
        self._extension_session_supported = False
        self._extension_session_inspection_supported = False
        self._extension_provider_auth_supported = False
        self._extension_provider_auth_generation = 0
        self._extension_auth_owner: tuple[str, str, int] | None = None
        self._extension_command_catalog_supported = False
        self._extension_command_catalog_generation = 0
        self._argument_supported = False
        self._argument_requests: dict[str, Any] = {}
        self._argument_scopes: dict[str, tuple[str, str, int]] = {}
        self._argument_cancel_supported = False
        self._argument_context_supported = False
        self._argument_context_requests: set[str] = set()
        self._extension_cancel_supported = False
        self._extension_cancel_scope: tuple[str, str] | None = None
        self._extension_command_results_supported = False
        self._extension_pending: tuple[str, str] | None = None
        self._extension_command_owner: tuple[str, str] | None = None
        self._child_panel: ChildPanel | None = None
        self._child_context = ""
        self._child_target = ""
        self._child_ui_requests: set[str] = set()
        self._child_presets = ChildPresets()
        self._child_presets_supported = False
        self._child_model_supported = False
        self._child_config_supported = False
        self._child_images_supported = False
        self._child_queue_owners: dict[str, tuple[str, str, str, str, str]] = {}
        self._child_drafts: dict[str, dict[tuple[str, str], ChildDraft]] = {}
        self._parent_drafts = ParentDrafts(lambda: get_data_dir() / 'saved-parent-drafts', self._parent_draft_snapshot,
                                           self._mcp_context_key, lambda text: self._chat_log.add_system(text),
            self._parent_draft_capacity, create_parent=True)
        self._parent_draft_ui = ParentDraftPresenter(
            self._parent_drafts, available=self._parent_draft_ui_available, prepare=self._prepare_parent_draft_view,
            mount=self._mount_parent_draft_view, unmount=self._unmount_parent_draft_view,
            restore=self._restore_parent_draft_composer, changed=self._parent_draft_changed)
        self._child_source_supported = False
        self._child_source_pending: tuple[str, str, str] | None = None
        self._child_source_deadline = 0.0
        self._children_supported = False
        self._child_queues_supported = False
        self._children_pending: dict[str, tuple[str, str]] = {}
        self._mcp_refresh_supported = False
        self._mcp_credentials_supported = False
        self._mcp_auth_supported = False
        self._mcp_auth_group = ""
        self._mcp_auth_session = ""
        self._mcp_auth_lease = ""
        self._mcp_auth_review: dict[str, Any] | None = None
        self._mcp_auth_requests: set[str] = set()
        self._mcp_auth_copying = False
        self._reviewed_url = ReviewedUrlOpener()
        self._mcp_panel: McpPanel | None = None
        self._mcp_document: McpDocument | None = None
        self._mcp_sequence = 0
        self._mcp_active: str | None = None
        self._mcp_action = ""
        self._mcp_owner_context = ""
        self._mcp_thread: threading.Thread | None = None
        self._transcript_panel: TranscriptPanel | None = None
        self._pending_output: TransferOptions | None = None
        self._mcp_supported = False
        self._mcp_browser_owner_supported = False
        self._mcp_owner_request: tuple[str, str, str] | None = None
        self._mcp_data_supported = False
        self._mcp_content_types_supported = False
        self._mcp_requests: set[str] = set()
        self._compaction_supported = False
        self._compact_pending: tuple[str, str] | None = None
        self._compact_deadline = 0.0
        self._skills_supported = False
        self._skills_request = ""
        self._skills_session = ""
        self._skills_deadline = 0.0
        self._tool_inventory_supported = False
        self._tool_inventory_selection_supported = False
        self._tools_request = ''
        self._tools_session = ''
        self._tools_deadline = 0.0
        self._saved_timeline_supported = False
        self._saved_source_supported = False
        self._saved_scope: tuple[str, str] | None = None
        self._source_supported = False
        self._source_request_id: str | None = None
        self._source_session_id = ""
        self._source_records: tuple[TranscriptRecord, ...] | None = None
        self._source_deadline = 0.0
        self._attachment_panel: AttachmentPanel | None = None
        self._local_images: dict[str, tuple[Attachment, ...]] = {}
        self._routed_images: dict[str, tuple[Attachment, ...]] = {}
        self._active_images: tuple[Attachment, ...] = ()
        self._attachment_transport = False
        self._history_format = 1
        self._history_tools: dict[str, ToolMessage] = {}
        self._acp_seen_session = ""
        self._acp_seen: set[str] = set()
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
        self._output_epoch = 0
        self._artifact_loader = ArtifactLoader(self._recv_queue.put)
        self._outputs = TranscriptActions(self._recv_queue.put, self._chat_log.add_system,
                                          lambda: f"{self._connection_generation}:{self._gateway_session_id}:"
                                                  f"{self._output_epoch}:child:"
                                                  + (self._child_panel.child_id
                                                     if self._child_panel is not None else ""),
                                          lambda: self._tui.request_render())

        self._todo_container = Container()
        self._work_container = Container()
        self._work_summary = CompactText("", 0, 0)
        self._work_container.add_child(self._work_summary)
        self._extension_view = ExtensionView(lambda: self._extension_snapshot)
        self._work_container.add_child(self._extension_view)
        self._status_container = Container()
        self._footer = StatusLine(theme_dim)
        self._keymap = PromptKeymap.load(get_config_dir() / "keybindings.json")
        self._editor = CustomEditor()
        original_commands = self._editor._command_provider
        self._editor._command_provider = lambda: {**original_commands(),
            **self._extension_snapshot.get("commands", {})}
        self._editor.set_keymap(self._keymap)
        from xdog.tui.argument_completion import ArgumentCompletionFeed
        self._argument_feed = ArgumentCompletionFeed(self._mcp_context_key,
            lambda: self._extension_snapshot.get('command_completions', {})
                if self._argument_supported and self._editor.focused
                and self._connection_ready and self._active_run_id is None and self._extension_pending is None
                and self._pending_command is None and not self._exit_requested else {},
            self._request_arguments, self._cancel_arguments)
        self._editor.set_argument_completion_provider(self._argument_feed)
        self._composer = AttachmentComposer(self._recv_queue.put, self._attachment_changed,
                                            self._chat_log.add_system, self._editor.handle_paste,
                                            self._open_attachment_preview)
        self._file_completions: WorkspaceFileCompletionProvider | None = None
        self._queue_container = Container()
        self._queue_summary = CompactText("", 0, 0)
        self._queue_container.add_child(self._queue_summary)
        self._details_panel = DetailsPanel(self._detail_records, max_rows=8)
        self._hints = CompactText("", 0, 0)
        self._update_hints()
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
            panel_theme=_panel_theme(),
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
        if self._parent_draft_panel is not None and self._tui.has_overlay():
            return None  # Native overlays/approval own input, not the underlying recovery view.
        action = self._keymap.resolve(event)
        if action == 'save_draft':
            if (event.event_type is KeyEventType.PRESS and self._editor.focused
                    and self._parent_draft_ui_available()):
                self._parent_draft_command('save')
            return {'consume': True}
        if (action is None or action.startswith("ext-")) and self._extension_shortcut_input(event):
            self._tui.request_render()
            return {"consume": True}

        if self._extension_dialog_panel is not None and self._outputs.panel is None:
            self._extension_dialog_panel.handle_input(event)
            self._tui.request_render()
            return {"consume": True}
        if self._parent_draft_panel is not None and self._outputs.panel is None and action != 'suspend':
            self._parent_draft_panel.handle_input(event)
            self._tui.request_render()
            return {'consume': True}
        if self._child_panel is not None and self._outputs.panel is None and action != "suspend":
            if action == "fullscreen":
                self._child_fullscreen()
            elif action == 'paste_clipboard':
                self._child_panel.paste_clipboard()
            elif action == "copy":
                preview = self._child_panel.preview
                self._child_output("copy", "selection" if preview is not None and preview.selection else "response")
            else:
                self._child_panel.handle_input(event)
            self._tui.request_render()
            return {"consume": True}
        if self._mcp_panel is not None and self._outputs.panel is None:
            self._mcp_panel.handle_input(event)
            self._tui.request_render()
            return {"consume": True}
        if self._outputs.panel is not None:
            self._outputs.panel.handle_input(event)
            self._tui.request_render()
            return {"consume": True}
        if action == "copy":
            if event.event_type == KeyEventType.PRESS:
                scope = ("selection" if self._transcript_panel is not None and self._transcript_panel.selection
                         else "response")
                self._output_command("copy", scope)
            return {"consume": True}
        if action in {"transcript", "fullscreen"}:
            if event.event_type == KeyEventType.PRESS:
                if action == "fullscreen":
                    self._open_transcript()
                    self._toggle_transcript_fullscreen()
                elif self._transcript_panel is not None:
                    self._close_transcript()
                else:
                    self._open_transcript()
            return {"consume": True}
        if self._transcript_panel is not None:
            if action in {"details", "sessions", "tree"}:
                self._close_transcript()
            elif action != "suspend":
                self._transcript_panel.handle_input(event)
                self._tui.request_render()
                return {"consume": True}
        if action == "paste_clipboard":
            if self._editor.focused and event.event_type == KeyEventType.PRESS:
                root = self._file_completions.root if self._file_completions is not None else Path.cwd()
                self._composer.command("paste", "", root)
            return {"consume": True}
        if self._attachment_panel is not None and action not in {"suspend", "details", "sessions", "tree"}:
            self._attachment_panel.handle_input(event)
            self._tui.request_render()
            return {"consume": True}
        if action in {"steer", "follow_up", "queue"}:
            if event.event_type == KeyEventType.PRESS:
                if action == "queue":
                    self._queue_command("")
                elif self._editor.focused:
                    content = self._editor.get_text().strip()
                    if content or self._composer.items:
                        self._editor.set_text("")
                        self._send_runtime_input(action, content)
            return {"consume": True}
        if action in {"suspend", "details", "external_editor", "bindings", "sessions", "tree"} and (
            event.event_type != KeyEventType.PRESS
        ):
            return {"consume": True}
        if action in {"sessions", "tree"}:
            self._open_navigation("sessions" if action == "sessions" else f"tree|{self._state['session_id']}|")
            return {"consume": True}
        if action == "bindings":
            self._show_keybindings()
            return {"consume": True}
        if action == "external_editor":
            self._open_external_editor()
            return {"consume": True}
        if action == "suspend":
            if not self._tui.suspend():
                self._set_activity_status("suspend is not supported on this platform")
            return {"consume": True}
        if self._navigation_panel is not None and action != "details":
            if action == "interrupt":
                self._close_navigation()
            else:
                self._navigation_panel.handle_input(event)
                self._tui.request_render()
            return {"consume": True}
        if self._editor.focused and self._editor.autocomplete_active and action != "details":
            return None
        if self._details_open:
            if event.matches("escape") or action == "interrupt":
                self._close_details()
                return {"consume": True}
            if any(event.matches(key) for key in ("left", "right", "pageup", "pagedown", "home", "end")):
                self._details_panel.handle_input(event)
                self._tui.request_render()
                return {"consume": True}
        if event.matches("escape") and self._session_mutation_id is not None:
            self._cancel_session_navigation()
            return {"consume": True}
        if event.matches("escape") and self._compact_pending is not None:
            self._cancel_compaction()
            return {"consume": True}
        if event.matches("escape") and self._active_run_id is not None:
            self._handle_escape()
            return {"consume": True}
        if action != "details":
            return None
        if self._attachment_panel is not None:
            self._close_attachment_preview()
        if self._navigation_panel is not None:
            self._close_navigation()
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

    def _effective_extension_shortcuts(self) -> ExtensionShortcuts:
        rows = self._extension_snapshot.get("shortcuts", [])
        effective = ExtensionShortcuts(rows if isinstance(rows, list) else [], self._keymap)
        if not self._extension_shortcuts_supported:
            effective.blocked.extend((row["command"], "updated shortcut gateway required") for row in effective.active)
            effective.active = []
        return effective

    def _extension_shortcut_input(self, event: KeyEvent) -> bool:
        if (self._exit_requested or not self._editor.focused or self._editor.autocomplete_active
                or self._tui.has_overlay() or self._outputs.pending or self._extension_dialog_panel is not None
                or self._navigation_panel is not None or self._transcript_panel is not None
                or self._details_open or self._child_panel is not None
                 is not None or self._mcp_panel is not None or self._attachment_panel is not None):
            return False
        if (self._extension_pending is not None or self._active_run_id is not None or self._waiting
                or not self._connection_ready or not self._extension_shortcuts_supported):
            return False
        command = self._effective_extension_shortcuts().command(event)
        if command is None:
            return False
        self._extension_command(command, "")
        return True

    def _show_keybindings(self) -> None:
        self._chat_log.add_system(self._keymap.help_text() + "\n\n" + self._effective_extension_shortcuts().help_text())
        self._tui.request_render()

    def _open_external_editor(self) -> None:
        if self._parent_draft_panel is not None:
            self._close_parent_drafts()
        if self._exit_requested:
            return
        if not self._editor.focused or self._tui.has_overlay():
            self._chat_log.add_system("Return to the prompt before opening the external editor; draft preserved")
            self._tui.request_render()
            return
        original = self._editor.get_text()

        def edit() -> None:
            result = edit_draft(
                original, cwd=self._file_completions.root if self._file_completions is not None else None,
                stopped=lambda: self._exit_requested or self._tui.stopped,
            )
            if self._exit_requested:
                return
            if result.text is not None and result.text != original:
                self._editor.set_text(result.text)
            self._chat_log.add_system(result.message)
            self._tui.request_render()

        if not self._tui.run_outside_terminal(edit):
            self._chat_log.add_system("External editor unavailable while terminal handoff is pending; draft preserved")
            self._tui.request_render()

    def _attachment_changed(self) -> None:
        count = len(self._composer.items)
        self._editor.set_attachment_summary(f"Images: {count} · /attachments inspect/remove/preview" if count else "")
        self._tui.request_render()

    def _retained_image_bytes(self) -> int:
        items = {item.id: item for group in (*self._local_images.values(), *self._routed_images.values(),
                                           self._active_images,
                                           self._native_submission.images if self._native_submission else ())
                 for item in group}
        return sum(len(item.data) for item in items.values()) + self._child_draft_bytes() + self._parent_saved_bytes()

    def _child_draft_bytes(self) -> int:
        items = {item.id: item for scope, cache in self._child_drafts.items()
                 for draft in cache.values() for item in draft.images}
        if self._child_panel is not None and self._child_panel.composer is not None:
            items.update(self._child_panel.composer.items)
        return sum(len(item.data) for item in items.values())

    def _child_retained_bytes(self) -> int:
        current = (self._child_panel.composer.bytes if self._child_panel is not None
                   and self._child_panel.composer is not None else 0)
        return max(0, self._retained_image_bytes() + self._composer.bytes - current)

    def _validate_draft_images(self, images: tuple[Attachment, ...]) -> bool:
        if not images:
            return True
        limits = self._state.get("image_limits", {})
        # Treat malformed advertised limits as unsupported, not an exception in
        # the terminal's input callback. Never detach a draft on an invalid pong.
        valid_limits = isinstance(limits, dict)
        count = limits.get("count", 4) if valid_limits else None
        size = limits.get("bytes", 10 * 1024 * 1024) if valid_limits else None
        types = limits.get("types", ()) if valid_limits else None
        if (type(count) is not int or count < 0 or type(size) is not int or size < 0
                or not isinstance(types, (list, tuple)) or any(not isinstance(kind, str) for kind in types)):
            self._chat_log.add_system("Invalid gateway image limits; images/draft remain editable")
            self._tui.request_render()
            return False
        if (not self._attachment_transport or self._state.get("image_input") is not True
                or len(images) > min(4, count)
                or any(len(item.data) > min(10 * 1024 * 1024, size)
                       or types and item.mime_type not in types for item in images)):
            self._chat_log.add_system("Gateway/model does not support these image inputs; images/draft remain editable")
            self._tui.request_render()
            return False
        return True

    def _open_children(self, target: str = "") -> None:
        if self._parent_draft_panel is not None:
            self._close_parent_drafts()
        if self._outputs.panel is not None or self._extension_dialog_panel is not None or self._tui.has_overlay():
            return
        if self._child_panel is not None:
            if target:
                self._child_panel.inspect(target)
            return
        for panel, close in ((self._mcp_panel, self._close_mcp),
                             (self._transcript_panel, self._close_transcript),
                             (self._navigation_panel, self._close_navigation)):
            if panel is not None:
                close()
        if self._details_open:
            self._close_details()
        self._child_context = self._mcp_context_key()
        self._child_drafts = {self._child_context: self._child_drafts.get(self._child_context, {})}
        self._child_target = target
        self._child_panel = ChildPanel(self._child_send, self._child_read, self._close_children,
                                       self._child_output, self._child_fullscreen,
                                       configure=lambda value: self._child_presets.resolve(value).to_dict(),
                                       configured_start=self._child_configured_start,
                                       image_send=self._child_image_send,
                                       root=Path(str(self._state.get('workspace_dir', '.'))),
                                       drafts=self._child_drafts.setdefault(self._child_context, {}),
                                       retained_bytes=self._child_retained_bytes,
)
        self._child_panel.queue_enabled = self._child_queues_supported
        self._layout.details = self._child_panel
        self._tui.set_focus(self._child_panel)
        self._child_panel.request_list()
        if self._child_presets_supported:
            self._child_send("presets", "", "", "read-only")
        else:
            self._child_panel.publish_presets((),
                                             "Updated preset gateway required; custom child settings still available")
        self._update_hints()
        self._tui.request_render()

    def _parent_draft_ui_available(self) -> bool:
        return (not self._exit_requested and not self._tui.has_overlay()
                and self._extension_dialog_panel is None and not self._extension_dialog_queue
                and self._outputs.panel is None and not self._outputs.pending)

    def _parent_saved_bytes(self) -> int:
        return sum(len(image.data) for row in self._parent_drafts.saved.values() for image in row.draft.images)

    def _parent_draft_capacity(self) -> int:
        # Existing recovery records are replaced; unrelated active/local owners stay counted.
        return max(0, 40 * 1024 * 1024 - self._retained_image_bytes()
                   + self._parent_saved_bytes() - self._composer.bytes)

    def _parent_draft_snapshot(self) -> ChildDraft:
        return ChildDraft(self._editor.get_text(), images=tuple(self._composer.items.values()),
                          cursor=self._editor._cursor)

    @property
    def _parent_draft_panel(self) -> ParentDraftPanel | None:
        return self._parent_draft_ui.panel

    def _parent_view_alive(self, panel: ParentDraftPanel, lease: int) -> bool:
        return self._parent_draft_ui.alive(panel, lease)

    def _close_parent_drafts(self) -> None:
        self._parent_draft_ui.close()

    def _attach_parent_drafts(self, panel: ParentDraftPanel) -> None:
        self._parent_draft_ui.attach(panel)

    def _begin_parent_drafts(self) -> None:
        self._parent_draft_ui.begin()

    def _parent_draft_command(self, args: str) -> None:
        self._parent_draft_ui.command(args)

    def _show_parent_save(self) -> None:
        self._parent_draft_ui.show_save()

    def _show_parent_drafts(self) -> None:
        self._parent_draft_ui.show_restore()

    def _poll_parent_drafts(self) -> None:
        self._parent_draft_ui.poll()

    def _prepare_parent_draft_view(self) -> None:
        # These are native view transitions, not a child-browser dependency.
        for panel, close in ((self._child_panel, self._close_children),
                             (self._mcp_panel, self._close_mcp),
                             (self._transcript_panel, self._close_transcript),
                             (self._attachment_panel, self._close_attachment_preview),
                             (self._navigation_panel, self._close_navigation)):
            if panel is not None:
                close()
        if self._details_open:
            self._close_details()


    def _mount_parent_draft_view(self, panel: ParentDraftPanel) -> None:
        self._layout.details = panel
        self._tui.set_focus(panel)

    def _unmount_parent_draft_view(self, panel: ParentDraftPanel) -> None:
        if self._tui._overlay_base_focus is panel:
            self._tui._overlay_base_focus = self._editor
        if self._layout.details is panel:
            self._layout.details = None
        if self._tui._focused is panel:
            self._tui.set_focus(self._editor)

    def _restore_parent_draft_composer(self, draft: ChildDraft) -> None:
        retained = self._retained_image_bytes() - self._parent_saved_bytes() + self._composer.bytes
        if retained + sum(len(item.data) for item in draft.images) > 40 * 1024 * 1024:
            raise ValueError('Parent recovery exceeds local retention; existing draft preserved')
        # Persisted cursor/config/access never become restore authority.
        self._editor.set_text(draft.text)
        self._composer.restore(draft.images)

    def _parent_draft_changed(self) -> None:
        self._update_hints()
        self._tui.request_render()


    def _child_image_send(self, action: str, child: str, text: str, access: str,
                          config: dict[str, Any], images: tuple[Attachment, ...]) -> None:
        if (not self._child_images_supported or not self._child_config_supported
                or (config.get('model') is not None or config.get('thinking') is not None)
                and not self._child_model_supported or config.get('preset') is not None
                and not self._child_presets_supported):
            if self._child_panel is not None:
                self._child_panel.fail('Updated child-image gateway required; no text downgrade, draft retained')
            return
        self._child_send(action, child, text, access, config=config, images=images)

    def _child_configured_start(self, task: str, access: str, config: dict[str, Any]) -> None:
        selected = self._child_presets.resolve(config).to_dict()
        if selected.get("preset") is not None and not self._child_presets_supported:
            if self._child_panel is not None:
                self._child_panel.fail("Updated preset gateway required; no specialist template silently ignored")
            return
        if not self._child_config_supported:
            if self._child_panel is not None:
                self._child_panel.fail("Updated child-configuration gateway required; no settings silently ignored")
            return
        if ((selected.get("model") is not None or selected.get("thinking") is not None)
                and not self._child_model_supported):
            if self._child_panel is not None:
                self._child_panel.fail("Updated child-model gateway required; no routing/reasoning silently ignored")
            return
        self._child_send("start", "", task, access, config=selected)

    def _child_send(self, action: str, child: str, text: str, access: str,
                    *, config: dict[str, Any] | None = None, images: tuple[Attachment, ...] = ()) -> None:
        if action == 'queue':
            try:
                command = json.loads(text)
            except (ValueError, TypeError):
                return
            if not isinstance(command, dict) or command != {'action': 'list'} or images or config:
                return  # Native list-only; Agent/SDK queue management stays backend-owned.
        panel = self._child_panel
        if panel is None:
            return
        if (not self._connection_ready or not self._children_supported or len(self._children_pending) >= 8
                or self._child_context != self._mcp_context_key()
                or action == "start" and (self._active_run_id is not None or self._waiting)
                or action == 'queue' and not self._child_queues_supported):
            panel.fail("Ready updated gateway/idle start required; child form retained")
            return
        identifier = "child-ui-" + uuid.uuid4().hex
        if action == 'queue' and panel.queue is None:
            return
        if action == 'queue' and panel.queue is not None:
            self._child_queue_owners[identifier] = (panel.queue.owner_token, panel.owner_token,
                self.group_id, self._gateway_session_id, child)
        self._children_pending[identifier] = action, self._gateway_session_id
        self._child_ui_requests.add(identifier)
        wire_config = dict(config or {})
        if not self._child_model_supported:
            for key in ("model", "thinking"):
                if wire_config.get(key) is None:
                    wire_config.pop(key, None)
        if not self._child_presets_supported and wire_config.get("preset") is None:
            wire_config.pop("preset", None)
        self._queue_request({"type": "child_control", "request_id": identifier, "group_id": self.group_id,
                             "session_id": self._gateway_session_id, "action": action, "child_id": child,
                             "text": text, "access": access, "config": wire_config},
                            images)

    def _cancel_child_source(self) -> None:
        pending = self._child_source_pending
        if pending is not None and self._connection_ready:
            self._queue_request({"type": "child_source_cancel", "request_id": pending[0], "group_id": self.group_id,
                                 "session_id": self._gateway_session_id, "child_id": pending[1]})
        self._child_source_pending = None
        self._child_source_deadline = 0

    def _child_read(self, child: str) -> None:
        if self._child_panel is None:
            return
        self._cancel_child_source()
        if not self._connection_ready or not self._child_source_supported:
            self._child_panel.source(child, (), "Updated child-source gateway required; no clipped fallback")
            return
        identifier = "child-source-" + uuid.uuid4().hex
        scope = self._gateway_session_id + ":" + child
        self._child_source_pending = identifier, child, scope
        self._child_source_deadline = time.monotonic() + 30
        self._child_panel.reading()
        self._queue_request({"type": "child_source_read", "request_id": identifier, "group_id": self.group_id,
                             "session_id": self._gateway_session_id, "child_id": child})

    def _child_fullscreen(self) -> None:
        preview = self._child_panel.preview if self._child_panel is not None else None
        if preview is not None and self._tui.request_fullscreen(not preview.fullscreen):
            preview.fullscreen = not preview.fullscreen
            self._layout.fullscreen_view = self._child_panel if preview.fullscreen else None
            self._tui.request_render()

    def _leave_child_fullscreen(self) -> None:
        if self._child_panel is not None and self._child_panel.preview is not None:
            if self._child_panel.preview.fullscreen:
                self._child_fullscreen()

    def _close_children(self) -> None:
        self._leave_child_fullscreen()
        self._cancel_child_source()
        if self._child_panel is not None:
            self._child_panel.close()
        self._child_panel = None
        self._child_target = ""
        self._child_ui_requests.clear()
        self._child_queue_owners.clear()
        self._layout.details = self._outputs.panel or self._extension_dialog_panel
        self._tui.set_focus(self._outputs.panel or self._extension_dialog_panel or self._editor)
        self._update_hints()
        self._tui.request_render()

    def _child_output(self, action: str, scope: str) -> None:
        preview = self._child_panel.preview if self._child_panel is not None else None
        if preview is not None and preview.settled and preview.records:
            root = Path(str(self._state.get("workspace_dir", ".")))
            self._outputs.start(TransferOptions(action, scope, "text" if action == "copy" else
                                                "html" if action == "share" else "md"),
                                preview.records, root, preview.selection, preview.entry_id)

    def _agents_command(self, args: str) -> None:
        action, _, rest = args.strip().partition(" ")
        if action in {"", "list", "browse"}:
            self._open_children()
            return
        if action == "inspect":
            self._open_children(rest.strip())
            return
        if action == 'saved':
            self._chat_log.add_system('Child draft disk management was removed; '
                                      '/agents drafts recovers this session only')
            self._tui.request_render()
            return
        if action == 'drafts':
            self._open_children()
            if self._child_panel is not None:
                self._child_panel.open_drafts()
                self._tui.request_render()
            return
        if action == "presets":
            self._open_children()
            if self._child_panel is not None:
                self._child_panel.open_presets()
            return
        action = action or "list"
        child, text, access = "", "", "read-only"
        selected_config: dict[str, Any] = {}
        if action == "start":
            try:
                text, access, selected_config = parse_child_options(rest)
            except ValueError as exc:
                self._chat_log.add_system(str(exc))
                return
        else:
            child, _, text = rest.partition(" ")
        if (action not in {"list", "start", "inspect", "steer", "follow-up", "cancel", "wait", "close"}
                or action != "list" and not (text if action == "start" else child)):
            self._chat_log.add_system(
                "Usage: /agents start [--inherit] TASK | list | inspect/steer/cancel/wait/close ID")
            return
        if action == "start" and selected_config.get("preset") is not None and not self._child_presets_supported:
            self._preserve_submission("/agents " + args, "Updated preset gateway required; draft preserved")
            return
        if (action == "start" and not self._child_model_supported
                and (selected_config.get("model") is not None or selected_config.get("thinking") is not None)):
            self._preserve_submission("/agents " + args, "Updated child-model gateway required; draft preserved")
            return
        if (action == "start" and not self._child_config_supported
                and ChildConfig.from_dict(selected_config) != ChildConfig()):
            self._preserve_submission("/agents " + args,
                                      "Updated child-configuration gateway required; draft preserved")
            return
        if (not self._connection_ready or not self._children_supported or len(self._children_pending) >= 8
                or action == "start" and (self._active_run_id is not None or self._waiting)):
            self._preserve_submission("/agents " + args,
                                      "Child control needs a ready gateway/idle start; draft preserved")
            return
        identifier = "child-" + uuid.uuid4().hex
        self._children_pending[identifier] = action, self._gateway_session_id
        self._queue_request({"type": "child_control", "request_id": identifier, "group_id": self.group_id,
                             "session_id": self._gateway_session_id, "action": action, "child_id": child,
                             "text": text, "access": access, "config": selected_config})

    def _provider_auth_review_current(self, item: dict[str, Any]) -> bool:
        pending = self._extension_pending
        return (self._connection_ready and self._extension_provider_auth_supported and not self._exit_requested
                and pending is not None and not item.get('child_id') and self._active_run_id is None
                and self._extension_auth_owner == (pending[0], self._mcp_context_key(),
                                                    self._extension_provider_auth_generation)
                and type(item.get('provider_auth_generation')) is int
                and item['provider_auth_generation'] == self._extension_provider_auth_generation
                and self._child_panel is None and not self._tui.has_overlay())

    def _open_provider_auth_url(self, url: str) -> None:
        from xdog.tui.components.extension_provider_auth import ExtensionProviderAuth
        panel = self._extension_dialog_panel
        def alive() -> bool:
            return (isinstance(panel, ExtensionProviderAuth) and self._extension_dialog_panel is panel
                    and self._provider_auth_review_current(panel.dialog) and self._tui._focused is panel
                    and panel.focused and not panel._resolved and panel._review._visible
                    and panel.dialog['provider_auth'].get('url') == url)
        if alive():
            try:
                self._reviewed_url.start(url, alive)
            except (ValueError, RuntimeError):
                self._chat_log.add_system('Provider URL opening unavailable/busy; no automatic retry')


    def _serve_session_request(self, item: dict[str, Any]) -> None:
        value: Any = None
        pending = self._extension_pending
        owner = pending[0] if pending is not None else ""
        allowed = (not item.get("child_id") and pending is not None and self._active_run_id is None
                   and self._extension_command_owner == (owner, str(item.get("extension", "")).lower())
                   and self._connection_ready and self._extension_session_supported and not self._exit_requested)
        metadata = {"session_id": self._gateway_session_id, "group_id": self.group_id,
                    "workspace": str(self._file_completions.root) if self._file_completions is not None else ""}
        if item.get('operation') == 'commands' and 'command_menu' in item:
            from xdog.agent.extensions.command_catalog import invocation_scope
            identity = invocation_scope(item['command_menu'])
            allowed = (identity is not None and not identity['child_id'] and not item.get('child_id')
                and identity['session_id'] == self._gateway_session_id and not self._exit_requested
                and self._connection_ready and self._extension_command_catalog_supported
                and item.get('command_menu_generation') == self._extension_command_catalog_generation
                and (item.get('_command_menu_run') == self._active_run_id and self._active_run_id is not None
                     or pending is not None and item.get('_command_menu_command') == pending[0]))
            owner = identity['invocation'] if identity is not None else ''
        if allowed:
            if item['operation'] == 'commands':
                from xdog.agent.extensions.command_catalog import page
                from xdog.tui.command_help import normalize_command_menu
                try:
                    if not self._extension_command_catalog_supported:
                        raise ValueError('Updated command-menu viewer required')
                    value = page(normalize_command_menu(self._editor._command_provider()),
                                 {key: item.get(key, default) for key, default in
                                  (('query', ''), ('offset', 0), ('revision', ''))},
                                 session_id=self._command_session_id(), owner=owner + ':' + self._mcp_context_key())
                except (ValueError, TypeError):
                    value = None
            elif item["operation"] == "snapshot":
                value = metadata
            elif item["operation"] in ("model", "usage"):
                value = None  # Only the owning gateway may supply actual runtime inspection.
        self._answer_extension_dialog(item["id"], value)


    def _command_session_id(self) -> str:
        return self._gateway_session_id


    def _sync_extension_dialog(self) -> None:
        selected = self._extension_stack.choose(self._extension_dialog_queue)
        if selected is not None and selected.get('kind') == 'session':
            self._serve_session_request(selected)
            return
        panel = self._extension_dialog_panel
        if selected is None:
            self._extension_dialog_panel = None
        elif panel is None or panel.dialog['id'] != selected['id']:
            if panel is not None:
                panel.focused = False
            cached = self._extension_stack.panels.get(selected['id'])
            self._extension_dialog_panel = cached if isinstance(cached, ExtensionDialog) else None
        if self._extension_dialog_panel is None and selected is not None:
            item = selected
            if (('provider_auth_host' in item or 'provider_auth_menu' in item)
                    and not self._provider_auth_review_current(item)):
                self._answer_extension_dialog(item['id'], None)
                return
            def answer(value: Any, key: str = item['id']) -> None:
                self._answer_extension_dialog(key, value)
            label = f"Child {item['child_id']} · {item['extension']}" if item.get('child_id') else item['extension']
            if 'provider_auth_host' in item:
                from xdog.tui.components.extension_provider_auth import ExtensionProviderAuth
                self._extension_dialog_panel = ExtensionProviderAuth(
                    label, item, answer, open_url=self._open_provider_auth_url)
            else:
                self._extension_dialog_panel = ExtensionDialog(label, item, answer)
            self._extension_stack.panels[item['id']] = self._extension_dialog_panel
        if self._outputs.panel is None and not self._tui.has_overlay():
            if self._extension_dialog_panel is not None:
                self._leave_child_fullscreen()
                self._leave_transcript_fullscreen()
                self._layout.details = self._extension_dialog_panel
                self._tui.set_focus(self._extension_dialog_panel)
            elif isinstance(self._layout.details, ExtensionDialog):
                self._layout.details = (self._parent_draft_panel or self._child_panel
                                       or self._mcp_panel
                                       or self._transcript_panel or (
                                           self._details_panel if self._details_open else None))
                self._tui.set_focus(self._parent_draft_panel or self._child_panel
                                       or self._mcp_panel
                                    or self._transcript_panel or self._editor)


    def _answer_extension_dialog(self, key: str, value: Any) -> None:
        item = next((item for item in self._extension_dialog_queue if item['id'] == key), None)
        if item is None or not self._extension_stack.accepts(item, self._extension_dialog_queue):
            return
        if (("provider_auth_host" in item or "provider_auth_menu" in item)
                and not self._provider_auth_review_current(item)):
            value = None
        if item.get("child_id"):
            self._queue_request({"type": "child_extension_dialog_answer", "id": key, "value": value,
                                 "group_id": self.group_id, "session_id": item["session_id"],
                                 "child_id": item["child_id"], "child_owner": item["child_owner"]})
        else:
            self._queue_request({"type": "extension_dialog_answer", "id": key, "value": value})
        self._extension_dialog_queue = [item for item in self._extension_dialog_queue if item["id"] != key]
        self._extension_dialog_panel = None
        self._sync_extension_dialog()
        self._update_hints()
        self._tui.request_render()

    def _extension_command(self, command: str, args: str) -> None:
        if not self._connection_ready or not self._extension_supported or self._extension_pending is not None:
            self._preserve_submission(
                "/" + command + " " + args, "Ready updated gateway required; command not replayed")
            return
        if self._waiting or self._active_run_id is not None:
            self._preserve_submission("/" + command + " " + args, "Extension commands require idle ownership")
            return
        if command == "extensions":
            parts = args.split()
            action, name = parts[0] if parts else "list", parts[1] if len(parts) > 1 else ""
            text = ""
            if action == "auth":
                name, text = "", args.strip()[len("auth"):].lstrip()
                if not self._extension_provider_auth_supported:
                    self._chat_log.add_system("Updated native provider authentication capability required")
                    return
        else:
            action, name, text = "command", command, args
        from xdog.agent.extensions.commands import owner_for
        source = owner_for(command, self._extension_snapshot.get('command_owners', {})) if action == 'command' else None
        if action == 'command' and source is None:
            self._preserve_submission('/' + command + ' ' + args,
                                      'Extension command owner unavailable; refresh required')
            return
        identifier = "extensions-" + uuid.uuid4().hex
        self._extension_pending = identifier, self._gateway_session_id
        self._extension_auth_owner = ((identifier, self._mcp_context_key(), self._extension_provider_auth_generation)
                                      if action == "auth" else None)
        self._extension_cancel_scope = identifier, self._gateway_session_id
        self._extension_command_owner = (identifier, source.lower()) if source is not None else None
        self._queue_request({"type": "extension_control", "request_id": identifier, "group_id": self.group_id,
                             "session_id": self._gateway_session_id, "action": action, "name": name, "args": text})
        self._update_hints()
        self._tui.request_render()

    def _cancel_extension_command(self) -> None:
        pending = self._extension_pending
        if pending is None:
            return
        if not self._connection_ready or not self._extension_cancel_supported:
            self._chat_log.add_system('Updated extension-cancel gateway required; command was not replayed')
        else:
            captured = self._extension_cancel_scope
            expected = captured[1] if captured is not None and captured[0] == pending[0] else pending[1]
            # Revoke reviewed command authority immediately; a late successful
            # callback/receipt must not dispatch navigation after local cancel.
            self._extension_command_owner = None
            self._extension_auth_owner = None
            self._queue_request({'type': 'extension_control_cancel', 'request_id': pending[0],
                                 'group_id': self.group_id, 'expected_session': expected})
            self._chat_log.add_system('Canceling extension command · awaiting owned worker settlement')
        self._tui.request_render()

    def _cancel_arguments(self, request: Any) -> None:
        rid = next((key for key, value in self._argument_requests.items() if value == request), None)
        scope = self._argument_scopes.get(rid) if rid is not None else None
        if (scope is None or not self._connection_ready or not self._argument_cancel_supported
                or scope[2] != self._connection_generation):
            return  # Older/detached peers retain the bounded deadline, never guessed cancel identity.
        self._queue_request({'type': 'argument_completion_cancel', 'request_id': rid,
                             'group_id': scope[0], 'session_id': scope[1]})

    def _request_arguments(self, request: Any) -> None:
        rid = 'arguments-' + uuid.uuid4().hex
        self._argument_requests[rid] = request
        self._argument_scopes[rid] = self.group_id, self._gateway_session_id, self._connection_generation
        if self._argument_context_supported:
            self._argument_context_requests.add(rid)
        self._queue_request({'type': 'argument_completion', 'request_id': rid, 'group_id': self.group_id,
                             'session_id': self._gateway_session_id, 'command': request.command,
                             'prefix': request.prefix, 'identity': request.identity,
                             **({'argument_context': request.context()} if self._argument_context_supported else {})})

    def _mcp_context_key(self) -> str:
        return f"{self._connection_generation}:{self._gateway_session_id}:{self._output_epoch}"

    def _open_mcp(self) -> None:
        if self._parent_draft_panel is not None:
            self._close_parent_drafts()
        if self._child_panel is not None:
            self._close_children()
        if self._exit_requested or self._tui.has_overlay():
            return
        if self._mcp_panel is not None:
            return
        if self._transcript_panel is not None:
            self._close_transcript()
        if self._navigation_panel is not None:
            self._close_navigation()
        if self._attachment_panel is not None:
            self._close_attachment_preview()
        if self._details_open:
            self._close_details()
        self._mcp_document = None
        self._mcp_owner_context = self._mcp_context_key()
        self._mcp_auth_lease = uuid.uuid4().hex
        self._mcp_auth_group, self._mcp_auth_session = self.group_id, self._gateway_session_id
        self._mcp_auth_review = None
        self._mcp_panel = McpPanel(self._mcp_send, self._close_mcp, self._mcp_insert, self._mcp_export,
                                   self._mcp_auth_copy)
        self._layout.details = self._mcp_panel
        self._tui.set_focus(self._mcp_panel)
        self._update_hints()
        self._tui.request_render()

    def _cancel_mcp_data(self) -> None:
        if (self._mcp_active is not None and self._connection_ready
                and self._mcp_action in {"resources", "templates", "prompts", "read", "prompt"}):
            self._queue_request({"type": "mcp_context_cancel", "request_id": self._mcp_active})

    def _detach_mcp_auth(self) -> None:
        if self._mcp_auth_lease and self._connection_ready and self._mcp_auth_supported:
            self._queue_request({"type": "mcp_auth_control", "action": "detach", "group_id": self._mcp_auth_group,
                                 "session_id": self._mcp_auth_session, "request_id": "detach-" + uuid.uuid4().hex,
                                 "lease_id": self._mcp_auth_lease})
        self._mcp_auth_review = None
        if self._mcp_auth_copying and self._outputs.pending:
            self._outputs.cancel("Authorization URI copy canceled with its view")
        self._mcp_auth_copying = False

    def _mcp_auth_copy(self, url: str) -> None:
        panel = self._mcp_panel
        if (panel is None or panel.auth is None or panel.auth.url != url
                or self._mcp_owner_context != self._mcp_context_key()):
            return
        self._mcp_auth_copying = True
        self._outputs.start(TransferOptions("copy", "response", "text"),
                            (TranscriptRecord("oauth-url", "assistant", "Reviewed OAuth URI", url),),
                            Path(str(self._state.get("workspace_dir", "."))))

    def _close_mcp(self) -> None:
        self._reviewed_url.cancel()
        self._cancel_mcp_data()
        self._detach_mcp_auth()
        self._mcp_requests.clear()
        self._mcp_auth_requests.clear()
        if self._mcp_panel is not None:
            self._mcp_panel.close()
        self._mcp_panel = None
        self._mcp_document = None
        self._mcp_active = None
        self._mcp_owner_request = None
        self._layout.details = self._transcript_panel or (self._details_panel if self._details_open else None)
        self._tui.set_focus(self._transcript_panel or self._editor)
        self._update_hints()
        self._tui.request_render()

    def _mcp_insert(self) -> None:
        document = self._mcp_document
        if document is None or self._mcp_owner_context != self._mcp_context_key():
            return
        if not self._mcp_content_types_supported and any(not block.content_type for block in document.blocks):
            self._chat_log.add_system("Updated typed-content gateway required to insert legacy previews; "
                                      "payload type is unreported. Preview/export remain available.")
            return
        try:
            text = document.draft_text()
        except ValueError as exc:
            self._chat_log.add_system(str(exc))
            return
        except RuntimeError as exc:
            self._chat_log.add_system(str(exc))
            return
        self._editor.handle_paste("\n\n" + text)
        self._chat_log.add_system("MCP data inserted into editable draft as untrusted content; NOT sent")
        self._close_mcp()

    def _mcp_export(self) -> None:
        document = self._mcp_document
        if self._file_completions is None:
            self._chat_log.add_system("MCP export needs gateway workspace metadata; no guessed cwd")
            return
        if document is None or self._mcp_owner_context != self._mcp_context_key():
            return
        import json
        from dataclasses import asdict

        from xdog.tui.transcript_browser import TranscriptRecord
        self._outputs.start(TransferOptions("export", "all", "json"),
                            (TranscriptRecord("mcp-raw", "mcp", document.name,
                                              json.dumps(asdict(document), ensure_ascii=False)),),
                            self._file_completions.root)

    def _mcp_publish(self, action: str, data: Any = (), error: str = "") -> None:
        if action not in _MCP_PANEL_ACTIONS and not action.startswith('auth_'):
            return  # Retired/unknown controls cannot invalidate a successor's active view.
        panel = self._mcp_panel
        if panel is None:
            return
        if self._mcp_owner_context != self._mcp_context_key():
            self._close_mcp()
            return
        from dataclasses import asdict
        if action.startswith("auth_"):
            try:
                data, self._mcp_auth_review = normalize_auth_reply(
                    action, data, review=self._mcp_auth_review, error=error,
                )
                panel.publish_auth(action, data, error)
            except ValueError as exc:
                self._detach_mcp_auth()
                panel.publish_auth(action, {}, str(exc))
            self._tui.request_render()
            return
        if error:
            panel.publish(action, error=error)
        elif isinstance(data, McpDocument):
            self._mcp_document = data
            panel.publish(action, text=data.text)
        elif (isinstance(data, tuple) and all(isinstance(entry, McpEntry) for entry in data)
              and action in {"resources", "templates", "prompts"}):
            panel.publish(action, rows=tuple(asdict(entry) for entry in data))
        else:
            panel.publish(action, rows=tuple(dict(row) for row in data))
        self._tui.request_render()

    def _mcp_send(self, action: str, server: str, name: str, arguments: dict[str, str]) -> None:
        if action not in _MCP_PANEL_ACTIONS and not action.startswith('auth_'):
            return  # Retired/unknown controls cannot invalidate a successor's active view.
        if action.startswith('sampling_'):
            return  # A retired control cannot mutate or unlock a successor view.

        if action == 'auth_leave':
            self._detach_mcp_auth()
            self._mcp_auth_lease = uuid.uuid4().hex
            return
        self._cancel_mcp_data()
        self._mcp_sequence += 1
        identifier = str(self._mcp_sequence)
        self._mcp_active, self._mcp_action = identifier, action
        if not self._connection_ready or not self._mcp_supported:
            self._recv_queue.put({'type': 'mcp_view_failed', 'id': identifier,
                                  'error': 'Ready updated MCP gateway required'})
            return
        if (action in {'connect', 'disconnect', 'refresh'}
                and (self._active_run_id is not None or self._waiting or self._pending_command is not None)):
            self._recv_queue.put({'type': 'mcp_view_failed', 'id': identifier,
                                  'error': 'MCP connection/schema changes require an idle owner'})
            return
        if action.startswith("auth_"):
            active_auth = self._mcp_panel.auth if self._mcp_panel is not None else None
            for review in (self._mcp_auth_review or {}, active_auth.review if active_auth is not None else {}):
                if (review.get('registration', 'pre-registered') != 'pre-registered'
                        or review.get('token_auth_method', 'none') != 'none' or review.get('client_secret_env', '')):
                    self._recv_queue.put({'type': 'mcp_view_failed', 'id': identifier,
                                          'error': 'Only pre-registered public PKCE consent is supported'})
                    return
            if action.startswith("auth_credentials_") and not self._mcp_credentials_supported:
                self._recv_queue.put({"type": "mcp_view_failed", "id": identifier,
                                     "error": "Updated private-credential gateway required"})
                return
            if action in {"auth_refresh_prepare", "auth_refresh"} and not self._mcp_refresh_supported:
                self._recv_queue.put({"type": "mcp_view_failed", "id": identifier,
                                      "error": "Updated explicit-refresh gateway required"})
                return
            if not self._mcp_auth_supported:
                self._recv_queue.put({"type": "mcp_view_failed", "id": identifier,
                                      "error": "Updated authorization gateway required"})
                return
            self._mcp_auth_requests.add(identifier)
            self._queue_request({"type": "mcp_auth_control", "request_id": identifier, "group_id": self.group_id,
                                 "session_id": self._gateway_session_id, "lease_id": self._mcp_auth_lease,
                                 "action": action.removeprefix("auth_"), "server": server, "review_id": name})
            return
        if action == 'list' and not self._gateway_session_id and self._mcp_browser_owner_supported:
            self._mcp_owner_request = identifier, self._mcp_auth_lease, self._mcp_owner_context
            self._mcp_requests.add(identifier)
            self._queue_request({'type': 'mcp_control', 'request_id': identifier, 'group_id': self.group_id,
                                 'action': 'owner', 'expected_session': '', 'view': self._mcp_auth_lease})
            return  # Opening selects the owner only; b/f remain separate explicit gestures.
        if action in {"list", "connect", "disconnect", "refresh"}:
            self._mcp_requests.add(identifier)
            self._queue_request({"type": "mcp_control", "request_id": identifier, "group_id": self.group_id,
                                 "server": server, "action": action})
        elif not self._mcp_data_supported:
            self._recv_queue.put({"type": "mcp_view_failed", "id": identifier,
                                  "error": "Gateway MCP context support required"})
        else:
            self._queue_request({"type": "mcp_context", "request_id": identifier, "group_id": self.group_id,
                                 "session_id": self._gateway_session_id, "server": server, "action": action,
                                 "name": name, "arguments": arguments})

    def _compact_command(self, instructions: str) -> None:
        if instructions.strip() == 'cancel':
            self._cancel_compaction()
            return
        from xdog.agent.summarization import summary_instructions
        try:
            instructions = summary_instructions(instructions)
        except ValueError as exc:
            self._chat_log.add_system(str(exc))
            return
        if (not self._connection_ready or not self._compaction_supported or self._compact_pending is not None
                or self._active_run_id is not None or self._pending_command is not None
                or self._history_loading or self._session_mutation_id is not None):
            self._chat_log.add_system('Updated compaction gateway and idle owner required; no model input sent')
            return
        identifier = 'compact-' + uuid.uuid4().hex
        self._compact_pending = identifier, self._gateway_session_id
        self._compact_deadline = time.monotonic() + 180
        self._queue_request({'type': 'session_compact', 'request_id': identifier, 'group_id': self.group_id,
                             'session_id': self._gateway_session_id, 'instructions': instructions})
        self._set_activity_status('compacting · Esc cancels')
        self._update_hints()
        self._tui.request_render()

    def _cancel_compaction(self) -> None:
        if self._compact_pending is None:
            return
        identifier, session = self._compact_pending
        self._queue_request({'type': 'session_compact_cancel', 'request_id': identifier,
                             'group_id': self.group_id, 'session_id': session})
        self._set_activity_status('canceling compaction · waiting for receipt')
        self._tui.request_render()

    def _lose_compaction_receipt(self) -> None:
        if self._compact_pending is not None:
            self._cancel_compaction()
            self._compact_pending = None
            self._chat_log.add_system('Compaction receipt lost; inspect /transcript after reconnect, no replay')
            self._set_activity_status('idle')
            self._update_hints()

    def _skills_command(self, args: str) -> None:
        parts = args.split()
        action, slug = (parts[0], parts[1] if len(parts) == 2 else '') if parts else ('list', '')
        if (not self._connection_ready or not self._skills_supported or self._skills_request
                or len(parts) > 2 or action not in {'list', 'load', 'unload'}):
            self._chat_log.add_system('Use /skills [load SLUG|unload SLUG|unload all] with a ready skill gateway')
            return
        self._skills_request = uuid.uuid4().hex
        self._skills_session = self._gateway_session_id
        self._skills_deadline = time.monotonic() + 15
        self._queue_request({'type': 'skills_control', 'request_id': self._skills_request,
            'group_id': self.group_id, 'session_id': self._skills_session, 'action': action, 'slug': slug})

    def _open_tools(self, query: str = '') -> None:
        self._open_transcript(query, tools=True)

    def _refresh_tools(self) -> None:
        panel = self._transcript_panel
        if not isinstance(panel, ToolInventoryPanel):
            return
        self._cancel_tools()
        if not self._connection_ready or not self._tool_inventory_supported or self._session_context_stale:
            panel.invalidate_inventory('Updated tool-inventory gateway/session required; no local approximation')
            return
        self._tools_request = uuid.uuid4().hex
        self._tools_session = self._gateway_session_id
        self._tools_deadline = time.monotonic() + 15
        panel.begin_inventory_read()
        self._queue_request({'type': 'tool_inventory_read', 'request_id': self._tools_request,
                             'group_id': self.group_id, 'session_id': self._tools_session,
                             **({'include_selection': True} if self._tool_inventory_selection_supported else {})})

    def _cancel_tools(self) -> None:
        if self._tools_request and self._connection_ready:
            self._queue_request({'type': 'tool_inventory_cancel', 'request_id': self._tools_request})
        self._tools_request = ''
        self._tools_deadline = 0.0

    def _open_transcript(self, query: str = "", *, tools: bool = False,
                         saved: tuple[str, str] | None = None) -> None:
        if self._parent_draft_panel is not None:
            self._close_parent_drafts()
        if self._transcript_panel is not None and saved != self._saved_scope:
            self._close_transcript()
        if self._transcript_panel is not None and isinstance(self._transcript_panel, ToolInventoryPanel) != tools:
            self._close_transcript()
        if self._child_panel is not None:
            self._close_children()
        if self._exit_requested or self._tui.has_overlay():
            return
        if self._mcp_panel is not None:
            self._close_mcp()
        if self._transcript_panel is not None:
            if query:
                self._transcript_panel.search(query)
            return
        if self._attachment_panel is not None:
            self._close_attachment_preview()
        if self._navigation_panel is not None:
            self._close_navigation()
        if self._details_open:
            self._close_details()
        self._saved_scope = saved
        if saved is not None:
            self._output_epoch += 1
            self._source_records = None
        panel_type = ToolInventoryPanel if tools else TranscriptPanel
        self._transcript_panel = panel_type((lambda: ()) if tools else self._full_transcript_records,
            self._close_transcript, self._toggle_transcript_fullscreen, query=query,
            on_refresh=self._refresh_tools if tools else self._request_full_source,
            on_output=self._output_from_browser)
        if saved is not None:
            self._transcript_panel.caption = ('Session timeline · read-only' if saved[1] == '@timeline'
                                              else 'Saved context · read-only')
        self._refresh_tools() if tools else self._request_full_source()
        self._layout.details = self._transcript_panel
        self._tui.set_focus(self._transcript_panel)
        self._update_hints()
        self._tui.request_render()

    def _view_navigation(self, entry: NavigationEntry) -> None:
        if entry.session_id == self._gateway_session_id and not entry.branch_id:
            self._open_transcript()
        else:
            self._open_transcript(saved=(entry.session_id, entry.branch_id))

    def _full_transcript_records(self) -> tuple[TranscriptRecord, ...]:
        if self._saved_scope is not None:
            return self._source_records or ()
        local = self._chat_log.transcript_records()
        if self._source_records is None:
            return local
        # Client notices are not provider context, but remain inspectable.
        notices = tuple(TranscriptRecord("client:" + record.id, record.role, record.title, record.body)
                        for record in local if record.role == "system")
        return (*notices, *self._source_records)

    def _cancel_source_read(self) -> None:
        if self._source_request_id is not None and self._connection_ready:
            self._queue_request({"type": "source_cancel", "request_id": self._source_request_id,
                                 "group_id": self.group_id, "session_id": self._source_session_id})
        self._source_request_id = None
        self._source_deadline = 0.0

    def _request_full_source(self) -> None:
        panel = self._transcript_panel
        if panel is None or isinstance(panel, ToolInventoryPanel):
            return  # A tool-definition view never admits canonical conversation reads.
        self._cancel_source_read()
        if not self._connection_ready or self._session_context_stale:
            panel.set_source_status("Full source unavailable: gateway/session not ready")
            return
        if (self._saved_scope is not None and (not self._saved_source_supported
                or self._saved_scope[1] == '@timeline' and not self._saved_timeline_supported)):
            self._source_records = None
            panel.replace_records(())
            panel.set_source_status('Saved source unavailable · updated gateway required · no live fallback', busy=True)
            return
        if not self._source_supported:
            panel.set_source_status("Limited source: updated gateway required")
            panel.replace_records(self._chat_log.transcript_records())
            self._tui.request_render()
            return
        self._source_request_id = "source-" + uuid.uuid4().hex
        self._source_session_id = self._gateway_session_id
        self._source_deadline = time.monotonic() + 30
        panel.replace_records(())
        panel.set_source_status("Saved source loading" if self._saved_scope is not None else "Full source loading",
                                busy=True)
        request: dict[str, Any] = {"type": "source_read", "request_id": self._source_request_id,
                                  "group_id": self.group_id, "session_id": self._source_session_id}
        if self._saved_scope is not None:
            request.update(type='saved_source_read', target_session_id=self._saved_scope[0],
                           branch_id=self._saved_scope[1])
        self._queue_request(request)
        self._tui.request_render()

    def _handle_source_event(self, msg: Mapping[str, Any]) -> None:
        if (self._transcript_panel is None or isinstance(self._transcript_panel, ToolInventoryPanel)
                or self._source_request_id is None or msg.get("request_id") != self._source_request_id
                or msg.get("session_id") != self._source_session_id
                or msg.get("group_id") != self.group_id or self._source_session_id != self._gateway_session_id
                or (msg.get('target_session_id', ''), msg.get('branch_id', '')) != (self._saved_scope or ('', ''))):
            return
        panel = self._transcript_panel
        self._source_deadline = time.monotonic() + 30
        if msg["type"] == "source_progress":
            received, size = msg.get("received"), msg.get("bytes")
            if type(received) is not int or type(size) is not int or not 0 <= received <= size:
                self._pending_output = None
                self._cancel_source_read()
                panel.set_source_status("Full source rejected: invalid progress")
            else:
                panel.set_source_status(f"Full source loading: {received:,}/{size:,} bytes", busy=True)
        elif msg["type"] == "source_ready":
            records = msg.get("records", ())
            if not isinstance(records, tuple) or any(not isinstance(record, TranscriptRecord) for record in records):
                self._pending_output = None
                panel.set_source_status("Full source rejected: invalid records")
            else:
                self._source_records = (tuple(replace(record, id=f'saved:{self._saved_scope[0]}:'
                                                     f'{self._saved_scope[1]}:' + record.id) for record in records)
                                        if self._saved_scope is not None else records)
                panel.set_source_status("Saved snapshot · no activation" if self._saved_scope is not None
                                        else "Full source snapshot")
                panel.replace_records(self._full_transcript_records())
            self._source_request_id = None
        elif msg["type"] == "source_failed":
            self._pending_output = None
            self._cancel_source_read()
            panel.set_source_status("Full source failed: " + str(msg.get("error", "unknown error")),
                                    busy=self._saved_scope is not None)
        self._tui.request_render()

    def _toggle_transcript_fullscreen(self) -> None:
        panel = self._transcript_panel
        if panel is None:
            return
        enabled = not panel.fullscreen
        if not self._tui.request_fullscreen(enabled):
            self._chat_log.add_system("Fullscreen unavailable during terminal handoff/dialog; viewer retained")
            return
        panel.fullscreen = enabled
        self._layout.fullscreen_view = panel if enabled else None
        self._tui.request_render()

    def _leave_transcript_fullscreen(self) -> None:
        if self._transcript_panel is not None and self._transcript_panel.fullscreen:
            self._transcript_panel.fullscreen = False
            self._layout.fullscreen_view = None
            self._tui.request_fullscreen(False)

    def _close_transcript(self) -> None:
        if self._saved_scope is not None:
            self._output_epoch += 1
        self._saved_scope = None
        self._cancel_tools()
        self._pending_output = None
        self._cancel_source_read()
        self._source_records = None
        self._leave_transcript_fullscreen()
        if self._transcript_panel is not None:
            self._transcript_panel.close()
        self._transcript_panel = None
        self._layout.details = self._details_panel if self._details_open else None
        self._tui.set_focus(self._editor)
        self._update_hints()
        self._tui.request_render()

    def _output_from_browser(self, action: str, scope: str) -> None:
        if action == "copy":
            self._output_command(action, scope)
        else:
            options = TransferOptions(action, scope, "html" if action == "share" else "md")
            self._begin_output(options)

    def _output_command(self, action: str, args: str) -> None:
        if args.strip() == "cancel":
            self._outputs.cancel()
            return
        try:
            options = parse_transfer(action, args)
        except ValueError as exc:
            self._chat_log.add_system(str(exc))
            self._tui.request_render()
            return
        self._begin_output(options)

    def _sync_output_panel(self) -> None:
        panel = self._outputs.panel
        if panel is not None and not self._tui.has_overlay():
            self._leave_child_fullscreen()
            self._leave_transcript_fullscreen()
            self._layout.details = panel
            self._tui.set_focus(panel)
        elif isinstance(self._layout.details, OutputPanel):
            self._layout.details = (self._extension_dialog_panel or self._parent_draft_panel
                                    or self._child_panel
                    or self._mcp_panel
                or self._transcript_panel or (
                self._details_panel if self._details_open else None))
            self._tui.set_focus((self._extension_dialog_panel if self._extension_dialog_panel is not None
                                    else None)
                                    or self._parent_draft_panel or self._child_panel
                    or self._mcp_panel
                                    or self._transcript_panel or self._editor)
        self._update_hints()

    def _load_command_artifact(self, args: str) -> None:
        import shlex
        try:
            parts = shlex.split(args)
            if len(parts) != 1:
                raise ValueError("Usage: /artifact PATH (read-only; never executes imported content)")
            path = Path(parts[0]).expanduser()
            root = (self._file_completions.root if self._file_completions is not None else Path("."))
            path = path if path.is_absolute() else root / path
            if not self._artifact_loader.start(path, self._mcp_context_key()):
                raise ValueError("Artifact reader is busy/closing; finish the current read first")
            self._chat_log.add_system("Reading a captured artifact; no model/command/tool execution")
        except ValueError as exc:
            self._chat_log.add_system(str(exc))
        self._tui.request_render()

    def _artifact_result(self, event: Mapping[str, Any]) -> None:
        if event.get("context") != self._mcp_context_key() or self._exit_requested:
            return
        value = event.get("artifact")
        if event.get("type") == "artifact_loaded" and isinstance(value, ExtensionCommandResult):
            self._chat_log.add_child(ExtensionCommand(value.command, value.result, value.text(),
                                                       owner=self._mcp_context_key()))
        else:
            self._chat_log.add_system(str(event.get("error", "Invalid artifact; nothing imported")))
        self._tui.request_render()

    def _export_command_artifact(self, options: TransferOptions) -> None:
        from xdog.tui.transcript_transfer import PreparedTransfer
        if options.action != "export" or options.format != "json":
            self._chat_log.add_system("Artifact export requires private JSON, never implicit copy/share")
            return
        owner = self._mcp_context_key()
        artifact = next((item for item in reversed(self._chat_log.children)
                         if isinstance(item, ExtensionCommand) and item.owner == owner), None)
        if artifact is None:
            self._chat_log.add_system("No owned command artifact in this session/connection; nothing exported")
            return
        extension = artifact.command.split("-", 2)[1]
        # A deep validated snapshot, not a live mutable renderer/selection source.
        snapshot = ExtensionCommandResult.from_dict({"extension": extension, "command": artifact.command,
                                                    "result": artifact.result}).to_dict()
        def prepare(normalized: TransferOptions, context: str, key: str) -> PreparedTransfer:
            import hashlib
            data, preview, images = export_snapshot(snapshot)
            return PreparedTransfer(key, context, normalized, preview, data,
                hashlib.sha256(data).hexdigest(), 1, ("command", "binary", "metadata"), False)
        root = self._file_completions.root if self._file_completions is not None else Path(".")
        self._outputs.start_snapshot(options, root, prepare)

    def _begin_output(self, options: TransferOptions) -> None:
        if isinstance(self._transcript_panel, ToolInventoryPanel):
            tool_panel = self._transcript_panel
            if (not tool_panel.settled or not self._connection_ready or not self._tool_inventory_supported
                    or self._tools_session != self._gateway_session_id):
                self._chat_log.add_system('Wait for settled configured definitions before copying/exporting')
                return
            root = self._file_completions.root if self._file_completions is not None else Path.cwd()
            if options.scope == 'response':
                options = replace(options, scope='entry')
            self._outputs.start(options, tool_panel.records, root, tool_panel.selection, tool_panel.entry_id)
            return
        if options.scope == "artifact":
            self._export_command_artifact(options)
            return
        panel = self._transcript_panel
        if self._saved_scope is not None:
            if (panel is None or not panel.settled or self._source_records is None
                    or not self._connection_ready or not self._saved_source_supported
                    or self._saved_scope[1] == '@timeline' and not self._saved_timeline_supported):
                self._chat_log.add_system('Wait for a current saved source before copying/exporting')
                return
            self._launch_output(options)
            return
        if options.scope in {"selection", "entry"}:
            if panel is None or not panel.settled or self._source_records is None:
                self._chat_log.add_system("Select settled full-source content first (updated gateway required)")
                return
            self._launch_output(options)
            return
        if not self._connection_ready or self._session_context_stale or not self._source_supported:
            self._chat_log.add_system(
                "Output needs a ready full-source gateway; no clipped fallback is copied/exported"
            )
            self._tui.request_render()
            return
        if self._outputs.pending or self._pending_output is not None:
            self._chat_log.add_system("An output request is already pending")
            return
        self._pending_output = options
        already_open = self._transcript_panel is not None
        self._open_transcript()
        if already_open:
            self._request_full_source()

    def _launch_output(self, options: TransferOptions) -> None:
        panel = self._transcript_panel
        if panel is None or isinstance(panel, ToolInventoryPanel) or self._source_records is None:
            return  # Canonical conversation output cannot consume tool-definition records.
        root = self._file_completions.root if self._file_completions is not None else Path.cwd()
        self._outputs.start(options, panel.records, root, panel.selection, panel.entry_id)

    def _transcript_command(self, args: str) -> None:
        parts = args.split()
        if parts and parts[0] == 'timeline' and len(parts) <= 2:
            self._open_transcript(saved=(parts[1] if len(parts) == 2 else self._gateway_session_id, '@timeline'))
            return
        if parts and parts[0] in {'saved', 'branch'}:
            if (parts[0] == 'branch' and len(parts) == 2
                    or parts[0] == 'saved' and len(parts) in {2, 3}):
                scope = ((self._gateway_session_id, parts[1]) if parts[0] == 'branch'
                         else (parts[1], parts[2] if len(parts) == 3 else ''))
                if all(not value or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', value) for value in scope):
                    self._open_transcript(saved=scope)
                    return
            self._chat_log.add_system('Usage: /transcript branch ID | /transcript saved SESSION [BRANCH]')
            return
        if args.strip() == "close":
            self._close_transcript()
        elif args.strip() == "refresh":
            self._open_transcript(saved=self._saved_scope)
            if self._transcript_panel is not None:
                self._transcript_panel.refresh()
        elif args.strip() == "fullscreen":
            self._open_transcript(saved=self._saved_scope)
            self._toggle_transcript_fullscreen()
        else:
            self._open_transcript(args)

    def _open_attachment_preview(self, item: Attachment) -> None:
        if self._parent_draft_panel is not None:
            self._close_parent_drafts()
        if self._transcript_panel is not None:
            self._close_transcript()
        if self._tui.has_overlay():
            return
        if self._mcp_panel is not None:
            self._close_mcp()
        if self._attachment_panel is not None:
            self._close_attachment_preview()
        if self._navigation_panel is not None:
            self._close_navigation()
        if self._details_open:
            self._close_details()
        self._attachment_panel = AttachmentPanel(item, self._close_attachment_preview)
        self._layout.details = self._attachment_panel
        self._tui.set_focus(self._attachment_panel)
        self._update_hints()
        self._tui.request_render()

    def _close_attachment_preview(self) -> None:
        self._attachment_panel = None
        self._layout.details = (self._navigation_panel
                                or (self._details_panel if self._details_open else None))
        self._tui.set_focus(self._navigation_panel
                            or (self._details_panel if self._details_open else self._editor))
        self._update_hints()
        self._tui.request_render()

    def _open_navigation(self, scope: str = "sessions") -> None:
        if self._parent_draft_panel is not None:
            self._close_parent_drafts()
        if self._child_panel is not None:
            self._close_children()
        if self._transcript_panel is not None:
            self._close_transcript()
        if self._attachment_panel is not None:
            self._close_attachment_preview()
        if not self._connection_ready or not self._session_controls_supported:
            self._chat_log.add_system("History browser requires a ready updated gateway")
            self._tui.request_render()
            return
        if self._tui.has_overlay():
            self._chat_log.add_system("Close the active dialog before browsing history")
            self._tui.request_render()
            return
        if self._mcp_panel is not None:
            self._close_mcp()
        if self._navigation_panel is not None:
            self._close_navigation()
        if self._details_open:
            self._close_details()
        self._navigation_provider = NavigationFeed(self._request_navigation)
        self._navigation_panel = HistoryPanel(self._navigation_provider, self._view_navigation,
                                             self._close_navigation, scope=scope)
        self._layout.details = self._navigation_panel
        self._tui.set_focus(self._navigation_panel)
        self._update_hints()
        self._tui.request_render()

    def _close_navigation(self) -> None:
        provider = self._navigation_provider
        if provider is not None:
            provider.close()
        if provider is not None:
            self._navigation_requests = {key: value for key, value in self._navigation_requests.items()
                                         if value[0] is not provider}
        self._navigation_panel = None
        self._navigation_provider = None
        self._layout.details = (self._details_panel if self._details_open else None)
        self._tui.set_focus((self._details_panel if self._details_open else self._editor))
        self._update_hints()
        self._tui.request_render()

    def _request_navigation(self, serial: int, scope: str, query: str, offset: int) -> None:
        provider = self._navigation_provider
        if provider is None:
            return
        if not self._connection_ready:
            provider.publish(serial, NavigationSnapshot(scope=scope, query=query, error="Gateway not ready"))
            return
        request_id = uuid.uuid4().hex
        self._navigation_requests[request_id] = (provider, serial, "read")
        self._queue_request({"type": "session_control", "request_id": request_id, "group_id": self.group_id,
                             "action": "list" if scope == "sessions" else "tree", "scope": scope,
                             "query": query, "offset": offset})

    def _new_context(self) -> None:
        if (not self._connection_ready or not self._session_controls_supported or self._active_run_id is not None
                or self._pending_command is not None):
            self._chat_log.add_system("New context requires a ready idle group; request was not sent")
            self._tui.request_render()
            return
        request_id = uuid.uuid4().hex
        self._navigation_requests[request_id] = (None, 0, "mutate")
        self._session_mutation_id = request_id
        self._pending_command = "session:" + request_id
        self._queue_request({"type": "session_control", "request_id": request_id, "group_id": self.group_id,
                             "action": "new", "expected_session": self._gateway_session_id})
        self._set_waiting(True)
        self._tui.request_render()

    def _retire_access_request(self, notice: str = '') -> None:
        pending, self._access_pending = self._access_pending, None
        if pending is not None and self._pending_command == 'access:' + pending[0]:
            self._pending_command = None
        if notice:
            self._chat_log.add_system(notice)

    def _readonly_command(self, args: str) -> None:
        action = args.strip() or 'status'
        if action not in {'on', 'off', 'status'}:
            self._chat_log.add_system('Usage: /readonly on|off|status')
            return
        if (not self._access_supported or not self._connection_ready or not self._gateway_session_id
                or self._session_context_stale or self._exit_requested or self._access_pending is not None
                or self._pending_command is not None or self._tui.has_overlay()
                or self._extension_dialog_panel is not None or self._extension_dialog_queue
                or self._compact_pending is not None or self._extension_pending is not None
                or self._session_mutation_id is not None
                or action != 'status' and (self._active_run_id is not None or self._waiting)):
            self._chat_log.add_system('Updated ready original idle owner required for access control; no replay')
            return
        identifier = 'access-' + uuid.uuid4().hex
        self._access_pending = identifier, self._gateway_session_id, self._mcp_context_key(), time.monotonic() + 30
        self._pending_command = 'access:' + identifier
        self._queue_request({'type': 'access_control', 'request_id': identifier, 'group_id': self.group_id,
                             'session_id': self._gateway_session_id,
                             'action': {'on': 'restrict', 'off': 'release', 'status': 'status'}[action]})
        self._tui.request_render()

    def _access_result(self, message: Mapping[str, Any]) -> None:
        pending = self._access_pending
        if (pending is None or message.get('request_id') != pending[0]
                or message.get('group_id') != self.group_id):
            return
        if (not self._access_supported or not self._connection_ready or pending[2] != self._mcp_context_key()
                or message.get('session_id') != pending[1] or pending[1] != self._gateway_session_id):
            self._retire_access_request('Access receipt owner changed; inspect status explicitly, no replay')
            return
        self._retire_access_request()
        access = message.get('access')
        if message.get('success') is not True:
            self._chat_log.add_system('Access control refused; inspect current status before retrying')
        elif (not isinstance(access, dict) or set(access) != {'read_only', 'revision'}
                or type(access['read_only']) is not bool or type(access['revision']) is not int
                or access['revision'] < 0 or self._access_session == pending[1]
                and access['revision'] < self._access_revision):
            self._chat_log.add_system('Invalid access receipt; no scope transition trusted')
        else:
            self._access_read_only, self._access_revision = access['read_only'], access['revision']
            self._access_session = pending[1]
            self._chat_log.add_system('Read-only scope ' + ('ON' if access['read_only'] else 'OFF')
                                      + f' · revision {access["revision"]} · existing tool permissions unchanged')
        self._update_hints()
        self._tui.request_render()

    def _name_session(self, args: str) -> None:
        if not args:
            if (not self._connection_ready or not self._name_read_supported or self._session_context_stale
                    or not self._gateway_session_id or self._pending_command is not None or self._exit_requested):
                self._chat_log.add_system('Name inspection requires an updated ready current owner; no cached fallback')
            elif self._name_read is not None:
                self._chat_log.add_system('Session name read already pending; Escape cancels it')
            else:
                request_id = uuid.uuid4().hex
                self._name_read = request_id, self.group_id, self._gateway_session_id, self._connection_generation
                self._queue_request({'type': 'session_control', 'request_id': request_id, 'action': 'name',
                                     'group_id': self.group_id, 'expected_session': self._gateway_session_id})
                self._chat_log.add_system('Reading session name from gateway · Escape cancels read')
            self._update_hints()
            self._tui.request_render()
            return
        if (not self._connection_ready or not self._session_rename_supported
                or self._session_context_stale or self._active_run_id is not None
                or self._pending_command is not None or self._tui.has_overlay()):
            self._chat_log.add_system('Session naming requires an updated ready idle group')
            self._tui.request_render()
            return
        from xdog.agent.session_name import session_name
        try:
            name = session_name('' if args.strip() == '-' else args)
        except ValueError as exc:
            self._chat_log.add_system(str(exc))
            self._tui.request_render()
            return
        if not self._gateway_session_id:
            self._chat_log.add_system('No active session to name; use /new first')
            self._tui.request_render()
            return
        self._cancel_name_read()
        request_id = uuid.uuid4().hex
        self._navigation_requests[request_id] = (None, 0, 'rename')
        self._session_mutation_id = request_id
        self._pending_command = 'session:' + request_id
        self._queue_request({'type': 'session_control', 'request_id': request_id, 'group_id': self.group_id,
                             'action': 'rename', 'expected_session': self._gateway_session_id, 'name': name})
        self._set_waiting(True)
        self._tui.request_render()

    def _cancel_name_read(self, *, send: bool = True, notice: str = '') -> None:
        read, self._name_read = self._name_read, None
        if read is None:
            return
        if (send and self._connection_ready and self._session_cancel_supported
                and read[3] == self._connection_generation):
            self._queue_request({'type': 'session_control_cancel', 'request_id': read[0],
                                 'group_id': read[1], 'expected_session': read[2]})
        if notice:
            self._chat_log.add_system(notice)
        self._update_hints()
        self._tui.request_render()

    def _handle_name_read(self, msg: Mapping[str, Any]) -> bool:
        read = self._name_read
        if read is None or msg.get('request_id') != read[0]:
            return False
        self._name_read = None
        if (msg.get('action') != 'name' or msg.get('group_id') != read[1]
                or msg.get('session_id') != read[2] or self.group_id != read[1]
                or self._gateway_session_id != read[2] or self._connection_generation != read[3]):
            self._chat_log.add_system('Session name read owner changed; inspect again')
        elif msg.get('success') is True:
            from xdog.agent.session_name import session_name
            try:
                name = session_name(msg.get('session_name'))
                if name != msg.get('session_name'):
                    raise ValueError('Session name read is not canonical plain text')
                self._state['session_name'] = name
                self._chat_log.add_system(f"Session name: {name or '(unnamed)'}")
            except ValueError:
                self._chat_log.add_system('Invalid session name read ignored; no cached fallback')
        else:
            if msg.get('status') == 'stale':
                self._session_context_stale = True
            self._chat_log.add_system(str(msg.get('error', 'Session name unavailable; no cached fallback')))
        self._update_hints()
        self._tui.request_render()
        return True

    def _cancel_session_navigation(self) -> None:
        if self._session_mutation_id is not None:
            if not self._session_cancel_supported:
                self._chat_log.add_system('Updated context-operation cancellation required; inspect before retrying')
                self._tui.request_render()
                return
            self._queue_request({'type': 'session_control_cancel', 'request_id': self._session_mutation_id,
                                 'group_id': self.group_id, 'expected_session': self._gateway_session_id})
            self._set_activity_status('canceling context operation · awaiting settlement')
            self._tui.request_render()

    def _handle_session_event(self, msg: Mapping[str, Any]) -> None:
        request_id = str(msg.get("request_id", ""))
        if msg["type"] == "session_result":
            if self._handle_name_read(msg):
                return
            request = self._navigation_requests.pop(request_id, None)
            if request is None:
                return
            provider, serial, kind = request
            if kind == 'rename':
                if request_id != self._session_mutation_id:
                    return
                if msg.get('success') is True:
                    from xdog.agent.session_name import session_name
                    try:
                        if (msg.get('group_id') != self.group_id
                                or msg.get('session_id') != self._gateway_session_id):
                            raise ValueError('Session name receipt owner changed; inspect before retrying')
                        name = session_name(msg.get('session_name'))
                        self._state['session_name'] = name
                        self._chat_log.add_system(f"Session name: {name or '(unnamed)'}")
                        if self._navigation_provider is not None:
                            view = self._navigation_provider.snapshot()
                            self._navigation_provider.request(view.scope, view.query, view.offset)
                    except ValueError as exc:
                        self._chat_log.add_system(str(exc))
                else:
                    self._chat_log.add_system(str(msg.get('error', 'Session naming failed')))
                self._finish_session_sync()
                return
            if kind == "read":
                if provider is not self._navigation_provider or provider is None:
                    return
                try:
                    if not msg.get("success"):
                        raise ValueError(str(msg.get("error", "Navigation failed")))
                    if "active_session" in msg:
                        actual = str(msg["active_session"])
                        if actual != self._gateway_session_id:
                            self._session_context_stale = True
                            self._chat_log.add_system(
                                "Group context rotated; refreshing the current context (history is read-only)")
                            if self._active_run_id is None:
                                self._queue_request({"type": "ping", "group_id": self.group_id})
                    entries = tuple(NavigationEntry(**dict(row)) for row in msg.get("entries", ()))
                    value = NavigationSnapshot(scope=str(msg.get("scope", "sessions")), query=str(msg.get("query", "")),
                                               offset=int(msg.get("offset", 0)), total=int(msg.get("total", 0)),
                                               entries=entries)
                except (ValueError, TypeError) as exc:
                    value = NavigationSnapshot(error=str(exc))
                provider.publish(serial, value)
            elif not msg.get("success"):
                self._pending_command = None
                self._session_mutation_id = None
                self._set_waiting(False)
                self._chat_log.add_system(str(msg.get("error", "Session change failed")))
            else:
                self._chat_log.add_system("Context rotation committed while cancellation settled; synchronizing context"
                    if msg.get('receipt_canceled') is True else "Session operation committed; synchronizing context")
        else:
            if request_id != self._session_mutation_id:
                return
            self._close_navigation()
            self._output_epoch += 1
            self._close_transcript()
            self._chat_log.clear_all()
            self._editor.reset_history()
            self._banner_added = False
            self._state["session_id"] = str(msg.get("session_id", ""))
            self._state["session_name"] = msg.get("session_name", "")
            self._gateway_session_id = self._state["session_id"]
            self._extension_snapshot = {**self._extension_snapshot}
            self._session_context_stale = False
            if "model" in msg:
                self._state["model"] = msg["model"]
            self._state["image_input"] = msg.get("image_input")
            self._state["image_limits"] = msg.get("image_limits", {})
            self._history_format = int(msg.get("history_format", 2))
            self._history_loading = int(msg.get("history_count", 0)) > 0
            self._replaying_history = self._history_loading
            self._connection_ready = not self._history_loading
            self._usage = {key: int(msg.get("usage", {}).get(key, 0)) for key in self._usage}
            self._update_header()
            if not self._history_loading:
                self._finish_session_sync()
        self._tui.request_render()

    def _finish_session_sync(self) -> None:
        self._pending_command = None
        self._session_mutation_id = None
        self._set_waiting(False)
        self._update_footer()
        self._dispatch_next_pending()


    def _close_details(self) -> None:
        self._details_open = False
        self._details_expanded = False
        self._layout.details = None
        self._tui.set_focus(self._editor)
        self._editor.set_focus(True)
        self._update_hints()
        self._tui.request_render()

    def _update_hints(self) -> None:
        if self._outputs.panel is not None:
            hint = "Output review · Tab path · PgUp/PgDn preview · Enter saves locally · Esc cancels"
        elif self._extension_dialog_panel is not None:
            hint = "Extension dialog · Enter answers · Esc cancels · NOT tool approval"
        elif self._parent_draft_panel is not None:
            hint = "Primary draft · explicit save/restore review · p image preview · Esc cancel · NOT sent"
        elif self._child_panel is not None:
            hint = "Child browser · Enter inspect · n new · c cancel · / search · Esc parent draft"
        elif self._mcp_panel is not None:
            hint = "MCP focused · c/d/r servers · u/t/p catalogs · Enter preview · i draft · e export · Esc back"
        elif isinstance(self._transcript_panel, ToolInventoryPanel):
            hint = "Tools · / schema search · n/N match · f screen · r refresh · Esc close · NOT approval"
        elif self._transcript_panel is not None:
            hint = "Transcript · / search · n/N match · arrows/pages · v/a select · f fullscreen · Esc close"
        elif self._attachment_panel is not None:
            hint = "Image preview · Esc return to draft · /attachments remove/clear"
        elif self._navigation_panel is not None:
            hint = "History · ↑/↓ select · →/← inspect/back · / search · Enter/v view (read-only) · Esc close"
        elif self._details_open:
            hint = "Details focused · ←/→ entry · PgUp/PgDn scroll · End latest · Esc input"
        elif self._compact_pending is not None:
            hint = "Compacting context · Esc or /compact cancel · draft retained · no automatic replay"
        elif self._extension_pending is not None:
            hint = 'Extension command pending · Esc cancels command · draft retained · NOT tool approval'
        elif self._name_read is not None:
            hint = 'Session name read pending · Esc cancels read · draft and active turn retained'
        elif self._waiting and self._active_run_id is not None:
            hint = (
                f"{self._keymap.label('send')} next turn · {self._keymap.label('steer')} steer · "
                f"{self._keymap.label('follow_up')} follow-up · Esc abort · /queue"
            )
        else:
            hint = (
                f"{self._keymap.label('send')} send · {self._keymap.label('newline')} newline · "
                f"{self._keymap.label('details')} details · {self._keymap.label('external_editor')} editor · /keys"
            )
        if self._editor.focused and self._access_session == self._gateway_session_id and self._access_read_only:
            hint = 'READ ONLY · ' + hint
        self._hints.set_text(hint)

    def _detail_records(self) -> tuple[DetailRecord, ...]:
        return self._chat_log.detail_records()

    def run(self) -> None:
        with capture_terminal_logs(self._recv_queue):
            self._run_owned_terminal()

    def _run_owned_terminal(self) -> None:
        if self._exit_requested:
            return
        self._update_footer()
        if self._keymap.error:
            self._chat_log.add_system(self._keymap.error)
        self._tui.on_tick(self._poll)
        try:
            self._io_thread.start()
            self._tui.start()
        finally:
            # EOF and exceptions bypass keyboard exit callbacks too. Cancel the
            # owning async task, not just a flag checked after a blocking read.
            self._request_exit()
            if self._io_thread.ident is not None:
                self._io_thread.join(timeout=2.0)

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
        if self._attachment_panel is not None:
            self._close_attachment_preview()
            return
        if self._navigation_panel is not None:
            self._close_navigation()
            return
        if self._details_open:
            self._close_details()
            return
        if self._session_mutation_id is not None:
            self._cancel_session_navigation()
            return
        if self._extension_pending is not None:
            self._cancel_extension_command()
            return
        if self._name_read is not None:
            self._cancel_name_read(notice='Session name read canceled; no metadata changed')
            return
        if self._compact_pending is not None:
            self._cancel_compaction()
            return
        run_id = self._active_run_id
        if run_id is None or self._aborting_run_id == run_id:
            return
        self._aborting_run_id = run_id
        if self._native_submission is not None and self._native_submission.run_id == run_id:
            self._native_submission = replace(self._native_submission, cancel_requested=True)
        self._queue_request({
            "type": "abort",
            "group_id": self._state.get("group_id", "main"),
            "run_id": run_id,
        })
        if self._native_submission is None or self._native_submission.run_id != run_id:
            self._restore_pending_draft(run_id, runtime=False)
        self._chat_log.add_system("cancelling run; runtime inputs await consumption/return receipts")
        self._chat_log.drop_assistant(run_id)
        for tool in self._chat_log._tools.values():
            tool.set_canceled()
        self._update_queue_summary()
        self._set_activity_status("cancelling")
        self._tui.request_render()

    def _restore_pending_draft(self, run_id: str, *, runtime: bool = True) -> None:
        """Restore active, queued, and currently typed text exactly once."""
        if run_id in self._restored_run_ids and not runtime:
            return
        already_restored = run_id in self._restored_run_ids
        queued = tuple(self._pending_messages) if not already_restored else ()
        if not already_restored:
            self._pending_messages.clear()
        self._pending_ids.clear()
        if not already_restored:
            self._composer.restore(self._active_images)
            self._active_images = ()
            self._composer.restore(tuple(item for group in self._local_images.values() for item in group))
            self._local_images.clear()
        if runtime:
            for key, entry in tuple(self._routed_inputs.items()):
                if entry.owner == run_id:
                    self._composer.restore(self._routed_images.pop(key, ()))
        routed = tuple(item.content for item in self._routed_inputs.values() if item.owner == run_id) if runtime else ()
        edits = tuple(content for action, _key, content, owner in self._input_requests.values()
                      if runtime and action == "edit" and owner == run_id)
        if runtime:
            self._routed_inputs = {key: item for key, item in self._routed_inputs.items() if item.owner != run_id}
            self._input_requests = {key: value for key, value in self._input_requests.items() if value[3] != run_id}
        draft = self._editor.get_text()
        restored = "\n\n".join(
            text for text in ("" if already_restored else self._active_text or "", *queued, *routed, *edits, draft)
            if text.strip()
        )
        if restored and restored != draft:
            self._editor.set_text(restored)
        self._restored_run_ids.add(run_id)
        self._update_queue_summary()

    def _draft_write_failures(self) -> tuple[int, ...]:
        return (self._parent_drafts.io.failures, self._parent_drafts.autosave.io.failures)

    def _finish_deferred_draft_quit(self) -> bool:
        failures = self._draft_quit_failures
        if failures is None:
            return False
        if self._draft_write_failures() != failures:
            self._draft_quit_failures = None
            self._chat_log.add_system('Quit canceled after draft storage failure; '
                                      'inspect unsaved drafts before exiting')
            return False
        self._request_exit()
        return self._exit_requested

    def _request_exit(self) -> None:
        """Request clean exit (matching OpenClaw's requestExit)."""
        if self._exit_requested:
            return
        self._parent_drafts.poll(self._show_parent_drafts, flush=True)
        if self._parent_drafts.busy:
            if self._draft_quit_failures is None:
                self._draft_quit_failures = self._draft_write_failures()
                self._chat_log.add_system('Saving pending drafts before quitting; storage failure keeps the UI open')
            return
        self._draft_quit_failures = None
        self._exit_requested = True
        self._close_parent_drafts()
        self._retire_access_request()
        self._argument_feed.close()
        self._cancel_name_read(send=False)
        if self._child_panel is not None:
            self._close_children()
        self._composer.close()
        self._extension_dialog_queue.clear()
        self._extension_stack.clear()
        self._extension_dialog_panel = None
        if self._mcp_panel is not None:
            self._close_mcp()
        self._outputs.close()
        if self._transcript_panel is not None:
            self._close_transcript()
        if self._navigation_provider is not None:
            self._navigation_provider.close()
        if self._file_completions is not None:
            self._file_completions.close()
        self._artifact_loader.close()
        self._io_stop.set()
        self._tui.stop()
        with self._io_lock:
            loop, task = self._io_loop_ref, self._io_task
        if loop is not None and task is not None:
            try:
                loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:
                pass  # The owner already finished and closed its loop.

    def _queue_request(self, request: dict[str, Any], images: tuple[Attachment, ...] = ()) -> None:
        """Fence requests to the session connection visible to the UI."""
        self._send_queue.put({**request, "_connection_generation": self._connection_generation,
                              **({"_images": images} if images else {})})

    def _preserve_submission(self, value: str, reason: str) -> None:
        draft = self._editor.get_text()
        if draft != value:
            self._editor.set_text("\n\n".join(part for part in (value, draft) if part.strip()))
        self._chat_log.add_system(reason)
        self._tui.request_render()

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
        queued = len(self._pending_messages) + len(self._routed_inputs)
        if queued:
            parts.append(f"queued {queued}")
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
        thinking = st.get("thinking")
        if isinstance(thinking, str) and thinking in {"minimal", "low", "medium", "high", "xhigh"}:
            stats.append("thinking:" + str(thinking))
        # Keep live usage/context visible at ordinary widths; long model/group/
        # session identities remain inspectable in the header and native catalogs.
        return " | ".join((" ".join(stats), model, f"agent {group}", f"session {session_short}"))

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
        self._update_hints()

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

    def _refresh_work_summary(self) -> None:
        """Show bounded todo progress without rendering full checklists."""
        lines: list[str] = []
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
        if not self._pending_messages and not self._routed_inputs:
            self._layout.queue = None
            return
        content = (
            self._pending_messages[0] if self._pending_messages else next(iter(self._routed_inputs.values())).content
        )
        preview = sanitize_terminal_text(content).replace("\n", " ↵ ")
        count = len(self._pending_messages) + len(self._routed_inputs)
        keys = self._pending_ids.sync(len(self._pending_messages))
        key = keys[0] if keys else next(iter(self._routed_inputs))
        self._queue_summary.set_text(theme_system(f"queued {count} • next{self._queued_image_label(key)}: {preview}"))
        self._layout.queue = self._queue_container

    # ── Header/footer (matching OpenClaw's updateHeader/updateFooter) ─

    def _accept_extension_model_limits(self, message: dict[str, Any]) -> None:
        # Read-only display metadata, after the caller fences request/group/session.
        # An old result cannot switch a model or overwrite another model's limits.
        if "model_context_window" not in message or message.get("model") != self._state.get("model"):
            return
        value = message["model_context_window"]
        if type(value) is int and 0 <= value <= 2**31:
            self._context_window = value
        else:
            self._chat_log.add_system("Invalid extension model limits ignored")

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

    def _queued_image_label(self, key: str) -> str:
        count = len(self._local_images.get(key, ())) + len(self._routed_images.get(key, ()))
        return f" · images: {count}" if count else ""

    def _queue_command(self, args: str) -> None:
        try:
            command = parse_queue_command(args)
        except ValueError as exc:
            self._chat_log.add_system(str(exc))
            self._tui.request_render()
            return
        ids = self._pending_ids.sync(len(self._pending_messages))
        if command.action == "list":
            rows = [f"Queue ({len(ids) + len(self._routed_inputs)}):", QUEUE_HELP]
            rows.extend(f"{key} [next_turn · local{self._queued_image_label(key)}] {text}"
                        for key, text in zip(ids, self._pending_messages))
            rows.extend(f"{item.id} [{item.delivery} · {item.state}{self._queued_image_label(item.id)}] {item.content}"
                        for item in self._routed_inputs.values())
            self._chat_log.add_system("\n".join(rows))
        elif command.id in ids:
            position = ids.index(command.id)
            values, keys = list(self._pending_messages), list(ids)
            if command.action == "edit":
                values[position] = command.content
            elif command.action == "remove":
                values.pop(position)
                self._local_images.pop(keys[position], None)
                keys.pop(position)
            else:
                target = position + command.direction
                if not 0 <= target < len(values):
                    self._chat_log.add_system("Queue item is already at that boundary")
                    self._tui.request_render()
                    return
                values[position], values[target] = values[target], values[position]
                keys[position], keys[target] = keys[target], keys[position]
            self._pending_messages = deque(values)
            self._pending_ids.ids = deque(keys)
            self._chat_log.add_system(f"Queue {command.id}: {command.action} applied")
        elif command.id in self._routed_inputs:
            self._runtime_input_request(command.action, command.id, command.content, command.direction)
        else:
            self._chat_log.add_system("Queue item no longer pending; inspect /queue again")
        self._update_queue_summary()
        self._tui.request_render()

    def _send_runtime_input(self, delivery: str, content: str) -> None:
        if not content.strip() and not self._composer.items:
            self._chat_log.add_system("Explicit steering/follow-up requires text")
            self._tui.request_render()
            return
        images = tuple(self._composer.items.values())
        if not self._validate_draft_images(images):
            self._preserve_submission(content, "Explicit delivery not sent")
            return
        self._composer.take()
        self._input_sequence += 1
        input_id = f"i{self._input_sequence}"
        self._routed_images[input_id] = images
        self._routed_inputs[input_id] = QueueEntry(input_id, content, delivery, "sending", self._active_run_id or "")
        self._runtime_input_request("add", input_id, content, delivery=delivery)

    def _runtime_input_request(self, action: str, input_id: str, content: str = "", direction: int = 0,
                               *, delivery: str = "") -> None:
        run_id = self._active_run_id
        if (not self._connection_ready or not self._input_controls_supported or run_id is None
                or run_id == self._aborting_run_id or self._exit_requested):
            if action == "add":
                self._routed_inputs.pop(input_id, None)
                self._composer.restore(self._routed_images.pop(input_id, ()))
                self._preserve_submission(
                    content, "No ready active input owner (updated gateway required); draft preserved",
                )
            else:
                self._chat_log.add_system("Input owner unavailable; edit/remove was not sent")
                if action == "edit":
                    self._preserve_submission(content, "Replacement text preserved")
            self._tui.request_render()
            return
        request_id = f"c-{uuid.uuid4().hex}"
        self._input_requests[request_id] = (action, input_id, content, run_id)
        self._queue_request({"type": "input_control", "run_id": run_id, "group_id": self.group_id,
                             "request_id": request_id, "input_id": input_id, "action": action,
                             "content": content, "direction": direction, "delivery": delivery},
                            self._routed_images.get(input_id, ()) if action == "add" else ())
        self._update_queue_summary()
        self._tui.request_render()

    def _restore_uncertain_inputs(self, owner: str) -> None:
        texts = [item.content for item in self._routed_inputs.values() if item.owner == owner]
        for key, item in tuple(self._routed_inputs.items()):
            if item.owner == owner:
                self._composer.restore(self._routed_images.pop(key, ()))
        texts.extend(content for action, _key, content, run_id in self._input_requests.values()
                     if action == "edit" and run_id == owner)
        self._routed_inputs = {key: item for key, item in self._routed_inputs.items() if item.owner != owner}
        self._input_requests = {key: value for key, value in self._input_requests.items() if value[3] != owner}
        if texts:
            self._preserve_submission(
                "\n\n".join(texts), "Runtime input lacks a final receipt; inspect context before resending",
            )

    def _handle_input_event(self, msg: Mapping[str, Any]) -> None:
        kind = msg["type"]
        if kind == "input_ack":
            request = self._input_requests.get(str(msg.get("request_id", "")))
            if request is None or msg.get("run_id") != request[3]:
                return
            snapshot = msg.get("snapshot", [])
            if type(msg.get("success")) is not bool or (msg.get("success") and (
                not isinstance(snapshot, (list, tuple))
                or any(not isinstance(raw, Mapping) or not isinstance(raw.get("id"), str) for raw in snapshot)
            )):
                self._chat_log.add_system("Invalid input acknowledgement; request remains uncertain")
                self._tui.request_render()
                return
            self._input_requests.pop(str(msg["request_id"]))
            action, input_id, content, _owner = request
            if not msg.get("success"):
                if action == "add" and self._routed_inputs.pop(input_id, None) is not None:
                    self._composer.restore(self._routed_images.pop(input_id, ()))
                    self._preserve_submission(content, str(msg.get("error", "Input rejected")))
                elif action == "edit":
                    self._preserve_submission(content, "Edit was not applied; replacement kept in draft")
                else:
                    self._chat_log.add_system(str(msg.get("error", "Input rejected")))
            elif action == "remove":
                self._routed_inputs.pop(input_id, None)
                self._routed_images.pop(input_id, None)
            else:
                ordered: dict[str, QueueEntry] = {}
                for raw in msg.get("snapshot", ()):
                    key = str(raw["id"])
                    previous = self._routed_inputs.get(key)
                    if previous is not None:
                        body = content if action == "edit" and input_id == key else previous.content
                        ordered[key] = QueueEntry(key, body, previous.delivery, "accepted", previous.owner)
                self._routed_inputs = {**ordered, **{key: value for key, value in self._routed_inputs.items()
                                                   if key not in ordered}}
                self._chat_log.add_system(f"Input {input_id}: {action} accepted (not proof of consumption)")
        else:
            input_id = str(msg.get("id", ""))
            item = self._routed_inputs.get(input_id)
            if item is None or msg.get("run_id") != item.owner:
                return
            content = msg.get("content", item.content)
            if not isinstance(content, str):
                self._chat_log.add_system("Invalid input receipt; request remains uncertain")
                self._tui.request_render()
                return
            self._routed_inputs.pop(input_id)
            images = self._routed_images.pop(input_id, ())
            if kind == "input_consumed":
                content = self._transformed_inputs.pop(input_id, content)
                self._chat_log.close_assistant(item.owner)
                image_count = len(images)
                self._chat_log.add_user(content + (f"\n[Images: {image_count}]" if image_count else ""))
                self._editor.add_to_history(content)
            else:
                self._transformed_inputs.pop(input_id, None)
                self._composer.restore(images)
                self._preserve_submission(content, "Unconsumed runtime input returned; not automatically replayed")
        self._update_queue_summary()
        self._tui.request_render()

    # ── Input submission (matching OpenClaw's createEditorSubmitHandler) ─

    def _handle_submit(self, text: str, *, queued: bool = False, images: tuple[Attachment, ...] = ()) -> None:
        """Handle editor submit (matching OpenClaw's submit handler).

        Flow: clear editor → add to history → handle slash/message.
        """
        value = text.strip()
        if not value and not images and not self._composer.items:
            return

        # Add to editor history (matching OpenClaw)
        self._editor.add_to_history(value)

        lower = value.lower()
        if not queued:
            parts = value.split(None, 1)
            command, args = (parts[0].lower() if parts else ""), parts[1] if len(parts) > 1 else ""
            if self._compact_pending is not None and command not in {"/compact", "/status", "/quit", "/exit"}:
                self._preserve_submission(value, "Compaction owns this session; input kept in draft")
                return
            if command == '/help':
                from xdog.tui.command_help import command_help
                try:
                    text = command_help(self._editor._command_provider(), args)
                except (ValueError, TypeError) as exc:
                    text = f'Help unavailable: {exc}'
                self._chat_log.add_system(text)
                self._tui.request_render()
                return
            if command == "/name":
                self._name_session(args)
                return
            if command == "/compact":
                self._compact_command(args)
                return
            if command == "/artifact":
                self._load_command_artifact(args)
                return
            if command == "/thinking":
                if (not self._reasoning_supported or not self._connection_ready or self._active_run_id is not None
                        or self._reasoning_pending is not None or self._extension_model_pending is not None):
                    self._chat_log.add_system("Updated reasoning gateway and idle owner required")
                    return
                level = args.strip().lower()
                if level and level not in {"off", "minimal", "low", "medium", "high", "xhigh"}:
                    self._chat_log.add_system("Usage: /thinking [off|minimal|low|medium|high|xhigh]")
                    return
                rid = 'thinking-' + uuid.uuid4().hex
                self._reasoning_pending = rid
                self._queue_request({"type": "reasoning_control", "request_id": rid, "group_id": self.group_id,
                                     "session_id": self._gateway_session_id, **({"thinking": level} if level else {})})
                return
            if command == "/model":
                if args.strip() and not args.strip().startswith("ext-") and not self._native_models_supported:
                    self._chat_log.add_system("Updated native-model gateway required; no agent input or fallback")
                    return
                if (not self._extension_models_supported or not self._connection_ready
                        or self._active_run_id is not None or self._reasoning_pending is not None
                        or self._extension_model_pending is not None):
                    self._chat_log.add_system("Updated extension-model gateway and idle owner required")
                    return
                rid = 'model-' + uuid.uuid4().hex
                self._extension_model_pending = rid
                self._queue_request({"type": "extension_model_control", "request_id": rid, "group_id": self.group_id,
                                     "session_id": self._gateway_session_id, "model": args.strip()})
                return
            if command == '/login':
                from xdog.agent.extensions.provider_oauth_host import login_arguments
                try:
                    self._extension_command('extensions', 'auth ' + login_arguments(args))
                except ValueError as exc:
                    self._chat_log.add_system(str(exc))
                return
            if (command == "/extensions" or command.startswith("/ext-")
                    or command.removeprefix('/') in self._extension_snapshot.get('commands', {})):
                self._extension_command(command[1:], args)
                return
            if command == '/draft':
                self._parent_draft_command(args)
                return
            if command == "/agents":
                self._agents_command(args)
                self._tui.request_render()
                return
            if command == '/plan':
                self._chat_log.add_system('Planning mode UI was removed; use /readonly on|off|status')
                return
            if command == '/readonly':
                self._readonly_command(args)
                return
            if command == "/skills":
                self._skills_command(args)
                return
            if command == "/tools":
                self._open_tools(args)
                return
            if command == "/mcp":
                if args.strip().startswith("authorize "):
                    self._open_mcp()
                    if self._mcp_panel is not None:
                        self._mcp_panel.authorize_server(args.strip().partition(" ")[2])
                    return
                if args.strip() in {"", "browse"}:
                    self._open_mcp()
                    return
                values = args.split()
                action, server = (values[0] if values else "list"), (values[1] if len(values) == 2 else "")
                if (len(values) > 2 or action not in {"list", "status", "connect", "disconnect", "refresh"}
                        or action not in {"list", "status"} and not server):
                    self._chat_log.add_system("Usage: /mcp [list|connect SERVER|disconnect SERVER|refresh SERVER]")
                elif not self._connection_ready or not self._mcp_supported:
                    self._chat_log.add_system("MCP management needs a ready updated gateway")
                elif action not in {"list", "status"} and self._active_run_id is not None:
                    self._chat_log.add_system("MCP connection/schema changes require an idle group")
                else:
                    identifier = "mcp-" + uuid.uuid4().hex
                    self._mcp_requests.add(identifier)
                    self._queue_request({"type": "mcp_control", "request_id": identifier,
                                         "group_id": self.group_id, "action": action, "server": server})
                    self._chat_log.add_system("MCP control sent; configured commands require your explicit trust")
                self._tui.request_render()
                return
            if command in {"/copy", "/export", "/share"}:
                self._output_command(command[1:], args)
                return
            if command in {"/transcript", "/fullscreen"}:
                self._transcript_command("fullscreen" if command == "/fullscreen" else args)
                return
            if command in {"/attach", "/attachments", "/paste"}:
                root = self._file_completions.root if self._file_completions is not None else Path.cwd()
                self._composer.command(command[1:], args, root)
                return
            if command in {"/sessions", "/tree"}:
                self._open_navigation("sessions" if command == "/sessions" else f"tree|{self._state['session_id']}|")
                return
            if command in {"/resume", "/fork", "/branch", "/goto"}:
                self._chat_log.add_system("Claw history is read-only; historical route switching is not supported")
                self._tui.request_render()
                return
            if command == "/new":
                if args.strip():
                    self._chat_log.add_system("Usage: /new")
                    self._tui.request_render()
                else:
                    self._new_context()
                return
            if command in {"/queue", "/steer", "/follow-up"}:
                if command == "/queue":
                    self._queue_command(args)
                else:
                    self._send_runtime_input("steer" if command == "/steer" else "follow_up", args)
                return
            # Slash commands
            lower = value.lower()
            if lower == "/keys":
                self._show_keybindings()
                return
            if lower == "/editor":
                self._open_external_editor()
                return
            if lower in ("/quit", "/exit"):
                self._request_exit()
                return
            if lower == "/clear":
                if self._active_run_id is not None:
                    self._chat_log.add_system("cannot clear during an active run; cancel or wait first")
                else:
                    self._close_transcript()
                    self._chat_log.clear_all()
                self._tui.request_render()
                return
            if lower not in {"/reset", "/status"} and value.startswith("/"):
                self._chat_log.add_system(f"Unknown command: {value}")
                self._tui.request_render()
                return
        if not queued:
            images = tuple(self._composer.items.values())
        if not self._validate_draft_images(images):
            if queued:
                self._composer.restore(images)
            self._preserve_submission(value, "Submission not sent")
            return
        if not queued:
            self._composer.take()
        if self._session_context_stale:
            self._composer.restore(images)
            self._preserve_submission(value, "Group context changed; reconnect to synchronize before sending")
            return
        if not self._connection_ready:
            self._composer.restore(images)
            status = self._state["connection_status"]
            self._preserve_submission(value, f"gateway not ready ({status}); draft preserved")
            return
        if self._pending_command is not None and (self._active_run_id is None or value.startswith("/")):
            self._composer.restore(images)
            self._preserve_submission(value, "waiting for gateway command; draft preserved")
            return
        if not queued and lower == "/reset":
            if self._active_run_id is not None:
                self._chat_log.add_system("cannot reset during an active run; cancel or wait first")
                self._tui.request_render()
                return
            self._pending_command = "reset"
            self._queue_request({"type": "reset", "group_id": self.group_id})
            self._set_waiting(True)
            return
        if not queued and lower == "/status":
            self._pending_command = "status"
            self._queue_request({"type": "status"})
            if self._active_run_id is None:
                self._set_waiting(True)
            return

        if self._active_run_id is not None or self._native_submission is not None:
            if self._active_run_id is not None and self._aborting_run_id == self._active_run_id:
                self._composer.restore(images)
                self._editor.set_text(value)
                self._tui.request_render()
                return
            if len(self._pending_messages) >= 50:
                self._composer.restore(images)
                self._preserve_submission(value, "Local queue is full (50); draft preserved")
                return
            self._pending_messages.append(value)
            keys = self._pending_ids.sync(len(self._pending_messages))
            self._local_images[keys[-1]] = images
            self._update_queue_summary()
            self._tui.request_render()
            return

        self._start_message(value, images)

    def _start_message(self, value: str, images: tuple[Attachment, ...]) -> None:
        """Canonical native turn transport, without slash parsing or composer capture."""
        self._clear_todos()  # clear any finalized todos from previous turn
        self._active_images = images
        image_count = len(images)
        self._chat_log.add_user(value + (f"\n[Images: {image_count}]" if image_count else ""))
        self._active_run_id = f"run-{uuid.uuid4().hex}"
        self._active_text = value
        if self._native_supported:
            self._native_submission = NativeSubmission(self.group_id, self._native_namespace,
                                                       self._active_run_id, value, images)
        self._aborting_run_id = None
        self._set_waiting(True)
        self._streaming_output_chars = 0
        # Estimate input for this turn: all prior session context + this message
        self._turn_input_chars = self._usage["input"] * 4 + len(value)
        self._queue_request({
            "type": "message",
            "group_id": self._state.get("group_id", "main"),
            "content": value,
            "run_id": self._active_run_id,
            **({"session_id": self._gateway_session_id} if self._session_controls_supported else {}),
        }, images)

    def _dispatch_next_pending(self) -> None:
        if (
            self._exit_requested or not self._connection_ready or self._pending_command is not None
            or self._active_run_id is not None or self._native_submission is not None or not self._pending_messages
        ):
            return
        keys = self._pending_ids.sync(len(self._pending_messages))
        images = self._local_images.pop(keys[0], ())
        next_message = self._pending_messages.popleft()
        self._pending_ids.pop()
        self._update_queue_summary()
        self._handle_submit(next_message, queued=True, images=images)

    def _request_native_receipt(self) -> None:
        submission = self._native_submission
        if (submission is None or submission.phase != 'unknown' or self._native_query is not None
                or not self._connection_ready):
            return
        if (not self._native_supported or submission.namespace != self._native_namespace
                or submission.group_id != self.group_id):
            self._chat_log.add_system('Original message retained: receipt owner unavailable/changed; no replay')
            return
        identifier = 'receipt-' + uuid.uuid4().hex
        self._native_query = identifier, self._connection_generation
        self._queue_request({'type': 'input_status', 'request_id': identifier, 'group_id': submission.group_id,
                             'namespace': submission.namespace, 'message_id': 'native:' + submission.run_id,
                             'wait': True})
        self._set_activity_status('reconciling input receipt')

    def _native_receipt_result(self, message: Mapping[str, Any]) -> None:
        submission, query = self._native_submission, self._native_query
        if (submission is None or query is None or message.get('request_id') != query[0]
                or query[1] != self._connection_generation or submission.phase != 'unknown'):
            return
        if (message.get('success') is not True or message.get('namespace') != submission.namespace
                or self._native_namespace != submission.namespace or self.group_id != submission.group_id):
            self._native_query = None
            self._chat_log.add_system('Original message retained: receipt unavailable; no automatic replay')
            return
        receipt = message.get('message')
        if receipt is None:
            self._native_query = None
            if submission.acknowledged:
                self._chat_log.add_system('Acknowledged input missing from its journal; inspect, no replay')
                return
            if submission.cancel_requested:
                self._native_submission = None
                self._set_waiting(False)
                self._chat_log.add_system('Cancel requested; no accepted input found, original not replayed')
                self._dispatch_next_pending()
                return
            # Positive absence in the original namespace permits exactly the
            # original ID/body/images, never a new-ID reconstruction of a draft.
            self._native_submission = replace(submission, phase='awaiting_ack')
            self._active_run_id, self._active_text = submission.run_id, submission.text
            self._active_images = submission.images
            self._set_waiting(True)
            self._queue_request({'type': 'message', 'group_id': submission.group_id,
                'run_id': submission.run_id, 'content': submission.text,
                **({'session_id': self._gateway_session_id} if self._session_controls_supported else {})},
                submission.images)
            return
        try:
            checked = validate_receipt(receipt, submission)
        except ValueError:
            return  # A stale/wrong-input payload cannot retire the original query.
        self._native_query = None
        if checked['status'] not in TERMINAL:
            self._native_submission = replace(submission, acknowledged=True)
            self._chat_log.add_system('Original input remains queued/running; no new submission')
            return
        if checked['response']:
            self._chat_log.add_assistant(checked['response'])
        self._chat_log.add_system('Original input receipt: ' + checked['status']
                                  + (': ' + checked['error'] if checked['error'] else ''))
        self._native_submission = None
        self._set_waiting(False)
        self._dispatch_next_pending()

    def _native_finished(self, run_id: str) -> None:
        if self._native_submission is not None and self._native_submission.run_id == run_id:
            self._native_submission = None
            self._native_query = None

    # ── Per-frame polling (matching OpenClaw's event loop) ──

    def _poll(self) -> None:
        self._poll_parent_drafts()
        if self._finish_deferred_draft_quit():
            return
        draft_state = self._parent_drafts.autosave.durability
        durability = f'parent draft:{draft_state}' if draft_state not in {'off', 'empty'} else ''
        draft_changed = self._footer.set_durability(durability)
        changed = bool(self._recv_queue.drain(self._handle_response))
        if self._access_pending is not None and (time.monotonic() >= self._access_pending[3]
                or self._access_pending[2] != self._mcp_context_key()):
            self._retire_access_request('Access receipt lost/owner changed; inspect status explicitly, no replay')
        changed = draft_changed or changed
        changed = self._editor.poll_autocomplete() or changed
        if self._transcript_panel is not None:
            if self._source_request_id is not None and time.monotonic() >= self._source_deadline:
                self._pending_output = None
                self._cancel_source_read()
                self._transcript_panel.set_source_status("Full source timed out; refresh explicitly to retry",
                                                       busy=self._saved_scope is not None)
                changed = True
            changed = self._transcript_panel.poll() or changed
        if (self._pending_output is not None and self._source_request_id is None
                and self._source_records is not None and self._transcript_panel is not None
                and self._transcript_panel.settled):
            options, self._pending_output = self._pending_output, None
            self._launch_output(options)
        if (self._saved_scope is not None and self._source_session_id != self._gateway_session_id
                and self._transcript_panel is not None):
            self._cancel_source_read()
            self._source_records = None
            self._transcript_panel.replace_records(())
            self._transcript_panel.set_source_status('Saved source owner changed · refresh explicitly', busy=True)
            self._source_session_id = self._gateway_session_id
            changed = True
        if isinstance(self._transcript_panel, ToolInventoryPanel) and self._tools_session != self._gateway_session_id:
            self._cancel_tools()
            self._tools_session = self._gateway_session_id
            self._transcript_panel.invalidate_inventory('Tool inventory owner changed; r reads the current owner')
            changed = True
        if self._compact_pending is not None and (time.monotonic() >= self._compact_deadline
                or self._compact_pending[1] and self._compact_pending[1] != self._gateway_session_id):
            self._lose_compaction_receipt()
            changed = True
        if self._skills_request and (time.monotonic() >= self._skills_deadline
                or self._skills_session and self._skills_session != self._gateway_session_id):
            self._skills_request = ''
            self._chat_log.add_system('Skill receipt lost or owner changed; /skills lists current state, no replay')
            changed = True
        if self._tools_request and time.monotonic() >= self._tools_deadline:
            self._cancel_tools()
            if isinstance(self._transcript_panel, ToolInventoryPanel):
                self._transcript_panel.invalidate_inventory('Tool inventory timed out; r retries explicitly')
        if self._extension_dialog_queue or self._extension_dialog_panel is not None:
            self._sync_extension_dialog()
        if self._child_panel is not None:
            if self._child_context != self._mcp_context_key():
                self._close_children()
            else:
                if self._child_source_pending is not None and time.monotonic() >= self._child_source_deadline:
                    child = self._child_source_pending[1]
                    self._cancel_child_source()
                    self._child_panel.source(child, (), "Child source timed out; refresh explicitly")
                changed = self._child_panel.poll() or changed
        if self._mcp_panel is not None:
            if self._mcp_owner_context != self._mcp_context_key():
                self._close_mcp()
            else:
                changed = self._mcp_panel.poll() or changed
        changed = self._reviewed_url.poll(self._chat_log.add_system) or changed
        changed = self._outputs.poll() or changed
        self._sync_output_panel()
        if self._navigation_panel is not None:
            changed = self._navigation_panel.poll() or changed

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
            if channel == "scheduler":
                continue
            if role == "user" and (content or entry.get("image_count")):
                if entry.get("image_count"):
                    content += f"\n[Images: {entry['image_count']}]"
                if channel == "acp":
                    if self._acp_seen_session != self._gateway_session_id:
                        self._acp_seen_session = self._gateway_session_id
                        self._acp_seen.clear()
                    if isinstance(entry.get("message_id"), str):
                        self._acp_seen.add(entry["message_id"])
                    self._chat_log.add_system(content)
                else:
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
        if msg_type in {'extension_dialog', 'child_extension_dialog'}:
            question = msg.get('dialog')
            if not isinstance(question, dict) or not self._extension_stack.accepts(question, []):
                return  # Removed extension controls cannot touch a successor owner or draft.
        if msg_type == 'notification':
            if msg.get('group_id') == self.group_id and isinstance(msg.get('content'), str):
                self._chat_log.add_system(msg['content'])
                self._tui.request_render()
            return
        if msg_type == "acp_input_completed":
            if (msg.get("group_id") != self.group_id or not isinstance(msg.get("session_id"), str)
                    or not msg.get("session_id") or self._gateway_session_id
                    and msg.get("session_id") != self._gateway_session_id):
                return
            if not all(isinstance(msg.get(key), str) for key in
                       ("message_id", "source", "instruction", "response", "error")):
                return
            if self._acp_seen_session != msg["session_id"]:
                self._acp_seen_session = msg["session_id"]
                self._acp_seen.clear()
            identifier = msg["message_id"]
            if not identifier or identifier in self._acp_seen:
                return
            self._acp_seen.add(identifier)
            if not self._gateway_session_id:
                self._chat_log.add_system("ACP group session: " + msg["session_id"])
            self._chat_log.add_system(f"[External: {msg['source']}]\n{msg['instruction']}")
            if msg["response"]:
                self._chat_log.add_assistant(msg["response"])
            if msg["error"]:
                self._chat_log.add_system("External input failed: " + msg["error"])
            self._tui.request_render()
            return
        if msg_type in {"output_prepared", "output_done", "output_failed"}:
            self._outputs.handle(msg)
            self._sync_output_panel()
            return
        if msg_type == "attachment_result":
            self._composer.handle(msg, retained=self._retained_image_bytes())
            return
        if msg_type == "attachment_ack":
            if msg.get("error"):
                self._chat_log.add_system("Image upload failed: " + str(msg["error"]))
            return
        if msg_type == "log_message":
            self._chat_log.add_system(f"{msg['level']} {msg['logger']}: {msg['message']}")
            return
        if msg_type == "_connection_opened":
            generation = int(msg["generation"])
            if generation <= self._connection_generation:
                return
            self._extension_provider_auth_supported = False
            self._extension_provider_auth_generation = 0
            self._extension_auth_owner = None
            self._retire_access_request('Access receipt lost on reconnect; inspect status, no automatic replay'
                                        if self._access_pending else '')
            self._access_supported = False
            self._access_read_only, self._access_revision, self._access_session = None, -1, ''
            self._connection_generation = generation
            self._native_query = None
            self._native_supported = False
            self._native_namespace = ''
            if self._native_submission is not None:
                self._native_submission = replace(self._native_submission, phase='unknown')
                self._chat_log.drop_assistant(self._native_submission.run_id)
                self._active_run_id, self._active_text = None, None
                self._active_images = ()
                self._set_waiting(False)
            self._tools_request = ''
            self._tools_deadline = 0.0
            self._tool_inventory_supported = False
            self._tool_inventory_selection_supported = False
            self._session_cancel_supported = False
            self._saved_source_supported = False
            if self._saved_scope is not None and self._transcript_panel is not None:
                self._cancel_source_read()
                self._source_records = None
                self._transcript_panel.replace_records(())
                self._transcript_panel.set_source_status('Saved source reconnected · refresh explicitly', busy=True)
            if isinstance(self._transcript_panel, ToolInventoryPanel):
                self._transcript_panel.invalidate_inventory('Reconnected inventory; refresh after handshake')
            self._child_presets = ChildPresets()
            self._child_presets_supported = False
            self._child_model_supported = False
            self._child_config_supported = False
            self._child_images_supported = False
            self._child_queue_owners.clear()
            self._child_source_supported = False
            if self._child_panel is not None:
                self._close_children()
            self._children_supported = False
            self._child_queues_supported = False
            self._children_pending.clear()
            self._extension_command_owner = None
            self._extension_pending = None
            self._extension_auth_owner = None
            self._extension_cancel_scope = None
            self._extension_cancel_supported = False
            self._extension_model_pending = None
            self._extension_models_supported = False
            self._native_models_supported = False
            self._reasoning_pending = None
            self._reasoning_supported = False
            self._extension_dialog_queue.clear()
            self._extension_stack.clear()
            self._extension_dialog_panel = None
            self._extension_snapshot = {}
            self._extension_supported = False
            self._mcp_requests.clear()
            self._lose_compaction_receipt()
            self._compaction_supported = False
            self._skills_request = ""
            self._skills_deadline = 0.0
            self._skills_supported = False
            self._connection_ready = False
            self._pending_command = None
            self._argument_feed.reset()
            self._argument_requests.clear()
            self._argument_scopes.clear()
            self._argument_context_requests.clear()
            self._cancel_name_read(send=False)
            self._session_mutation_id = None
            self._navigation_requests.clear()
            self._output_epoch += 1
            self._pending_output = None
            if self._navigation_panel is not None:
                self._close_navigation()
            self._history_loading = False
            self._set_connection_status("syncing")
            return
        event_generation = msg.get("_connection_generation")
        if event_generation is not None and event_generation != self._connection_generation:
            return
        if msg_type == 'input_status_result':
            self._native_receipt_result(msg)
            return
        if msg_type == 'access_control_result':
            self._access_result(msg)
            return
        if msg_type == "reasoning_result":
            if (msg.get("request_id") != self._reasoning_pending or self._reasoning_pending is None
                    or msg.get("group_id") != self.group_id):
                return
            self._reasoning_pending = None
            actual = msg.get("session_id")
            if (msg.get("success") is True and not self._gateway_session_id and isinstance(actual, str)
                    and 0 < len(actual) <= 128):
                self._gateway_session_id = actual
                self._state["session_id"] = actual
            if msg.get("success") is not True:
                self._chat_log.add_system(str(msg.get("error", "Reasoning setting failed; no automatic retry")))
            elif (msg.get("session_id") == self._gateway_session_id and msg.get("model") == self._state.get("model")
                    and isinstance(msg.get("thinking"), str)
                    and msg["thinking"] in {"off", "minimal", "low", "medium", "high", "xhigh"}
                    and isinstance(msg.get("levels"), list) and 1 <= len(msg["levels"]) <= 6
                    and msg["thinking"] in msg["levels"] and "off" in msg["levels"]
                    and all(isinstance(level, str) and level in {"off", "minimal", "low", "medium", "high", "xhigh"}
                            for level in msg["levels"])):
                self._state["thinking"] = msg["thinking"]
                self._chat_log.add_system("Thinking level: " + msg["thinking"] +
                                         " · available: " + ", ".join(msg["levels"]))
                self._update_footer()
            else:
                self._chat_log.add_system("Reasoning model/session metadata changed; inspect before retrying")
            self._tui.request_render()
            return
        if msg_type == "extension_model_result":
            if msg.get("request_id") != self._extension_model_pending or msg.get("group_id") != self.group_id:
                return
            self._extension_model_pending = None
            if msg.get("success") is not True:
                self._chat_log.add_system(str(msg.get("error", "Model selection failed")))
            else:
                actual = msg.get("session_id")
                if self._gateway_session_id and actual != self._gateway_session_id:
                    self._chat_log.add_system("Model session changed; refresh before selecting")
                    return
                self._gateway_session_id = str(actual or "")
                self._state["session_id"] = self._gateway_session_id
                self._state["model"] = str(msg.get("model", ""))
                thinking = msg.get("thinking")
                if isinstance(thinking, str) and thinking in {"off", "minimal", "low", "medium", "high", "xhigh"}:
                    self._state["thinking"] = thinking
                self._accept_extension_model_limits(msg)
                label = "Extension model: " if self._state["model"].startswith("ext-") else "Model: "
                self._chat_log.add_system(label + self._state["model"] + "\n" +
                    "\n".join(str(row.get("id", "")) for row in msg.get("models", []) if isinstance(row, dict)))
                self._update_header()
                self._update_footer()
            self._tui.request_render()
            return
        if msg_type == 'session_compact_result':
            compact_pending = self._compact_pending
            if (compact_pending is None or msg.get('request_id') != compact_pending[0]
                    or msg.get('group_id') != self.group_id):
                return
            self._compact_pending = None
            actual = msg.get('session_id')
            if (not self._connection_ready or not self._compaction_supported
                    or not isinstance(actual, str) or compact_pending[1] and actual != compact_pending[1]
                    or self._gateway_session_id and actual != self._gateway_session_id):
                self._chat_log.add_system('Compaction owner changed; inspect current context, no replay')
            elif msg.get('success') is not True:
                self._chat_log.add_system(str(msg.get('error', 'Compaction failed; inspect context before retrying')))
            elif (type(msg.get('before')) is not int or type(msg.get('after')) is not int
                    or msg['before'] < 0 or msg['after'] < 0 or type(msg.get('changed')) is not bool):
                self._chat_log.add_system('Invalid compaction receipt; inspect context, no replay')
            else:
                if not self._gateway_session_id:
                    self._gateway_session_id = actual
                    self._state['session_id'] = actual
                self._chat_log.add_system(f"Compacted: {msg['before']} → {msg['after']} transcript entries"
                    if msg['changed'] else 'No older context to compact; no summary request needed')
                recovery = msg.get('recovery_branch_id')
                if msg['changed'] and isinstance(recovery, str) and recovery:
                    self._chat_log.add_system('Recovery checkpoint saved; /tree → Before compaction')
                if msg['changed'] and self._transcript_panel is not None:
                    self._cancel_source_read()
                    self._transcript_panel.set_source_status('Context compacted; r refreshes the full source')
            self._set_activity_status('idle')
            self._update_hints()
            self._tui.request_render()
            return
        if msg_type == 'skills_result':
            if (not self._skills_request or msg.get('request_id') != self._skills_request
                    or msg.get('group_id') != self.group_id):
                return
            self._skills_request = ''
            actual_skill_session = msg.get('session_id', '')
            active, skills = msg.get('active'), msg.get('skills')
            if (not self._connection_ready or not self._skills_supported
                    or msg.get('success') is not True
                    or not isinstance(actual_skill_session, str) or not actual_skill_session
                    or self._skills_session and actual_skill_session != self._skills_session
                    or self._gateway_session_id and actual_skill_session != self._gateway_session_id):
                self._chat_log.add_system(str(msg.get('error', 'Skill owner changed; no replay')))
            elif (not isinstance(active, list) or len(active) > 128
                    or any(not isinstance(slug, str) for slug in active)
                    or not isinstance(skills, list) or len(skills) > 2048
                    or any(not isinstance(item, dict)
                           or any(not isinstance(item.get(key), str) for key in ('slug', 'scope', 'description'))
                           or type(item.get('active')) is not bool for item in skills)):
                self._chat_log.add_system('Invalid skill catalog; no replay')
            else:
                if not self._gateway_session_id:
                    self._gateway_session_id = actual_skill_session
                    self._state['session_id'] = actual_skill_session
                self._chat_log.add_system('Skills · active: ' + ', '.join(active) + '\n' +
                    '\n'.join(f"{item['slug']} [{item['scope']}] "
                        + ('[active] ' if item['active'] else '') + item['description'] for item in skills)
                    + '\nSelection only; no model/tool/permission change')
            self._tui.request_render()
            return
        if msg_type == 'tool_inventory_result':
            inventory_panel = self._transcript_panel
            if (not isinstance(inventory_panel, ToolInventoryPanel) or msg.get('request_id') != self._tools_request
                    or msg.get('group_id') != self.group_id
                    or msg.get('requested_session_id', msg.get('session_id')) != self._tools_session
                    or self._gateway_session_id and msg.get('session_id') != self._gateway_session_id):
                return
            self._tools_request = ''
            self._tools_deadline = 0.0
            try:
                if not self._connection_ready or not self._tool_inventory_supported or msg.get('success') is not True:
                    raise ValueError('Unavailable inventory')
                from xdog.agent.extensions.tool_inventory import validate as validate_tools
                actual_session = msg.get('session_id')
                if not isinstance(actual_session, str) or not actual_session:
                    raise ValueError('Missing inventory session owner')
                data = validate_tools(msg.get('snapshot'), session_id=actual_session, owner_id=actual_session)
                if not self._gateway_session_id:
                    self._gateway_session_id = actual_session
                    self._state['session_id'] = actual_session
                    self._update_header()
                self._tools_session = actual_session
                inventory_panel.publish_inventory(inventory_records(data))
            except (ValueError, TypeError, RuntimeError):
                inventory_panel.invalidate_inventory('Inventory unavailable · r retries explicitly')
            self._tui.request_render()
            return
        if msg_type in {"child_extension_dialog", "child_extension_dialog_closed"}:
            if (not isinstance(msg.get("child_id"), str) or not re.fullmatch(r"[a-f0-9]{12}", msg["child_id"])
                    or not isinstance(msg.get("child_owner"), str)
                    or not re.fullmatch(r"[a-f0-9]{32}", msg["child_owner"])):
                return
            if (msg.get("group_id") != self.group_id or msg.get("session_id") != self._gateway_session_id
                    or not isinstance(msg.get("child_id"), str) or not isinstance(msg.get("child_owner"), str)):
                # An initial explicit start may create the session before its result arrives.
                pending_origin = self._children_pending.get(str(msg.get("origin_request", "")))
                origin_valid = (pending_origin is not None and pending_origin[0] == "start" and not pending_origin[1]
                                or self._active_run_id is not None and msg.get("origin_run") == self._active_run_id)
                if (msg_type != "child_extension_dialog" or self._gateway_session_id
                        or msg.get("group_id") != self.group_id or not origin_valid):
                    return
                if not isinstance(msg.get("session_id"), str) or not msg["session_id"]:
                    return
                self._gateway_session_id = msg["session_id"]
                self._state["session_id"] = msg["session_id"]
                if self._child_panel is not None:
                    self._child_context = self._mcp_context_key()
            if msg_type == "child_extension_dialog":
                try:
                    dialog = validate_dialog(msg.get("dialog"))
                except (ValueError, TypeError):
                    return
                item = {**dialog, "extension": str(msg.get("extension", "")), "child_id": msg["child_id"],
                        "child_owner": msg["child_owner"], "session_id": msg["session_id"]}
                if not self._extension_stack.accepts(item, self._extension_dialog_queue):
                    self._queue_request({"type": "child_extension_dialog_answer", "id": item["id"], "value": None,
                        "group_id": self.group_id, "session_id": item["session_id"], "child_id": item["child_id"],
                        "child_owner": item["child_owner"]})
                    return
                if not any(value["id"] == item["id"] for value in self._extension_dialog_queue):
                    self._extension_dialog_queue.append(item)
            else:
                child_dialog_id = str(msg.get("id", ""))
                self._extension_dialog_queue = [item for item in self._extension_dialog_queue
                                                if item["id"] != child_dialog_id]
                if (self._extension_dialog_panel is not None
                        and self._extension_dialog_panel.dialog["id"] == child_dialog_id):
                    self._extension_dialog_panel = None
            self._sync_extension_dialog()
            self._update_hints()
            self._tui.request_render()
            return
        if msg_type in {"extension_dialog", "extension_dialog_closed"}:
            if msg.get("group_id") != self.group_id:
                return
            valid = (msg.get("run_id") == self._active_run_id and self._active_run_id is not None
                     or self._extension_pending is not None and msg.get("request_id") == self._extension_pending[0])
            if (valid and self._extension_pending is not None and not self._extension_pending[1]
                    and not self._gateway_session_id and isinstance(msg.get("session_id"), str)):
                actual = msg["session_id"]
                auth_bootstrap = self._extension_auth_owner
                old_context = self._mcp_context_key()
                self._gateway_session_id = actual
                self._state["session_id"] = actual
                self._extension_pending = self._extension_pending[0], actual
                if auth_bootstrap == (self._extension_pending[0], old_context,
                                      self._extension_provider_auth_generation):
                    self._extension_auth_owner = (self._extension_pending[0], self._mcp_context_key(),
                                                  self._extension_provider_auth_generation)
                self._session_context_stale = False
            if msg_type == "extension_dialog":
                if not valid or len(self._extension_dialog_queue) >= 8:
                    return
                try:
                    item = validate_dialog(msg.get("dialog"))
                    item["extension"] = str(msg.get("extension", ""))
                    item["session_id"] = self._gateway_session_id
                    if item.get('operation') == 'commands':
                        item.update(_command_menu_run=msg.get('run_id'), _command_menu_command=msg.get('request_id'))
                except (ValueError, TypeError):
                    self._chat_log.add_system("Invalid extension dialog ignored")
                    return
                if not self._extension_stack.accepts(item, self._extension_dialog_queue):
                    self._queue_request({"type": "extension_dialog_answer", "id": item["id"], "value": None})
                    return
                if not any(row["id"] == item["id"] for row in self._extension_dialog_queue):
                    self._extension_dialog_queue.append(item)
            else:
                key = msg.get("id")
                self._extension_dialog_queue = [item for item in self._extension_dialog_queue if item["id"] != key]
                if self._extension_dialog_panel is not None and self._extension_dialog_panel.dialog["id"] == key:
                    self._extension_dialog_panel = None
            self._sync_extension_dialog()
            self._update_hints()
            self._tui.request_render()
            return
        if msg_type == "extension_dialog_ack":
            return
        if msg_type in {"artifact_loaded", "artifact_failed"}:
            self._artifact_result(msg)
            return
        if msg_type == "extension_result":
            extension_pending = self._extension_pending
            if (extension_pending is None or extension_pending[0] != msg.get("request_id")
                    or msg.get("group_id") != self.group_id):
                return
            self._extension_pending = None
            self._extension_auth_owner = None
            self._extension_cancel_scope = None
            if extension_pending[1] and extension_pending[1] != self._gateway_session_id:
                self._chat_log.add_system("Extension session changed; inspect status before retrying")
                return
            if msg.get("success") is not True:
                self._chat_log.add_system(str(msg.get("error", "Extension control failed")))
                failure = msg.get('failure_snapshot')
                if failure is not None:
                    try:
                        if (not isinstance(failure, dict) or set(failure) != {'version', 'session_id', 'snapshot'}
                                or type(failure['version']) is not int or failure['version'] != 1
                                or not extension_pending[1] or failure['session_id'] != extension_pending[1]
                                or failure['session_id'] != self._gateway_session_id):
                            raise ValueError('Unowned extension failure snapshot')
                        self._extension_snapshot = validate_snapshot(failure['snapshot'])
                    except (ValueError, TypeError):
                        self._chat_log.add_system('Invalid/foreign extension failure metadata ignored')
            else:
                actual = msg.get("session_id")
                if (not isinstance(actual, str)
                        or self._gateway_session_id and actual != self._gateway_session_id):
                    self._chat_log.add_system("Unexpected extension session; refresh before retrying")
                    return
                if not self._gateway_session_id:
                    self._gateway_session_id = actual
                    self._state["session_id"] = actual
                if not extension_pending[1]:
                    self._session_context_stale = False
                self._accept_extension_model_limits(msg)
                self._update_footer()
                if isinstance(msg.get("snapshot"), dict):
                    try:
                        self._extension_snapshot = validate_snapshot(msg["snapshot"])
                    except (ValueError, TypeError):
                        self._chat_log.add_system("Invalid extension snapshot ignored")
                for notice in msg.get("notices", [])[:8]:
                    self._chat_log.add_system(str(notice))
                if "command_content" in msg:
                    try:
                        if not self._extension_command_results_supported:
                            raise ValueError("Updated rich-command gateway required")
                        artifact = ExtensionCommandResult.from_dict(msg["command_content"])
                        self._chat_log.add_child(ExtensionCommand(artifact.command, artifact.result, artifact.text(),
                                                                  owner=self._mcp_context_key()))
                    except (ValueError, RuntimeError):
                        self._chat_log.add_system("Invalid/unnegotiated extension command artifact rejected")
                else:
                    self._chat_log.add_system(str(msg.get("output") or json.dumps(self._extension_snapshot)))
            self._tui.request_render()
            return
        if msg_type in {"child_source_ready", "child_source_failed", "child_source_progress", "child_source_loading"}:
            pending_source = self._child_source_pending
            if (pending_source is not None and self._child_panel is not None
                    and msg.get("request_id") == pending_source[0] and msg.get("group_id") == self.group_id
                    and msg.get("session_id") == pending_source[2] and self._child_context == self._mcp_context_key()):
                if msg_type == "child_source_ready":
                    self._child_panel.source(pending_source[1], tuple(msg.get("records", ())))
                    self._child_source_pending = None
                elif msg_type == "child_source_failed":
                    self._child_panel.source(pending_source[1], (), str(msg.get("error", "Child source failed")))
                    self._cancel_child_source()
                else:
                    self._child_source_deadline = time.monotonic() + 30
                self._tui.request_render()
            return
        if msg_type == "child_result":
            if 'child_return_owner' in msg or 'queue_view' in msg:
                return  # Removed live-return receipts cannot settle a current native request.
            request_id = str(msg.get("request_id", ""))
            queue_owner = self._child_queue_owners.pop(request_id, None)
            browser_job = request_id in self._child_ui_requests
            self._child_ui_requests.discard(request_id)
            child_pending = self._children_pending.pop(request_id, None)
            if (child_pending is None or msg.get("group_id") != self.group_id
                    or child_pending[1] != self._gateway_session_id and not (
                        child_pending[0] == "start" and not child_pending[1]
                        and msg.get("session_id") == self._gateway_session_id)):
                return
            if queue_owner is not None and (self._child_panel is None or self._child_panel.queue is None
                    or queue_owner != (self._child_panel.queue.owner_token, self._child_panel.owner_token,
                                       self.group_id, self._gateway_session_id, self._child_panel.queue.child)
                    or msg.get('session_id') != queue_owner[3]
                    or self._child_context != self._mcp_context_key()):
                return  # Late replies cannot adopt/cancel a replacement queue view's draft.
            if not msg.get("success"):
                self._chat_log.add_system("Child control failed: " + str(msg.get("error", "unknown error")))
                if browser_job and self._child_panel is not None:
                    self._child_panel.complete(child_pending[0], str(msg.get('error', 'unknown error')))
            elif not isinstance(msg.get("data"), dict) or not isinstance(msg.get("session_id"), str):
                self._chat_log.add_system("Invalid child control response")
                if browser_job and self._child_panel is not None:
                    self._child_panel.complete(child_pending[0],
                                               'Invalid child receipt; admission uncertain, draft retained')
            else:
                actual = msg["session_id"]
                if actual != self._gateway_session_id:
                    if child_pending[0] != "start" or child_pending[1]:
                        self._chat_log.add_system("Unexpected child session; refresh before retrying")
                        if browser_job and self._child_panel is not None:
                            self._child_panel.complete(child_pending[0], 'Child receipt owner changed; draft retained')
                        return
                    self._gateway_session_id = actual
                    self._state["session_id"] = actual
                    self._output_epoch += 1
                if browser_job and self._child_panel is not None:
                    self._child_context = self._mcp_context_key()
                    try:
                        if child_pending[0] == "presets":
                            self._child_presets = ChildPresets.from_snapshot(msg["data"].get("presets"))
                            self._child_panel.publish_presets(self._child_presets.snapshot(),
                                                              str(msg["data"].get("error", "")))
                        elif child_pending[0] == 'queue':
                            self._child_panel.complete('queue', data=msg['data'])
                        elif "children" in msg["data"]:
                            self._child_panel.complete(child_pending[0], data=msg['data'])
                            self._child_panel.publish(child_entries(msg["data"]), str(msg["data"].get("error", "")))
                            if self._child_target:
                                target, self._child_target = self._child_target, ""
                                self._child_panel.inspect(target)
                        else:
                            self._child_panel.complete(child_pending[0], data=msg['data'])
                            self._child_panel.request_list()
                    except (ValueError, TypeError) as exc:
                        if child_pending[0] == "presets":
                            self._child_presets = ChildPresets()
                            self._child_panel.publish_presets((), "Preset catalog rejected: " + str(exc))
                        self._child_panel.fail(str(exc))
                elif child_pending[0] == "start":
                    try:
                        roster = child_entries(msg["data"])
                        started_child = next(row for row in roster if row.id == msg["data"].get("child_id"))
                    except (ValueError, TypeError, StopIteration):
                        self._chat_log.add_system("Invalid child start receipt; inspect roster before retrying")
                    else:
                        self._chat_log.add_system(
                            f"Child {started_child.id} started ({started_child.access}); "
                            f"/agents inspect {started_child.id}")
                else:
                    self._chat_log.add_system(json.dumps(msg["data"], indent=2, ensure_ascii=True))
            if browser_job and self._child_panel is not None and not msg.get("success"):
                if child_pending[0] == "presets":
                    self._child_presets = ChildPresets()
                    self._child_panel.publish_presets((), str(msg.get("error", "Preset catalog failed")))
                self._child_panel.complete(child_pending[0], str(msg.get("error", "Child control failed")))
            self._tui.request_render()
            return
        if msg_type == "mcp_view_failed":
            if msg.get("id") == self._mcp_active:
                self._mcp_publish(self._mcp_action, error=str(msg.get("error", "MCP read failed")))
            return
        if msg_type == "mcp_data_ready":
            if msg.get("request_id") == self._mcp_active:
                self._mcp_publish(self._mcp_action, msg.get("records", ()))
            return
        if msg_type == "mcp_data_failed":
            if msg.get("request_id") == self._mcp_active:
                self._mcp_publish(self._mcp_action, error=str(msg.get("error", "MCP read failed")))
            return
        if msg_type == "mcp_auth_result":
            request_id = str(msg.get("request_id", ""))
            if request_id not in self._mcp_auth_requests:
                return
            self._mcp_auth_requests.discard(request_id)
            if (request_id != self._mcp_active or msg.get("group_id") != self.group_id
                    or msg.get("lease_id") != self._mcp_auth_lease or self._mcp_panel is None
                    or self._mcp_owner_context != self._mcp_context_key()):
                return
            self._mcp_publish(self._mcp_action, msg.get("data", {}),
                              "" if msg.get("success") else str(msg.get("error", "Authorization failed")))
            return
        if msg_type == "mcp_result":
            owner_request = self._mcp_owner_request
            if owner_request is not None and msg.get('request_id') == owner_request[0]:
                if (self._mcp_panel is None or self._mcp_active != owner_request[0]
                        or self._mcp_auth_lease != owner_request[1] or msg.get('view') != owner_request[1]
                        or self._mcp_context_key() != owner_request[2] or msg.get('group_id') != self.group_id
                        or msg.get('expected_session') != '' or self._gateway_session_id):
                    return  # A closed/replaced/foreign view cannot adopt the selected session.
                self._mcp_owner_request = None
                self._mcp_requests.discard(owner_request[0])
                selected = msg.get('session_id')
                if (msg.get('success') is not True or not self._mcp_browser_owner_supported
                        or not isinstance(selected, str) or not 0 < len(selected) <= 128):
                    self._mcp_publish('list', error=str(msg.get('error', 'MCP owner unavailable; reopen explicitly')))
                    return
                self._gateway_session_id = selected
                self._state['session_id'] = selected
                self._mcp_auth_session = selected
                self._mcp_owner_context = self._mcp_context_key()
                self._mcp_publish('list', tuple(msg.get('servers', ())))
                return
            if msg.get("request_id") == self._mcp_active:
                self._mcp_publish(self._mcp_action, tuple(msg.get("servers", ())),
                                  "" if msg.get("success") else str(msg.get("error", "MCP operation failed")))
                self._mcp_requests.discard(str(msg.get("request_id")))
                return
            identifier = msg.get("request_id")
            if (isinstance(identifier, str) and identifier in self._mcp_requests
                    and msg.get("group_id") == self.group_id):
                self._mcp_requests.remove(identifier)
                self._chat_log.add_system(json.dumps(msg.get("servers", ()), indent=2) if msg.get("success")
                                          else str(msg.get("error", "MCP operation failed")))
            return
        if msg_type in {"source_loading", "source_progress", "source_ready", "source_failed"}:
            self._handle_source_event(msg)
            return
        if msg_type in {"internal_protocol_error", "protocol_error"}:
            self._chat_log.add_system(theme_error(str(msg["message"])))
            return
        if msg_type == "error" and not msg.get("run_id"):
            if self._pending_command is not None or self._active_run_id is None:
                self._pending_command = None
                if self._active_run_id is None:
                    self._set_waiting(False)
                self._chat_log.add_system(theme_error(f"Error: {msg.get('message', 'Unknown error')}"))
                self._dispatch_next_pending()
                return
            # Gateway validation failures (e.g. unknown group) can predate the
            # run acknowledgement and carry no run ID. They still settle input.
            msg["run_id"] = self._active_run_id
        if msg_type == "todo" and msg.get("run_id"):
            run_id = str(msg["run_id"])
            if run_id != self._active_run_id or run_id == self._aborting_run_id:
                return

        if msg_type in {"input_transformed", "input_rejected", "input_handled"}:
            run = str(msg.get("run_id", ""))
            if run != self._active_run_id or msg.get("group_id") != self.group_id:
                return
            key = str(msg.get("id", ""))
            if msg_type == "input_handled":
                if key:
                    self._routed_inputs.pop(key, None)
                    self._composer.restore(self._routed_images.pop(key, ()))
                    self._transformed_inputs.pop(key, None)
                else:
                    self._composer.restore(self._active_images)
                    self._active_images = ()
                    self._active_text = None  # A known handled request is not a later uncertain abort/replay draft.
                self._chat_log.add_system("Input handled by extension; no model/history admission. "
                                          "Original images remain editable.\n" + str(msg.get("feedback", "")))
                self._update_queue_summary()
            elif msg_type == "input_transformed":
                text = str(msg.get("content", ""))
                if key and key in self._routed_inputs:
                    self._transformed_inputs[key] = text
                elif msg.get("source") == "prompt":
                    self._chat_log.transform_user_input(str(msg.get("original", "")), text,
                                                        int(msg.get("image_count", 0)))
                self._chat_log.add_system("Submitted input transformed before model/history admission")
            elif not key and msg.get("source") == "prompt":
                self._restore_pending_draft(run, runtime=False)
                self._chat_log.add_system("Input not admitted; original text/images restored, no automatic replay")
            self._tui.request_render()
            return
        if msg_type in {"input_ack", "input_consumed", "input_returned"}:
            self._handle_input_event(msg)
            return
        if msg_type in {"session_result", "session_changed"}:
            self._handle_session_event(msg)
            return
        if msg_type == 'argument_completion_result':
            rid = str(msg.get('request_id', ''))
            request = self._argument_requests.pop(rid, None)
            self._argument_scopes.pop(rid, None)
            contextual = rid in self._argument_context_requests
            self._argument_context_requests.discard(rid)
            if request is None:
                return
            argument_rows: list[dict[str, Any]] | None = None
            status = 'Argument completion unavailable; no cached fallback'
            if (msg.get('group_id') == self.group_id and msg.get('session_id') == self._gateway_session_id
                    and request.owner == self._mcp_context_key()
                    and (not contextual or self._argument_context_supported)):
                try:
                    from xdog.agent.extensions.argument_completion import items
                    argument_rows = items(msg.get('items')) if msg.get('status') == 'ok' else None
                    if isinstance(msg.get('snapshot'), dict):
                        self._extension_snapshot = validate_snapshot(msg['snapshot'])
                    status = '' if argument_rows is not None else status
                except (ValueError, TypeError):
                    argument_rows = None
            self._argument_feed.publish(request, argument_rows, status)
            self._editor.poll_autocomplete()
            self._tui.request_render()
            return
        if msg_type == "pong":
            self._access_supported = type(msg.get('access_controls')) is int and msg.get('access_controls') == 1
            if not self._access_supported:
                self._retire_access_request()
                self._access_read_only, self._access_revision, self._access_session = None, -1, ''
            auth_generation = msg.get('extension_provider_auth_generation', -1)
            self._extension_provider_auth_supported = (type(msg.get('extension_provider_auth')) is int
                and msg['extension_provider_auth'] == 1 and type(auth_generation) is int and auth_generation >= 0)
            if (not self._extension_provider_auth_supported
                    or auth_generation != self._extension_provider_auth_generation):
                self._extension_auth_owner = None
                from xdog.tui.components.extension_provider_auth import ExtensionProviderAuth
                if isinstance(self._extension_dialog_panel, ExtensionProviderAuth):
                    self._extension_dialog_panel.focused = False  # Clear its private code on withdrawal.
            self._extension_provider_auth_generation = auth_generation if type(auth_generation) is int else -1
            self._extension_models_supported = type(msg.get("extension_models")) is int and msg["extension_models"] == 1
            self._native_models_supported = type(msg.get("native_models")) is int and msg["native_models"] == 1
            self._reasoning_supported = type(msg.get("reasoning_controls")) is int and msg["reasoning_controls"] == 1
            self._extension_supported = type(msg.get("extension_controls")) is int and msg["extension_controls"] == 1
            if isinstance(msg.get("extension_snapshot"), dict):
                try:
                    self._extension_snapshot = validate_snapshot(msg["extension_snapshot"])
                except (ValueError, TypeError):
                    self._extension_supported = False
                    self._chat_log.add_system("Invalid extension registrations; controls disabled")
            self._child_images_supported = type(msg.get('child_images')) is int and msg.get('child_images') == 1
            self._child_presets_supported = type(msg.get("child_config")) is int and msg.get("child_config") == 3
            self._child_model_supported = (type(msg.get("child_config")) is int and msg.get("child_config") in {2, 3})
            self._child_config_supported = (type(msg.get("child_config")) is int
                                            and msg.get("child_config") in {1, 2, 3})
            self._child_source_supported = type(msg.get("child_source")) is int and msg.get("child_source") == 1
            self._children_supported = type(msg.get("child_controls")) is int and msg.get("child_controls") == 1
            self._child_queues_supported = type(msg.get('child_queues')) is int and msg.get('child_queues') == 1
            if self._child_panel is not None:
                self._child_panel.queue_enabled = self._child_queues_supported
            actual = str(msg.get("session_id") or "")
            refresh_current = (self._has_connected and actual != self._gateway_session_id
                               and msg.get("session_controls") == 1 and self._active_run_id is None)
            if self._has_connected and actual != self._gateway_session_id and msg.get("session_controls") == 1:
                self._session_context_stale = not refresh_current
            if refresh_current:
                # Synchronize only the current group context; no historical
                # activation or user/model input is sent after reconnect.
                self._output_epoch += 1
                self._chat_log.clear_all()
                self._editor.reset_history()
                self._history_tools.clear()
                self._usage = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
            if msg.get("session_controls") != 1:
                self._session_context_stale = False
            if self._transcript_panel is not None and actual != self._gateway_session_id:
                self._close_transcript()
            self._gateway_session_id = actual
            self._session_cancel_supported = (type(msg.get('session_navigation_cancel')) is int
                                              and msg.get('session_navigation_cancel') == 1)
            self._saved_timeline_supported = (type(msg.get('saved_timeline')) is int
                                               and msg.get('saved_timeline') == 1)
            self._saved_source_supported = type(msg.get('saved_source')) is int and msg.get('saved_source') == 1
            if (self._saved_scope is not None and self._transcript_panel is not None
                    and (not self._saved_source_supported
                         or self._saved_scope[1] == '@timeline' and not self._saved_timeline_supported)):
                self._cancel_source_read()
                self._source_records = None
                self._transcript_panel.replace_records(())
                self._transcript_panel.set_source_status('Saved source capability withdrawn · no live fallback',
                                                       busy=True)
            self._compaction_supported = (type(msg.get("session_compaction")) is int
                                          and msg.get("session_compaction") == 1)
            if not self._compaction_supported:
                self._lose_compaction_receipt()
            self._skills_supported = type(msg.get("skill_controls")) is int and msg.get("skill_controls") == 1
            if not self._skills_supported:
                self._skills_request = ""
            self._mcp_refresh_supported = type(msg.get("mcp_refresh")) is int and msg.get("mcp_refresh") == 1
            self._mcp_credentials_supported = (type(msg.get("mcp_credentials")) is int
                                               and msg.get("mcp_credentials") == 1)
            self._mcp_auth_supported = type(msg.get("mcp_auth")) is int and msg.get("mcp_auth") == 1
            self._extension_shortcuts_supported = (type(msg.get("extension_shortcuts")) is int
                                                   and msg.get("extension_shortcuts") == 1)
            self._extension_session_inspection_supported = (type(msg.get("extension_session_inspection")) is int
                                                            and msg.get("extension_session_inspection") == 1)
            self._argument_context_supported = (type(msg.get('extension_argument_context')) is int
                                                and msg.get('extension_argument_context') == 1)
            if not self._argument_context_supported and self._argument_context_requests:
                self._argument_feed.dismiss(self._editor.get_text(), self._editor._cursor)
            self._argument_cancel_supported = (type(msg.get('extension_argument_cancel')) is int
                                               and msg.get('extension_argument_cancel') == 1)
            self._argument_supported = (type(msg.get('extension_argument_completion')) is int
                                        and msg.get('extension_argument_completion') == 1)
            if not self._argument_supported:
                self._argument_feed.invalidate()
            self._extension_command_catalog_supported = (type(msg.get('extension_command_catalog')) is int
                                                         and msg.get('extension_command_catalog') == 1)
            menu_generation = msg.get('extension_command_catalog_generation', 0)
            self._extension_command_catalog_generation = (menu_generation if type(menu_generation) is int
                                                          and menu_generation >= 0 else -1)
            self._extension_cancel_supported = (type(msg.get('extension_command_cancel')) is int
                                                and msg.get('extension_command_cancel') == 1)
            self._extension_session_supported = (type(msg.get("extension_session")) is int
                                                  and msg.get("extension_session") == 1)
            self._extension_command_results_supported = (type(msg.get("extension_command_results")) is int
                                                          and msg.get("extension_command_results") == 1)
            self._mcp_data_supported = type(msg.get("mcp_context")) is int and msg["mcp_context"] == 1
            self._tool_inventory_supported = (type(msg.get('tool_inventory')) is int and msg.get('tool_inventory') == 1)
            self._tool_inventory_selection_supported = (type(msg.get('tool_inventory_selection')) is int
                                                        and msg.get('tool_inventory_selection') == 1)
            if isinstance(self._transcript_panel, ToolInventoryPanel) and not self._tool_inventory_supported:
                self._cancel_tools()
                self._transcript_panel.invalidate_inventory('Tool-inventory capability withdrawn; no cached fallback')
            self._mcp_content_types_supported = (type(msg.get("mcp_content_types")) is int
                                                  and msg["mcp_content_types"] == 1)
            self._mcp_supported = type(msg.get("mcp_controls")) is int and msg["mcp_controls"] == 1
            self._mcp_browser_owner_supported = (type(msg.get('mcp_browser_owner')) is int
                                                 and msg['mcp_browser_owner'] == 1)
            self._source_supported = type(msg.get("transcript_source")) is int and msg["transcript_source"] == 1
            self._attachment_transport = msg.get("attachment_transport") == 1
            self._state["image_input"] = msg.get("image_input")
            self._state["image_limits"] = msg.get("image_limits", {})
            self._session_controls_supported = msg.get("session_controls") == 1
            self._session_rename_supported = type(msg.get("session_rename")) is int and msg.get("session_rename") == 1
            self._name_read_supported = type(msg.get('session_name_read')) is int and msg.get('session_name_read') == 1
            if not self._name_read_supported:
                self._cancel_name_read(send=False, notice='Session name read support withdrawn; no cached fallback')
            if (not self._session_rename_supported and self._session_mutation_id is not None
                    and self._navigation_requests.get(self._session_mutation_id, (None, 0, ''))[2] == 'rename'):
                self._navigation_requests.pop(self._session_mutation_id, None)
                self._chat_log.add_system('Session naming support withdrawn; inspect the saved name before retrying')
                self._finish_session_sync()
            self._state["session_name"] = msg.get("session_name", "")
            self._input_controls_supported = msg.get("input_controls") == 1
            namespace = msg.get('inbox_namespace')
            self._native_supported = msg.get('input_receipts') == 1 and valid_namespace(namespace)
            self._native_namespace = str(namespace) if self._native_supported else ''
            # The local socket peer owns the group's workspace. Never suggest
            # paths relative to the directory from which the TUI was launched.
            workspace = msg.get("workspace_dir")
            if isinstance(workspace, str) and workspace and not self._exit_requested:
                if self._file_completions is None or str(self._file_completions.root) != workspace:
                    if self._file_completions is not None:
                        self._file_completions.close()
                    self._file_completions = WorkspaceFileCompletionProvider(workspace)
                    self._editor.set_autocomplete_provider(self._file_completions)
            elif self._file_completions is not None:
                self._file_completions.close()
                self._file_completions = None
                self._editor.set_autocomplete_provider(None)
            # Use session ID from gateway if available (resume existing session),
            # otherwise create a new one
            gateway_session_id = msg.get("session_id")
            gateway_turn_count = msg.get("turn_count", 0)

            if gateway_session_id:
                self._state["session_id"] = gateway_session_id
            elif not self._state.get("session_id"):
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

            self._history_loading = int(msg.get("history_count", 0)) > 0
            self._connection_ready = not self._history_loading
            connection_status = "syncing" if self._history_loading else "connected"
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
                self._replaying_history = self._history_loading
                if history_count:
                    self._chat_log.add_system(
                        f"resumed session: {sid} ({gateway_turn_count} turns)"
                    )
                    self._history_format = int(msg.get("history_format", 1))
                    if history:
                        self._replay_history_entries(history)
                else:
                    self._chat_log.add_system(f"new session: {sid}")
            elif refresh_current:
                self._history_format = int(msg.get("history_format", 1))
                self._replaying_history = self._history_loading
                self._chat_log.add_system(f"Current group context: {sid}")
                history = msg.get("history", [])
                if history:
                    self._replay_history_entries(history)

            self._update_header()
            self._update_footer()
            self._set_connection_status(connection_status)
            self._request_native_receipt()
            if self._active_run_id is None:
                self._set_activity_status("idle")
            return

        if msg_type == "history_chunk":
            entries = msg.get("entries")
            if self._replaying_history and isinstance(entries, list):
                self._replay_history_entries(entries)
            return

        if msg_type == "history_end":
            if not self._history_loading:
                return
            self._replaying_history = False
            self._history_tools.clear()
            self._history_loading = False
            self._connection_ready = True
            self._set_connection_status("connected")
            self._request_native_receipt()
            if self._session_mutation_id is not None:
                self._finish_session_sync()
            return

        # Reconnect status update (internal, not from gateway)
        if msg_type == "_reconnect_status":
            self._connection_ready = False
            attempt = msg.get("attempt", 1)
            delay = msg.get("delay", 1)
            self._set_connection_status("reconnecting")
            self._set_activity_status(
                f"reconnecting (attempt {attempt}, {delay:.0f}s)"
            )
            return

        # Successfully reconnected after disconnection
        if msg_type == "internal_reconnected":
            self._connection_ready = False
            self._set_connection_status("syncing")
            return

        if msg_type == "quit_ack":
            self._tui.stop()
            return

        # Todo checklist update (ephemeral, within-turn progress)
        if msg_type == "todo":
            todos = msg.get("todos", [])
            self._render_todos(todos)
            return

        if msg_type in {"extension_notice", "extension_ui"}:
            if (msg.get("group_id") != self.group_id or msg.get("run_id") != self._active_run_id
                    or self._active_run_id is None):
                return
            if msg_type == "extension_notice":
                self._chat_log.add_system(str(msg.get("text", "")))
            elif isinstance(msg.get("snapshot"), dict):
                try:
                    self._extension_snapshot = validate_snapshot(msg["snapshot"])
                except (ValueError, TypeError):
                    self._chat_log.add_system("Invalid extension UI snapshot ignored")
            self._tui.request_render()
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
                if msg_type in {"response", "final", "aborted", "error"} or (
                    msg_type == "abort_ack" and msg.get("status") == "not_active"
                ):
                    self._native_finished(run_id)
                    self._restore_uncertain_inputs(run_id)
                    self._chat_log.drop_assistant(run_id)
                    self._active_run_id = None
                    self._active_text = None
                    self._active_images = ()
                    self._aborting_run_id = None
                    self._set_waiting(False)
                    if msg_type == "aborted":
                        self._chat_log.add_system("run aborted")
                    self._dispatch_next_pending()
                return

        if msg_type in ("busy", "queued"):
            run_id = str(msg.get("run_id", ""))
            if run_id == self._active_run_id:
                if self._native_submission is not None and self._native_submission.run_id == run_id:
                    self._native_submission = replace(self._native_submission, phase='unknown')
                else:
                    self._restore_pending_draft(run_id)
                self._active_run_id = None
                self._active_text = None
                self._aborting_run_id = None
                self._set_waiting(False)
                self._active_images = ()
                self._chat_log.add_system('Group busy; original input retained separately; /status refreshes receipt'
                    if self._native_submission is not None else 'group is busy; message restored to editor')
            return

        if msg_type == "run_ack":
            staged = self._native_submission
            if (staged is not None and msg.get('run_id') == staged.run_id
                    and msg.get('message_id') == 'native:' + staged.run_id
                    and msg.get('inbox_namespace') == staged.namespace):
                self._native_submission = replace(staged, acknowledged=True,
                    phase='unknown' if staged.phase == 'unknown' else 'accepted')
            if msg.get("run_id") == self._active_run_id and msg.get("session_id"):
                actual = str(msg["session_id"])
                if self._source_request_id is not None and actual != self._source_session_id:
                    self._cancel_source_read()
                    if self._transcript_panel is not None:
                        self._transcript_panel.set_source_status("Source session changed; refresh explicitly")
                self._gateway_session_id = actual
                self._state["session_id"] = self._gateway_session_id
                self._update_footer()
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
            self._native_finished(str(run_id))
            self._restore_uncertain_inputs(str(run_id))
            content = msg.get("content", "")
            self._set_waiting(False)
            self._active_run_id = None
            self._active_text = None
            self._active_images = ()
            self._aborting_run_id = None
            self._streaming_output_chars = 0
            self._finalize_todos()

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
            self._native_finished(str(run_id))
            self._restore_uncertain_inputs(str(run_id))
            self._set_waiting(False)
            self._active_run_id = None
            self._active_text = None
            self._aborting_run_id = None
            self._clear_todos()
            self._chat_log.add_system("run aborted")
            self._chat_log.drop_assistant(run_id)
            self._active_images = ()
            self._dispatch_next_pending()
            return

        # Error (matching OpenClaw's chat event: state=error)
        if msg_type in ("error", "internal_error"):
            if msg_type == "internal_error":
                if self._mcp_requests:
                    self._chat_log.add_system("MCP control outcome uncertain; inspect /mcp list before retrying")
                    self._mcp_requests.clear()
                self._pending_output = None
                self._output_epoch += 1
            if msg_type == "internal_error":
                self._native_query = None
                self._connection_ready = False
                self._pending_command = None
                if self._session_mutation_id is not None:
                    self._chat_log.add_system(
                        "Session operation outcome uncertain; inspect gateway state before retrying"
                    )
                self._argument_feed.reset()
                self._argument_requests.clear()
                self._argument_scopes.clear()
                self._argument_context_requests.clear()
                self._cancel_name_read(send=False, notice='Session name read disconnected; not replayed')
                self._session_mutation_id = None
                self._navigation_requests.clear()
                if self._navigation_panel is not None:
                    self._close_navigation()
                self._history_loading = False
                self._replaying_history = False
                self._history_tools.clear()
                if isinstance(self._transcript_panel, ToolInventoryPanel):
                    self._tools_request = ''
                    self._tools_deadline = 0.0
                    self._tool_inventory_supported = False
                    self._transcript_panel.invalidate_inventory('Disconnected inventory; refresh after reconnect')
                elif self._transcript_panel is not None:
                    self._cancel_source_read()
                    self._transcript_panel.set_source_status("Disconnected snapshot; refresh after reconnect")
                self._lose_compaction_receipt()
                self._compaction_supported = False
                self._skills_supported = False
                self._skills_request = ""
                self._set_connection_status("reconnecting")
            run_id = str(msg.get("run_id", "default"))
            if msg_type == "internal_error" and self._active_run_id is not None:
                run_id = self._active_run_id
                if self._native_submission is not None and self._native_submission.run_id == run_id:
                    self._native_submission = replace(self._native_submission, phase='unknown')
                    self._active_images = ()
                    self._chat_log.add_system('Original input retained for receipt reconciliation; no new-ID replay')
                else:
                    self._restore_pending_draft(run_id)
                    self._chat_log.add_system(
                        "drafts restored; prior tool effects may have completed — review before resending")
                self._active_text = None
                for tool in self._chat_log._tools.values():
                    tool.set_canceled()
                self._update_queue_summary()
            if msg_type == "error" and self._active_run_id == run_id:
                staged = self._native_submission
                if staged is not None and staged.run_id == run_id:
                    self._restore_uncertain_inputs(run_id)
                    if staged.acknowledged:
                        self._native_finished(run_id)
                    else:
                        self._native_submission = replace(staged, phase='unknown')
                    self._active_images = ()
                else:
                    self._restore_pending_draft(run_id)
                self._active_text = None
            if msg_type == "internal_error":
                orphaned = [item.content for item in self._routed_inputs.values()]
                orphaned.extend(content for action, _key, content, _owner in self._input_requests.values()
                                if action == "edit")
                self._composer.restore(tuple(item for group in self._routed_images.values() for item in group))
                self._routed_images.clear()
                self._routed_inputs.clear()
                self._input_requests.clear()
                if orphaned:
                    self._preserve_submission(
                        "\n\n".join(orphaned), "Uncertain inputs restored; inspect before resending",
                    )
            self._set_waiting(False)
            self._active_run_id = None
            self._aborting_run_id = None
            self._clear_todos()
            err = msg.get("message", msg.get("content", "Unknown error"))
            self._chat_log.add_system(
                f"Canceled: {err}" if msg_type == "error" and msg.get("canceled") is True
                else theme_error(f"Error: {err}")
            )
            if msg_type == "error" and msg.get("retain_partial") is True:
                self._chat_log.retain_assistant(run_id)
            else:
                self._chat_log.drop_assistant(run_id)
            self._dispatch_next_pending()
            return

        if msg_type == "reset_ack":
            if self._pending_command != "reset":
                return
            self._pending_command = None
            self._set_waiting(False)
            self._clear_todos()
            # Use session ID from gateway if provided
            gateway_session_id = msg.get("session_id")
            if gateway_session_id:
                self._state["session_id"] = gateway_session_id
            else:
                self._state["session_id"] = str(uuid.uuid4())
            self._close_transcript()
            self._gateway_session_id = str(self._state["session_id"])
            self._extension_snapshot = {**self._extension_snapshot}
            self._session_context_stale = False
            self._chat_log.clear_all()
            self._usage = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
            self._last_turn_usage = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
            sid = self._state["session_id"][:12]
            self._chat_log.add_system(f"new session: {sid}")
            self._update_header()
            self._update_footer()
            return

        if msg_type == "status":
            if self._pending_command == "status":
                self._pending_command = None
            if self._active_run_id is None and self._pending_command is None:
                self._set_waiting(False)
            pid = msg.get("pid", "?")
            groups = ", ".join(msg.get("groups", []))
            self._chat_log.add_system(
                f"Gateway running (PID: {pid}) — groups: {groups}"
            )
            self._request_native_receipt()
            self._dispatch_next_pending()
            return

    # ── Socket I/O thread with auto-reconnect ──

    _RECONNECT_BASE_DELAY = 1.0
    _RECONNECT_MAX_DELAY = 30.0
    _TRANSPORT_TIMEOUT = 10.0

    def _io_loop(self) -> None:
        async def owned_connection_loop() -> None:
            with self._io_lock:
                self._io_loop_ref = asyncio.get_running_loop()
                self._io_task = asyncio.current_task()
            try:
                if not self._io_stop.is_set():
                    await self._io_loop_async()
            finally:
                with self._io_lock:
                    self._io_loop_ref = None
                    self._io_task = None

        try:
            asyncio.run(owned_connection_loop())
        except asyncio.CancelledError:
            if not self._io_stop.is_set():
                self._recv_queue.put({
                    "type": "internal_error", "message": "Gateway connection task was cancelled",
                })
        except Exception as exc:
            if not self._io_stop.is_set():
                self._recv_queue.put({"type": "internal_error", "message": f"IO thread error: {exc}"})

    async def _io_loop_async(self) -> None:
        """Own socket resources across reconnects; never mutate UI state here."""
        attempt = 0
        while not self._io_stop.is_set():
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_unix_connection(self.socket_path, limit=4 * 1024 * 1024),
                    timeout=self._TRANSPORT_TIMEOUT,
                )
            except (OSError, asyncio.TimeoutError) as exc:
                if attempt == 0:
                    self._recv_queue.put({"type": "internal_error", "message": f"Connection error: {exc}"})
                attempt += 1
                self._set_reconnect_status(attempt)
                await asyncio.sleep(self._reconnect_delay(attempt))
                continue

            self._io_generation += 1
            self._recv_queue.put({"type": "_connection_opened", "generation": self._io_generation})
            try:
                disconnected = await self._run_connection(reader, writer)
            finally:
                writer.close()
                try:
                    await asyncio.wait_for(writer.wait_closed(), timeout=1.0)
                except (OSError, asyncio.TimeoutError):
                    pass
            if not disconnected or self._io_stop.is_set():
                break
            # An accepted socket is not a successful session handshake. Failed
            # handshakes keep backing off; healthy sessions restart the counter.
            attempt = 1 if self._io_session_ready else attempt + 1
            self._set_reconnect_status(attempt)
            await asyncio.sleep(self._reconnect_delay(attempt))

    def _reconnect_delay(self, attempt: int) -> float:
        delay = self._RECONNECT_BASE_DELAY
        for _ in range(max(0, attempt - 1)):
            if delay >= self._RECONNECT_MAX_DELAY:
                break
            delay *= 2
        return float(min(delay, self._RECONNECT_MAX_DELAY))

    def _set_reconnect_status(self, attempt: int) -> None:
        self._recv_queue.put({
            "type": "_reconnect_status", "attempt": attempt,
            "delay": self._reconnect_delay(attempt),
            "_connection_generation": self._io_generation,
        })

    async def _run_connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> bool:
        """Handshake first, then exchange only current-generation requests.

        Never replay a request whose send/ack outcome is uncertain. The UI
        restores its text for explicit resubmission. Cancel/await both child
        tasks on every exit, including cancellation of this owning coroutine.
        """
        generation = self._io_generation
        self._io_session_ready = False
        ready = asyncio.Event()
        disconnected = False
        source_receiver = SourceReceiver(lambda event: self._recv_queue.put({
            **event, "_connection_generation": generation,
        }))

        mcp_receiver = McpDataReceiver(lambda event: self._recv_queue.put({
            **event, "_connection_generation": generation,
        }))
        child_receiver = SourceReceiver(lambda event: self._recv_queue.put({
            **event, "type": event["type"].replace("source_", "child_source_", 1),
            "_connection_generation": generation,
        }))


        def lost(message: str) -> None:
            nonlocal disconnected
            disconnected = True
            self._recv_queue.put({
                "type": "internal_error", "message": message,
                "_connection_generation": generation,
            })

        async def read_events() -> None:
            received_pong = False
            while not self._io_stop.is_set():
                try:
                    line = await reader.readline()
                    if not line:
                        lost("Gateway closed connection. Reconnecting...")
                        return
                    try:
                        message = json.loads(line.decode("utf-8"))
                        if not isinstance(message, dict) or not isinstance(message.get("type"), str):
                            raise ValueError("Expected a gateway event object")
                        if message["type"] == "pong" and not isinstance(message.get("history_count", 0), int):
                            raise ValueError("Invalid history count")
                    except (ValueError, UnicodeDecodeError):
                        self._recv_queue.put({
                            "type": "internal_protocol_error", "message": "Invalid gateway event ignored",
                            "_connection_generation": generation,
                        })
                        continue
                    if message["type"] in {"mcp_data_begin", "mcp_data_chunk", "mcp_data_end", "mcp_data_error"}:
                        await asyncio.to_thread(mcp_receiver.feed, message)
                        continue
                    if message["type"] in {"child_source_begin", "child_source_chunk", "child_source_end",
                                            "child_source_error"}:
                        packet = {**message, "type": message["type"].replace("child_source_", "source_", 1)}
                        if packet["type"] == "source_begin" and (
                                type(packet.get("bytes")) is not int or packet["bytes"] > 2 * 1024 * 1024):
                            packet.update(type="source_error", error="Child source exceeds retention budget")
                        await asyncio.to_thread(child_receiver.feed, packet)
                        continue
                    if message["type"] in {"source_begin", "source_chunk", "source_end", "source_error"}:
                        await asyncio.to_thread(source_receiver.feed, message)
                        continue
                    message["_connection_generation"] = generation
                    self._recv_queue.put(message)
                    if message["type"] == "pong":
                        received_pong = True
                    if received_pong and (
                        (message["type"] == "pong" and not message.get("history_count"))
                        or message["type"] == "history_end"
                    ):
                        self._io_session_ready = True
                        ready.set()
                except (OSError, ValueError) as exc:
                    lost(f"Read error: {exc}. Reconnecting...")
                    return

        async def write_requests() -> None:
            try:
                hello = {"type": "ping", "group_id": self.group_id,
                         "extension_command_results": 1, "reasoning_controls": 1,
                         "extension_session": 1, "extension_session_inspection": 1,
                         "extension_provider_auth": 1,
                         "session_name_read": 1,
                         "extension_command_catalog": 1,
                         "extension_argument_completion": 1, "extension_argument_cancel": 1,
                         "extension_argument_context": 1,
                         "mcp_content_types": 1, "access_controls": 1,

                         "tool_inventory": 1,
                         "skill_controls": 1, "session_compaction": 1, "saved_source": 1, "saved_timeline": 1,
                         "acp_updates": 1, "input_receipts": 1}
                writer.write((json.dumps(hello) + "\n").encode("utf-8"))
                await asyncio.wait_for(writer.drain(), timeout=self._TRANSPORT_TIMEOUT)
                await asyncio.wait_for(ready.wait(), timeout=self._TRANSPORT_TIMEOUT)
                while not self._io_stop.is_set():
                    try:
                        queued = self._send_queue.get_nowait()
                    except queue.Empty:
                        await asyncio.sleep(0.05)
                        continue
                    request = dict(queued)
                    if request.pop("_connection_generation", None) != generation:
                        continue
                    if request.get("type") == "quit":
                        self._recv_queue.put({"type": "quit_ack", "_connection_generation": generation})
                        return
                    if request["type"] == "mcp_context_cancel":
                        await asyncio.to_thread(mcp_receiver.close)
                    if request["type"] == "mcp_context":
                        await asyncio.to_thread(mcp_receiver.expect, request["request_id"], request["group_id"],
                                                request["session_id"])
                    if request["type"] == "child_source_read":
                        await asyncio.to_thread(child_receiver.expect, request["request_id"], request["group_id"],
                                                request["session_id"] + ":" + request["child_id"])
                    elif request["type"] == "child_source_cancel":
                        await asyncio.to_thread(child_receiver.close)
                    if request["type"] in {"source_read", "saved_source_read"}:
                        await asyncio.to_thread(source_receiver.expect, request["request_id"],
                                                request["group_id"], request["session_id"],
                                                target_session_id=request.get('target_session_id', ''),
                                                branch_id=request.get('branch_id', ''))
                    elif request["type"] == "source_cancel":
                        await asyncio.to_thread(source_receiver.close)
                    images = request.pop("_images", ())
                    references = []
                    for image in images:
                        key = image.id + "-" + uuid.uuid4().hex
                        for index, offset in enumerate(range(0, len(image.data), 64 * 1024)):
                            final = offset + 64 * 1024 >= len(image.data)
                            chunk = {"type": "attachment_chunk", "id": key, "index": index,
                                     "mime_type": image.mime_type,
                                     "data": base64.b64encode(image.data[offset:offset + 64 * 1024]).decode("ascii"),
                                     "final": final,
                                     **({"sha256": hashlib.sha256(image.data).hexdigest()} if final else {})}
                            writer.write((json.dumps(chunk) + "\n").encode())
                            await asyncio.wait_for(writer.drain(), timeout=self._TRANSPORT_TIMEOUT)
                        references.append(key)
                    if references:
                        request["image_refs"] = references
                    request.setdefault("group_id", self.group_id)
                    writer.write((json.dumps(request) + "\n").encode("utf-8"))
                    await asyncio.wait_for(writer.drain(), timeout=self._TRANSPORT_TIMEOUT)
            except (OSError, ValueError, asyncio.TimeoutError) as exc:
                lost(f"Write/handshake error: {exc}. Reconnecting...")

        tasks = [
            asyncio.create_task(read_events(), name="claw-gateway-read"),
            asyncio.create_task(write_requests(), name="claw-gateway-write"),
        ]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            # Propagate unexpected child failures instead of silently abandoning
            # the transport. The worker catches them and notifies the UI.
            for task in tasks:
                if task.done():
                    task.result()
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await asyncio.to_thread(child_receiver.close)
            await asyncio.to_thread(source_receiver.close)
            await asyncio.to_thread(mcp_receiver.close)
        return disconnected


def run_tui_app(
    socket_path: str, group_id: str = "main", *, model: str = ""
) -> None:
    app = ChatApp(socket_path, group_id)
    if model:
        app._state["model"] = model
    app.run()
