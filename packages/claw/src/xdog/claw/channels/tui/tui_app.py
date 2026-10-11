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
from xdog.agent.extensions.custom import rich as custom_rich
from xdog.agent.extensions.dialogs import validate_dialog
from xdog.agent.extensions.forms import multi_choice_form
from xdog.agent.extensions.runtime import validate_snapshot
from xdog.agent.mcp.completion import McpCompletion
from xdog.agent.mcp.context import McpDocument, McpEntry, resource_completion_document
from xdog.agent.mcp.oauth import validate_launch, validate_review
from xdog.agent.planning import PlanState
from xdog.agent.supervision_config import ChildConfig, ChildPresets, parse_child_options
from xdog.ai.types import ImageContent
from xdog.claw.channels.tui.mcp_data import McpDataReceiver
from xdog.claw.channels.tui.source_reader import SourceReceiver
from xdog.claw.config import get_config_dir, get_data_dir
from xdog.tui.attachments import Attachment, AttachmentComposer
from xdog.tui.child_browser import child_entries
from xdog.tui.child_draft_autosave import ChildDraftAutosave
from xdog.tui.child_draft_io import ChildDraftIO
from xdog.tui.child_draft_store import ChildDraftStore, SavedChildDraft
from xdog.tui.child_drafts import ChildDraft
from xdog.tui.child_recovery import ChildRecoveryReceiver
from xdog.tui.components.attachment_panel import AttachmentPanel
from xdog.tui.components.child_draft_panel import ChildDraftPanel, DraftEntry, draft_entries
from xdog.tui.components.child_panel import ChildPanel
from xdog.tui.components.details import set_details_expanded
from xdog.tui.components.details_panel import DetailRecord, DetailsPanel, streaming_preview
from xdog.tui.components.extension_command import ExtensionCommand
from xdog.tui.components.extension_custom import ExtensionCustom, ExtensionCustomPresentation
from xdog.tui.components.extension_dialog import ExtensionDialog
from xdog.tui.components.extension_navigation import ExtensionNavigation
from xdog.tui.components.extension_stack import ExtensionDialogStack
from xdog.tui.components.extension_view import ExtensionView
from xdog.tui.components.image import Image
from xdog.tui.components.inline_layout import CompactText, InlineLayout
from xdog.tui.components.input_panel import InputPanel, SlashSelectList, _sanitize_editor_text
from xdog.tui.components.markdown import DefaultTextStyle, MarkdownTheme
from xdog.tui.components.mcp_panel import McpPanel
from xdog.tui.components.mcp_sampling_panel import McpSamplingPanel, _json
from xdog.tui.components.messages import AssistantMessages, ThinkingMessage, Transcript
from xdog.tui.components.messages import UserMessage as SharedUserMessage
from xdog.tui.components.navigation_panel import NavigationPanel
from xdog.tui.components.output_panel import OutputPanel
from xdog.tui.components.plan_panel import PlanPanel
from xdog.tui.components.remote_parent_drafts import RemoteParentDraftPanel
from xdog.tui.components.review_panel import ReviewPanel
from xdog.tui.components.spacer import Spacer
from xdog.tui.components.status_line import StatusLine
from xdog.tui.components.text import Text
from xdog.tui.components.tool_inventory import ToolInventoryPanel, inventory_records
from xdog.tui.components.tool_message import ToolMessage as ToolMessageView
from xdog.tui.components.transcript_panel import TranscriptPanel
from xdog.tui.editor_actions import EditorActions
from xdog.tui.event_queue import EventQueue, thaw_event
from xdog.tui.extension_shortcuts import ExtensionShortcuts
from xdog.tui.external_editor import edit_draft
from xdog.tui.file_completion import WorkspaceFileCompletionProvider
from xdog.tui.keys import KeyEvent, KeyEventType
from xdog.tui.navigation import NavigationEntry, NavigationFeed, NavigationSnapshot
from xdog.tui.parent_draft_receiver import ParentDraftReceiver, ParentRecovery, composer_receipt
from xdog.tui.parent_drafts import ParentDrafts
from xdog.tui.prompt_keymap import PromptKeymap
from xdog.tui.prompt_terminal import PromptTerminalInterceptor, prompt_key_policy
from xdog.tui.queue_controls import QUEUE_HELP, LocalQueueIds, QueueEntry, parse_queue_command
from xdog.tui.repository_review import RepositoryReview
from xdog.tui.terminal_interception import HeldTerminalInput
from xdog.tui.terminal_logging import capture_terminal_logs
from xdog.tui.transcript_actions import TranscriptActions
from xdog.tui.transcript_browser import TranscriptRecord
from xdog.tui.transcript_transfer import TransferOptions, parse_transfer
from xdog.tui.tui import TUI, Component, Container
from xdog.tui.url_open import ReviewedUrlOpener
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
    "/help": "Search terminal commands: /help [QUERY]",
    "/compact": "Summarize older context: /compact [focus instructions] | /compact cancel",
    "/skills": "List/load/unload actual group skills without a model turn",
    "/tools": "Read-only configured/active tool/schema browser",
    "/mcp": "Manage explicit external MCP connections",
    "/model": "Inspect/select configured native and session-owned extension models",
    "/thinking": "Inspect/change session reasoning level; no model turn",
    "/extensions": "Explicit trusted extension status/reload/unload/auth",
    "/login": "Extension provider authentication menu/login; native provider auth uses xdog-ai",
    "/draft": "Parent autosave/saved; remote recovery; remote-save (shortcut preserves composer)",
    "/agents": "Child browser/controls; start supports --model/--thinking and reviewed specialization settings",
    "/plan": "Inspect plan or /plan start OBJECTIVE | execute REVISION | revise | stop",
    "/copy": "Copy response/selected source", "/export": "Save private transcript snapshot",
    "/share": "Review local shareable HTML (no upload)",
    "/transcript": "Search/select live or saved source: branch ID | saved SESSION [BRANCH]",
    "/fullscreen": "Toggle fullscreen transcript viewer",
    "/quit": "Exit the TUI",
    "/exit": "Exit the TUI",
    "/reset": "Reset chat session",
    "/status": "Show gateway status",
    "/clear": "Clear screen and chat",
    "/keys": "Show active keybindings",
    "/terminal-input": "Inspect/restore/discard rejected input, or pause/resume hooks",
    "/editor": "Open external prompt editor",
    "/queue": "Inspect/edit/remove/reorder queued inputs",
    "/diff": "Read-only repository-wide change review",
    "/sessions": "Browse saved sessions", "/tree": "Browse branches/checkpoints", "/resume": "Activate saved session",
    "/name": "Inspect/name session: /name [NAME|- to clear]",
    "/new": "Create new session", "/fork": "Save checkpoint",
    "/branch": "Restore branch", "/goto": "Fork at checkpoint",
    "/artifact": "Load a checksummed command artifact read-only",
    "/attach": "Attach an image file", "/attachments": "Inspect/remove/preview images", "/paste": "Read clipboard",
    "/steer": "Send input at the next checkpoint",
    "/follow-up": "Send after active tool continuation",
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
        self._pending_messages: deque[str] = deque()
        self._pending_ids = LocalQueueIds()
        self._transformed_inputs: dict[str, str] = {}
        self._routed_inputs: dict[str, QueueEntry] = {}
        self._extension_queued_inputs: dict[str, tuple[str, int]] = {}
        self._extension_queued_inputs_supported = False
        self._extension_context_messages_supported = False
        self._extension_context_messages_generation = 0
        self._context_results: dict[str, tuple[str, str, int]] = {}
        self._extension_queued_inputs_generation = 0
        self._child_queued_inputs_supported = False
        self._child_queued_inputs_generation = 0
        self._child_queued_inputs_owner = ''
        self._child_extension_inputs: dict[str, tuple[str, str, str]] = {}
        self._input_requests: dict[str, tuple[str, str, str, str]] = {}
        self._input_sequence = 0
        self._input_controls_supported = False
        self._last_ctrl_c_at: float = 0.0
        self._exit_requested = False
        self._draft_quit_failures: tuple[int, ...] | None = None
        self._extension_browser: tuple[str, str, str] | None = None
        self._extension_navigation: tuple[str, str, dict[str, Any]] | None = None
        self._extension_navigation_review: tuple[str, str, dict[str, Any]] | None = None
        self._editor_actions = EditorActions(self._command_editor_snapshot, self._command_editor_replace)
        from xdog.tui.editor_write import EditorWriteReviews
        self._editor_write_reviews = EditorWriteReviews()
        self._extension_editor_write_supported = False
        self._extension_editor_write_generation = 0
        self._extension_editor_write_owner = ''
        self._has_connected = False
        self._connection_ready = False
        self._connection_generation = 0
        self._history_loading = False
        self._pending_command: str | None = None
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
        self._review_panel: ReviewPanel | None = None
        self._review_backend: RepositoryReview | None = None
        self._navigation_panel: NavigationPanel | None = None
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
        self._extension_dialog_panel: ExtensionDialog | ExtensionNavigation | ExtensionCustom | None = None
        self._extension_supported = False
        self._extension_models_supported = False
        self._native_models_supported = False
        self._reasoning_supported = False
        self._reasoning_pending: str | None = None
        self._extension_model_pending: str | None = None
        self._terminal_supported = False
        self._terminal_release_supported = False
        self._terminal_child_supported = False
        self._terminal_children: dict[str, dict[str, Any]] = {}
        self._child_terminal_owner = ""
        self._terminal_owner = ""
        self._terminal_request: dict[str, Any] | None = None
        self._terminal_held: HeldTerminalInput | None = None
        self._extension_shortcuts_supported = False
        self._extension_navigation_supported = False
        self._extension_session_supported = False
        self._extension_session_inspection_supported = False
        self._extension_active_tools_supported = False
        self._extension_tools_after_run_supported = False
        self._extension_tools_after_run_generation = 0
        self._extension_compaction_supported = False
        self._extension_compaction_generation = 0
        self._extension_name_after_run_supported = False
        self._extension_name_after_run_generation = 0
        self._extension_compaction_after_run_supported = False
        self._extension_compaction_after_run_generation = 0
        self._extension_provider_auth_supported = False
        self._extension_provider_auth_generation = 0
        self._extension_auth_owner: tuple[str, str, int] | None = None
        self._extension_message_supported = False
        self._extension_message_generation = 0
        self._extension_message_images_supported = False
        self._extension_message_images_generation = 0
        self._extension_name_supported = False
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
        self._extension_editor_supported = False
        self._extension_custom_nested_supported = False
        self._extension_custom_rich_supported = False
        self._extension_custom_overlay_supported = False
        self._extension_custom_live_overlay_supported = False
        self._extension_custom_supported = False
        self._extension_forms_supported = False
        self._extension_multi_forms_supported = False
        self._extension_command_results_supported = False
        self._extension_pending: tuple[str, str] | None = None
        self._extension_editor_owner: tuple[str, str] | None = None
        self._child_panel: ChildPanel | None = None
        self._child_context = ""
        self._child_target = ""
        self._child_ui_requests: set[str] = set()
        self._child_presets = ChildPresets()
        self._child_presets_supported = False
        self._child_model_supported = False
        self._child_config_supported = False
        self._child_images_supported = False
        self._child_image_recovery_supported = False
        self._child_task_recovery_supported = False
        self._child_return_supported = False
        self._child_return_owner = ''
        self._child_queue_owners: dict[str, str] = {}
        self._child_recovery_ready: dict[str, dict[str, Any]] = {}
        self._child_drafts: dict[str, dict[tuple[str, str], ChildDraft]] = {}
        self._child_draft_labels: dict[str, str] = {}
        self._child_draft_io = ChildDraftIO()
        self._child_autosave = ChildDraftAutosave()
        self._parent_drafts = ParentDrafts(lambda: get_data_dir() / 'saved-parent-drafts', self._parent_draft_snapshot,
                                           self._mcp_context_key, lambda text: self._chat_log.add_system(text),
            self._parent_draft_capacity, create_parent=True)
        self._remote_parent_panel: RemoteParentDraftPanel | None = None
        self._remote_parent_receiver = ParentDraftReceiver(
            lambda: max(0, 40 * 1024 * 1024 - self._retained_image_bytes() - self._composer.bytes))
        self._remote_parent_caps: dict[str, Any] = {}
        self._remote_parent_scope: dict[str, Any] = {}
        self._remote_parent_request = ''
        self._remote_parent_read_completed = False
        self._remote_parent_revision = 0
        self._remote_parent_context = ''
        self._remote_parent_deadline = 0.0
        self._remote_parent_operation = ''
        self._remote_parent_review_id = ''
        self._remote_parent_upload: dict[str, Any] | None = None
        self._remote_parent_selected: dict[str, Any] = {}
        self._remote_parent_worker: threading.Thread | None = None
        self._remote_parent_decode_cancel = threading.Event()
        self._saved_child_drafts: dict[str, SavedChildDraft] = {}
        self._child_source_supported = False
        self._child_source_pending: tuple[str, str, str] | None = None
        self._child_source_deadline = 0.0
        self._children_supported = False
        self._child_queues_supported = False
        self._children_pending: dict[str, tuple[str, str]] = {}
        self._plan_panel: PlanPanel | None = None
        self._plan_snapshot = PlanState()
        self._plan_supported = False
        self._plan_pending: tuple[str, str, str] | None = None
        self._plan_context = ""
        self._plan_refresh_needed = False
        self._mcp_refresh_supported = False
        self._mcp_client_auth_supported = False
        self._mcp_private_jwt_supported = False
        self._mcp_registration_supported = False
        self._mcp_credentials_supported = False
        self._mcp_auth_supported = False
        self._mcp_watch_supported = False
        self._mcp_catalog_watch_supported = False
        self._mcp_combined_watch_supported = False
        self._mcp_elicitation_supported = False
        self._mcp_elicitation_servers: set[str] = set()
        self._mcp_elicitation_requests: dict[str, str] = {}
        self._mcp_sampling_supported = False
        self._mcp_sampling_servers: set[str] = set()
        self._mcp_sampling_requests: dict[str, str] = {}
        self._mcp_roots_supported = False
        self._mcp_root_requests: set[str] = set()
        self._mcp_watch_view = ""
        self._mcp_watch_requests: set[str] = set()
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
        self._mcp_completion_supported = False
        self._mcp_resource_completion_supported = False
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
        self._output_epoch = 0
        self._artifact_loader = ArtifactLoader(self._recv_queue.put)
        self._outputs = TranscriptActions(self._recv_queue.put, self._chat_log.add_system,
                                          lambda: f"{self._connection_generation}:{self._gateway_session_id}:"
                                                  f"{self._output_epoch}:child:"
                                                  + (self._child_panel.child_id
                                                     if self._child_panel is not None else ""),
                                          lambda: self._tui.request_render())

        self._todo_container = Container()
        self._goal_container = Container()
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
        self._editor.on_change = lambda _text: self._remote_parent_edited()
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
        )

        self._custom_presentation = ExtensionCustomPresentation(self._tui, self._layout)
        self._tui.add_child(self._layout)
        self._tui.set_focus(self._editor)
        self._tui.add_input_listener(self._handle_global_input)

        self._io_thread = threading.Thread(target=self._io_loop, daemon=True)

        # Wire up editor callbacks (matching OpenClaw's editor event wiring)
        self._editor.on_submit = self._handle_submit
        self._editor.on_ctrl_c = self._handle_ctrl_c
        self._editor.on_ctrl_d = self._request_exit
        self._editor.on_escape = self._handle_escape

        self._terminal_input = PromptTerminalInterceptor(
            self._tui, self._terminal_prompt_eligible, self._terminal_prompt_owner,
            self._terminal_available,
            self._terminal_key_policy, self._send_terminal_input, self._cancel_terminal_input,
            self._chat_log.add_system,
            surface=lambda: "child-prompt" if self._terminal_child() else "prompt",
            release_available=self._terminal_release_available)

    def _terminal_child(self) -> str:
        return self._child_panel.terminal_child if self._child_panel is not None else ""

    def _terminal_available(self) -> bool:
        child = self._terminal_child()
        if self._child_panel is not None:
            return (self._terminal_supported and self._terminal_child_supported
                    and self._terminal_children.get(child, {}).get("active") is True)
        return self._terminal_supported and self._extension_snapshot.get('terminal_input') is True

    def _terminal_release_available(self) -> bool:
        if not self._terminal_release_supported or not self._terminal_available():
            return False
        child = self._terminal_child()
        events = (self._terminal_children.get(child, {}).get('events', []) if self._child_panel is not None
                  else self._extension_snapshot.get('terminal_events', []))
        return isinstance(events, list) and 'release' in events

    def _terminal_prompt_eligible(self) -> bool:
        child = self._terminal_child()
        target = self._child_panel if child else self._editor
        return (not self._exit_requested and self._connection_ready and not self._history_loading
                and not self._session_context_stale and self._tui._focused is target
                and not self._tui.has_overlay() and not self._details_open
                and self._pending_command is None and self._session_mutation_id is None
                and self._extension_pending is None and self._extension_dialog_panel is None
                and not self._extension_dialog_queue and self._outputs.panel is None
                and (self._child_panel is None or bool(child)) and self._plan_panel is None
                and self._mcp_panel is None and self._transcript_panel is None and self._attachment_panel is None
                and self._navigation_panel is None and self._review_panel is None)

    def _terminal_prompt_owner(self) -> str:
        extra = ""
        if self._child_panel is not None:
            child = self._terminal_child()
            generation = self._terminal_children.get(child, {}).get("generation", 0)
            extra = (f":child:{child}:{generation}:{self._child_panel.terminal_lease}:"
                     f"{self._child_terminal_owner}:{id(self._child_panel)}")
        return (f'{self.group_id}:{self._gateway_session_id}:{self._connection_generation}:'
                f'{self._terminal_owner}:{self._output_epoch}:'
                f'{self._extension_snapshot.get("terminal_generation", 0)}:{self._exit_requested}{extra}')

    def _terminal_key_policy(self, event: KeyEvent) -> str:
        if self._terminal_child() and self._child_panel is not None:
            action = ("terminal_input" if self._keymap.resolve(event) == "terminal_input"
                      else self._child_panel.terminal_policy_action(event))
            return prompt_key_policy(event, action)
        return prompt_key_policy(event, self._keymap.resolve(event),
                                 extension=self._effective_extension_shortcuts().command(event) is not None)

    def _restore_terminal_text(self, text: str) -> bool:
        if self._terminal_child() and self._child_panel is not None:
            return self._child_panel.restore_terminal_text(text)
        return self._editor.handle_paste(text)

    def _accept_child_terminal_metadata(self, msg: Mapping[str, Any]) -> None:
        if not self._terminal_child_supported:
            return
        rows, owner = msg.get("terminal_children", {}), msg.get("child_terminal_owner", "")
        try:
            if (not isinstance(rows, dict) or len(rows) > 16 or not isinstance(owner, str)
                    or re.fullmatch(r"[a-f0-9]{32}", owner) is None):
                raise ValueError("Invalid child terminal registry")
            for child, info in rows.items():
                if (not isinstance(child, str) or re.fullmatch(r"[a-f0-9]{12}", child) is None
                        or not isinstance(info, dict) or set(info) - {"active", "generation", "events"}
                        or not {"active", "generation"} <= set(info)
                        or type(info["active"]) is not bool or type(info["generation"]) is not int
                        or not 0 <= info["generation"] <= 2**53):
                    raise ValueError("Invalid child terminal registration")
                from xdog.agent.extensions.terminal_input import event_kinds
                event_kinds(info.get("events", []))
        except ValueError:
            self._terminal_children = {}
            self._child_terminal_owner = ""
            if self._terminal_child():
                self._terminal_input.invalidate()
            self._chat_log.add_system("Invalid child terminal registrations; hooks disabled")
            return
        self._terminal_children = {child: dict(info) for child, info in rows.items()}
        self._child_terminal_owner = owner

    def _send_terminal_input(self, item: HeldTerminalInput) -> bool:
        if not self._terminal_supported or self._terminal_request is not None:
            return False
        request = {'type': 'terminal_input', 'request_id': 'terminal-' + uuid.uuid4().hex,
                   'group_id': self.group_id, 'session_id': self._gateway_session_id,
                   'terminal_owner': self._terminal_owner,
                   'input_owner': hashlib.sha256(item.owner.encode()).hexdigest(),
                   'input_sequence': item.id,
                   'extension_generation': self._extension_snapshot.get('terminal_generation', 0),
                   'kind': item.kind, 'surface': item.surface, 'data': item.data}
        child = self._terminal_child()
        if child:
            if not self._terminal_child_supported:
                return False
            request.update(child_id=child, child_owner=self._child_terminal_owner,
                           extension_generation=self._terminal_children.get(child, {}).get("generation", 0))
        self._terminal_request, self._terminal_held = request, item
        self._queue_request(request)
        return True

    def _cancel_terminal_input(self) -> None:
        request, self._terminal_request = self._terminal_request, None
        self._terminal_held = None
        if request is not None:
            self._queue_request({key: value for key, value in request.items()
                                 if key not in {'data', 'kind'}} | {'type': 'terminal_input_cancel'})

    def _terminal_input_result(self, msg: Mapping[str, Any]) -> None:
        request, item = self._terminal_request, self._terminal_held
        if (request is None or item is None or any(type(msg.get(key)) is not type(request[key])
                or msg.get(key) != request[key] for key in (
                'request_id', 'group_id', 'session_id', 'terminal_owner', 'input_owner',
                'extension_generation', 'input_sequence'))):
            return
        if request.get('surface') == 'child-prompt' and any(
                type(msg.get(key)) is not str or msg.get(key) != request[key]
                for key in ('child_id', 'child_owner', 'surface')):
            return
        self._terminal_request, self._terminal_held = None, None
        result = ({key: msg[key] for key in ('data', 'consume') if key in msg}
                  if msg.get('error') is False else {'error': True})
        if msg.get('error') is False and 'terminal_input_active' in msg:
            active = msg['terminal_input_active']
            from xdog.agent.extensions.terminal_input import event_kinds
            try:
                events = list(event_kinds(msg.get('terminal_events', [])))
            except ValueError:
                events = []
                result = {'error': True}
            if type(active) is not bool:
                result = {'error': True}
            else:
                child = request.get('child_id')
                if request.get('surface') == 'child-prompt' and isinstance(child, str):
                    self._terminal_children[child] = {"active": active, "generation": request['extension_generation'],
                                                      "events": events}
                else:
                    self._extension_snapshot = {**self._extension_snapshot, 'terminal_input': active,
                                                'terminal_events': events}
        self._terminal_input.queue.accept(item.id, result)

    def _handle_global_input(self, event: KeyEvent) -> dict[str, object] | None:
        """Route bounded detail navigation before cancellation."""
        action = self._keymap.resolve(event)
        if action in {'save_draft', 'remote_save_draft'}:
            if (event.event_type is KeyEventType.PRESS and self._editor.focused
                    and self._parent_draft_ui_available()):
                self._parent_draft_command('remote-save' if action == 'remote_save_draft' else 'save')
            return {'consume': True}
        if self._remote_parent_panel is not None and action != 'suspend':
            self._remote_parent_panel.handle_input(event)
            self._tui.request_render()
            return {'consume': True}
        if (action is None or action.startswith("ext-")) and self._extension_shortcut_input(event):
            self._tui.request_render()
            return {"consume": True}

        if isinstance(self._extension_dialog_panel, ExtensionCustom) and self._custom_presentation.blocked():
            return None  # The top overlay owns all input; custom callbacks stay hidden/unfocused.
        background_overlay = (isinstance(self._extension_dialog_panel, ExtensionCustom)
                              and self._extension_dialog_panel.dialog.get('overlay') is not None
                              and not self._extension_dialog_panel.focused)
        if background_overlay and self._outputs.panel is None:
            if event.matches('ctrl+c'):
                if event.event_type is KeyEventType.PRESS and self._extension_dialog_panel is not None:
                    self._answer_extension_dialog(self._extension_dialog_panel.dialog['id'], None)
                return {"consume": True}
            if action == 'suspend' or event.matches('ctrl+z'):
                if event.event_type is KeyEventType.PRESS:
                    self._custom_presentation.sync(None, False)
                    self._tui.suspend()
                return {"consume": True}
            if event.matches('ctrl+d'):
                if event.event_type is KeyEventType.PRESS:
                    self._request_exit()
                return {"consume": True}
        if (self._extension_dialog_panel is not None and self._outputs.panel is None
                and not background_overlay):
            if isinstance(self._extension_dialog_panel, ExtensionCustom):
                if event.ctrl and event.key.lower() in ("d", "z") or action == "suspend":
                    if event.event_type is not KeyEventType.PRESS:
                        return {"consume": True}
                if event.ctrl and event.key.lower() == "d":
                    self._request_exit()
                    return {"consume": True}
                if action == "suspend" or event.ctrl and event.key.lower() == "z":
                    self._custom_presentation.sync(None, False)
                    self._tui.suspend()
                    return {"consume": True}
            self._extension_dialog_panel.handle_input(event)
            self._tui.request_render()
            return {"consume": True}
        if self._terminal_child() and action == "terminal_input":
            if event.event_type == KeyEventType.PRESS:
                self._terminal_input.toggle_pause()
                self._terminal_input.recovery_command('list', self._restore_terminal_text)
            return {"consume": True}
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
        if self._plan_panel is not None and self._outputs.panel is None:
            self._plan_panel.handle_input(event)
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
            if action in {"details", "review", "sessions", "tree"}:
                self._close_transcript()
            elif action != "suspend":
                self._transcript_panel.handle_input(event)
                self._tui.request_render()
                return {"consume": True}
        if action == "terminal_input":
            if event.event_type == KeyEventType.PRESS:
                if self._terminal_supported:
                    self._terminal_input.toggle_pause()
                    self._terminal_input.recovery_command('list', self._editor.handle_paste)
                else:
                    self._chat_log.add_system("Updated terminal-input gateway required; input remains host-owned")
                    self._tui.request_render()
            return {"consume": True}
        if action == "paste_clipboard":
            if self._editor.focused and event.event_type == KeyEventType.PRESS:
                root = self._file_completions.root if self._file_completions is not None else Path.cwd()
                self._composer.command("paste", "", root)
            return {"consume": True}
        if self._attachment_panel is not None and action not in {"suspend", "details", "review", "sessions", "tree"}:
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
        if action in {"suspend", "details", "external_editor", "bindings", "review", "sessions", "tree"} and (
            event.event_type != KeyEventType.PRESS
        ):
            return {"consume": True}
        if action in {"sessions", "tree"}:
            self._open_navigation("sessions" if action == "sessions" else f"tree|{self._state['session_id']}|")
            return {"consume": True}
        if action == "review":
            self._close_review() if self._review_panel is not None else self._open_review()
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
        if self._review_panel is not None and action != "details":
            if action == "interrupt":
                self._close_review()
            else:
                self._review_panel.handle_input(event)
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
        if self._review_panel is not None:
            self._close_review()
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
                or self._review_panel is not None or self._details_open or self._child_panel is not None
                or self._plan_panel is not None or self._mcp_panel is not None or self._attachment_panel is not None):
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
        self._remote_parent_edited()
        count = len(self._composer.items)
        self._editor.set_attachment_summary(f"Images: {count} · /attachments inspect/remove/preview" if count else "")
        self._tui.request_render()

    def _retained_image_bytes(self) -> int:
        items = {item.id: item for group in (*self._local_images.values(), *self._routed_images.values(),
                                           self._active_images) for item in group}
        return sum(len(item.data) for item in items.values()) + self._child_draft_bytes() + self._parent_saved_bytes()

    def _child_draft_bytes(self, *, exclude_saved: bool = False) -> int:
        items = {item.id: item for scope, cache in self._child_drafts.items()
                 if not exclude_saved or not scope.startswith('saved-draft:')
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

    def _open_plan(self) -> None:
        if self._child_panel is not None:
            self._close_children()
        if self._exit_requested or self._tui.has_overlay():
            return
        if self._plan_panel is not None:
            return
        for panel, close in ((self._mcp_panel, self._close_mcp), (self._transcript_panel, self._close_transcript),
                             (self._attachment_panel, self._close_attachment_preview),
                             (self._navigation_panel, self._close_navigation),
                             (self._review_panel, self._close_review)):
            if panel is not None:
                close()
        if self._details_open:
            self._close_details()
        self._plan_context = self._mcp_context_key()
        self._plan_panel = PlanPanel(lambda: {**self._plan_snapshot.to_dict(),
                                    "busy": self._plan_pending is not None or self._active_run_id is not None
                                            or self._waiting}, self._plan_action, self._close_plan)
        self._layout.details = self._outputs.panel or self._plan_panel
        self._tui.set_focus(self._outputs.panel or self._plan_panel)
        self._update_hints()
        self._tui.request_render()
        self._plan_send("show", self._plan_snapshot.revision)

    def _close_plan(self) -> None:
        self._plan_panel = None
        self._layout.details = self._outputs.panel or self._mcp_panel or self._transcript_panel
        self._tui.set_focus(self._outputs.panel or self._mcp_panel or self._transcript_panel or self._editor)
        self._update_hints()
        self._tui.request_render()

    def _plan_action(self, action: str, revision: int) -> None:
        if self._plan_context != self._mcp_context_key():
            self._close_plan()
            return
        self._plan_send(action, revision)

    def _plan_send(self, action: str, revision: int, objective: str = "") -> bool:
        if not self._connection_ready or not self._plan_supported:
            self._chat_log.add_system("Plan controls require a ready updated gateway")
            return False
        if self._plan_pending is not None or action != "show" and (self._active_run_id is not None or self._waiting):
            self._chat_log.add_system("Wait for idle plan/group ownership before changing mode")
            return False
        identifier = "plan-" + uuid.uuid4().hex
        self._plan_pending = identifier, action, self._gateway_session_id
        self._queue_request({"type": "plan_control", "request_id": identifier, "group_id": self.group_id,
                             "session_id": self._gateway_session_id, "revision": revision,
                             "action": action, "objective": objective})
        return True

    def _open_children(self, target: str = "") -> None:
        if self._outputs.panel is not None or self._extension_dialog_panel is not None or self._tui.has_overlay():
            return
        if self._child_panel is not None:
            if target:
                self._child_panel.inspect(target)
            return
        for panel, close in ((self._plan_panel, self._close_plan), (self._mcp_panel, self._close_mcp),
                             (self._transcript_panel, self._close_transcript),
                             (self._navigation_panel, self._close_navigation),
                             (self._review_panel, self._close_review)):
            if panel is not None:
                close()
        if self._details_open:
            self._close_details()
        self._child_context = self._mcp_context_key()
        self._child_target = target
        self._child_draft_labels[self._child_context] = (
            f'{self._state.get("workspace_dir", ".")} · group {self.group_id} · session {self._gateway_session_id}')
        self._child_panel = ChildPanel(self._child_send, self._child_read, self._close_children,
                                       self._child_output, self._child_fullscreen,
                                       configure=lambda value: self._child_presets.resolve(value).to_dict(),
                                       configured_start=self._child_configured_start,
                                       image_send=self._child_image_send,
                                       root=Path(str(self._state.get('workspace_dir', '.'))),
                                       drafts=self._child_drafts.setdefault(self._child_context, {}),
                                       retained_bytes=self._child_retained_bytes,
                                       draft_source=self._child_draft_entries,
                                       draft_action=self._child_draft_action)
        self._child_panel.queue_enabled = self._child_queues_supported
        self._child_panel.queue_image_recovery_enabled = self._child_image_recovery_supported
        self._child_panel.queue_task_recovery_enabled = self._child_task_recovery_supported
        self._child_panel.queue_image_return_enabled = self._child_return_supported
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
        # Existing parent catalog is replaced; every unrelated active/local owner stays counted.
        return max(0, 40 * 1024 * 1024 - self._retained_image_bytes()
                   + self._parent_saved_bytes() - self._composer.bytes)

    def _parent_draft_snapshot(self) -> ChildDraft:
        return ChildDraft(self._editor.get_text(), images=tuple(self._composer.items.values()),
                          cursor=self._editor._cursor)

    def _parent_draft_command(self, args: str) -> None:
        try:
            if args.strip() == 'remote-save':
                self._save_remote_parent()
            elif args.strip() == 'remote':
                self._open_remote_parent()
            elif args.strip() == 'save':
                self._show_parent_save()
            elif args.strip().partition(' ')[0] == 'saved':
                if not self._parent_draft_ui_available():
                    raise ValueError('Another modal UI owns input; parent snapshot read not admitted')
                self._open_children()
                panel = self._child_panel
                if panel is None:
                    raise ValueError('Parent recovery view unavailable; no read admitted')
                lease = panel.terminal_lease
                draft_view, queue_view, preview = panel.draft_panel, panel.queue, panel.preview
                self._parent_drafts.command(args, alive=lambda: (
                    self._child_panel is panel and not self._exit_requested and panel.terminal_lease == lease
                    and panel.draft_panel is draft_view and panel.queue is queue_view and panel.preview is preview
                    and self._parent_draft_ui_available()))
            else:
                self._parent_drafts.command(args)
        except ValueError as exc:
            self._chat_log.add_system(str(exc))
        self._tui.request_render()

    def _remote_parent_edited(self) -> None:
        self._remote_parent_revision += 1

    def _remote_parent_composer(self) -> dict[str, Any]:
        return composer_receipt(self._parent_draft_snapshot(), self._remote_parent_revision)

    def _remote_parent_alive(self) -> bool:
        return (self._remote_parent_panel is not None and self._connection_ready
                and self._remote_parent_context == self._mcp_context_key() and not self._exit_requested
                and self._parent_draft_ui_available() and self._active_run_id is None and not self._waiting
                and self._tui._focused is self._remote_parent_panel)

    def _open_remote_parent(self, *, catalog: bool = True) -> None:
        if (not self._connection_ready or self._remote_parent_caps.get('parent_saved_drafts') != 1
                or not self._parent_draft_ui_available() or self._active_run_id is not None or self._waiting):
            raise ValueError('Remote parent drafts require current idle negotiated gateway')
        if self._remote_parent_worker is not None and self._remote_parent_worker.is_alive():
            raise ValueError('Remote parent decode still settling')
        self._open_children()
        self._close_children()
        self._remote_parent_receiver.close()
        self._remote_parent_receiver = ParentDraftReceiver(
            lambda: max(0, 40 * 1024 * 1024 - self._retained_image_bytes() - self._composer.bytes))
        if self._remote_parent_panel is not None:
            self._close_remote_parent()
        self._remote_parent_context = self._mcp_context_key()
        self._remote_parent_panel = RemoteParentDraftPanel(self._read_remote_parent, self._close_remote_parent,
            discard=self._discard_remote_parent)
        self._layout.details = self._remote_parent_panel
        self._tui.set_focus(self._remote_parent_panel)
        self._remote_parent_request = 'parent-' + uuid.uuid4().hex
        self._remote_parent_scope = {'request_id': self._remote_parent_request, 'group_id': self.group_id,
            'session_id': self._gateway_session_id, 'view': uuid.uuid4().hex,
            'parent_draft_owner': self._remote_parent_caps['parent_draft_owner'],
            'parent_draft_generation': self._remote_parent_caps['parent_draft_generation']}
        self._remote_parent_deadline = time.monotonic() + 30
        if catalog:
            self._queue_request({'type': 'parent_draft_control', **self._remote_parent_scope, 'action': 'catalog'})
        self._update_hints()
        self._tui.request_render()

    def _close_remote_parent(self) -> None:
        self._remote_parent_decode_cancel.set()
        if self._connection_ready and self._remote_parent_scope:
            self._queue_request({'type': 'parent_draft_control',
                **{key: value for key, value in self._remote_parent_scope.items() if key != 'session_revision'},
                'request_id': 'parent-view-cancel-' + uuid.uuid4().hex, 'action': 'view_cancel'})
        self._remote_parent_upload = None
        self._remote_parent_review_id = ''
        self._remote_parent_selected = {}
        panel, self._remote_parent_panel = self._remote_parent_panel, None
        if panel is not None:
            panel.close()
        # A decode worker owns the original spools until finish returns. View
        # revocation blocks delivery immediately without closing its active files.
        if self._remote_parent_worker is None or not self._remote_parent_worker.is_alive():
            self._remote_parent_receiver.close()
        self._remote_parent_request = ''
        self._remote_parent_operation = ''
        self._remote_parent_scope = {}
        if self._layout.details is panel:
            self._layout.details = None
            self._tui.set_focus(self._editor)
        self._tui.request_render()

    def _read_remote_parent(self, row: dict[str, Any]) -> None:
        if not self._remote_parent_alive() or self._editor.get_text() or self._composer.items:
            raise ValueError('Remote recovery needs original empty composer')
        if row['image_bytes'] + self._retained_image_bytes() + self._composer.bytes > 40 * 1024 * 1024:
            raise ValueError('Remote parent recovery exceeds current retained-image capacity')
        old = dict(self._remote_parent_scope)
        self._remote_parent_request = 'parent-' + uuid.uuid4().hex
        scope = {**old, 'request_id': self._remote_parent_request}
        self._remote_parent_read_completed = False
        self._remote_parent_scope = scope
        self._remote_parent_receiver.expect(scope, self._remote_parent_composer(), row['id'], row['sha256'])
        self._remote_parent_deadline = time.monotonic() + 30
        self._queue_request({'type': 'parent_draft_control',
            **{key: value for key, value in scope.items() if key != 'session_revision'},
            'action': 'read', 'composer': self._remote_parent_composer(),
            'snapshot_id': row['id'], 'snapshot_sha256': row['sha256']})

    def _remote_parent_control(self, action: str, **fields: Any) -> None:
        if not self._remote_parent_alive():
            raise ValueError('Remote parent original view unavailable')
        self._remote_parent_request = 'parent-' + uuid.uuid4().hex
        self._remote_parent_scope = {**self._remote_parent_scope, 'request_id': self._remote_parent_request}
        self._remote_parent_operation = action
        self._remote_parent_deadline = time.monotonic() + 30
        self._queue_request({'type': 'parent_draft_control',
            **{key: value for key, value in self._remote_parent_scope.items() if key != 'session_revision'},
            'action': action, **fields})

    def _save_remote_parent(self) -> None:
        if self._remote_parent_caps.get('parent_saved_save') != 1:
            raise ValueError('Remote parent save requires independent gateway capability')
        draft = self._parent_draft_snapshot()
        receipt = self._remote_parent_composer()
        if not draft.text and not draft.images:
            raise ValueError('Remote parent composer is empty; nothing to save')
        self._open_remote_parent(catalog=False)
        panel = self._remote_parent_panel
        assert panel is not None
        if draft.images:
            if self._remote_parent_caps.get('parent_saved_uploads') != 1:
                self._close_remote_parent()
                raise ValueError('Updated independently negotiated parent upload gateway required')
            self._remote_parent_upload = {'draft': draft, 'composer': receipt, 'image': 0, 'offset': 0,
                                          'refs': ['parent-image-' + uuid.uuid4().hex for _ in draft.images]}
            self._send_remote_parent_chunk()
        else:
            self._remote_parent_control('save_prepare', text=draft.text, images=[],
                                        composer=receipt, label='Claw remote parent draft')
        panel.message = 'Preparing remote parent save review; no disk write yet'

    def _send_remote_parent_chunk(self) -> None:
        upload = self._remote_parent_upload
        if upload is None or not self._remote_parent_alive():
            raise ValueError('Parent upload view revoked')
        if _json(self._remote_parent_composer()) != _json(upload['composer']):
            raise ValueError('Parent upload composer changed')
        draft = upload['draft']
        index, offset = upload['image'], upload['offset']
        if index == len(draft.images):
            self._remote_parent_control('save_prepare', text=draft.text, image_refs=upload['refs'],
                image_names=[image.name for image in draft.images], composer=upload['composer'],
                label='Claw remote parent draft')
            self._remote_parent_upload = None
            return
        image = draft.images[index]
        final = offset + 65536 >= len(image.data)
        self._remote_parent_control('upload_chunk', id=upload['refs'][index], index=offset // 65536,
            mime_type=image.mime_type, data=base64.b64encode(image.data[offset:offset + 65536]).decode(),
            final=final, **({'sha256': hashlib.sha256(image.data).hexdigest()} if final else {}))
        upload['offset'] = offset + 65536
        upload['last_final'] = final

    def _discard_remote_parent(self, row: dict[str, Any]) -> None:
        if self._remote_parent_caps.get('parent_saved_discard') != 1:
            raise ValueError('Remote parent discard requires independent gateway capability')
        self._remote_parent_selected = dict(row)
        self._remote_parent_control('discard_prepare', snapshot_id=row['id'], snapshot_sha256=row['sha256'],
                                    composer=self._remote_parent_composer())

    def _remote_parent_mutation_review(self, msg: Mapping[str, Any]) -> None:
        panel = self._remote_parent_panel
        if panel is None or not self._remote_parent_alive():
            raise ValueError('Remote parent review view unavailable')
        scope = dict(self._remote_parent_scope)
        if any(type(msg.get(key)) is not type(value) or msg.get(key) != value
               for key, value in scope.items()):
            raise ValueError('Remote parent review owner changed')
        body = json.loads(_json(msg.get('review')))
        operation = 'save' if msg.get('action') == 'save_prepare' else 'discard'
        if (body.get('operation') != operation or _json(body.get('composer')) != _json(self._remote_parent_composer())
                or not isinstance(body.get('content'), str) or len(body['content'].encode()) > 65536
                or not isinstance(msg.get('review_id'), str)
                or re.fullmatch(r'[a-f0-9]{32}', msg['review_id']) is None):
            raise ValueError('Remote parent review data/composer changed')
        expected_fields = {'operation', 'composer', 'label', 'content', 'images', 'snapshot_sha256', 'notice'}
        if operation == 'discard':
            expected_fields.add('snapshot_id')
        if (set(body) != expected_fields or not isinstance(body['images'], list) or len(body['images']) > 4
                or not isinstance(body['label'], str) or len(body['label'].encode()) > 4096
                or not isinstance(body['snapshot_sha256'], str)
                or re.fullmatch(r'[a-f0-9]{64}', body['snapshot_sha256']) is None):
            raise ValueError('Invalid remote disk review shape')
        if not isinstance(body.get('notice'), str) or len(body['notice'].encode()) > 8192:
            raise ValueError('Invalid remote disk review notice')
        for image in body['images']:
            if (not isinstance(image, dict) or set(image) != {'name', 'mime_type', 'bytes', 'sha256'}
                    or not isinstance(image['name'], str) or len(image['name'].encode()) > 1024
                    or image['mime_type'] not in {'image/png', 'image/jpeg', 'image/webp'}
                    or type(image['bytes']) is not int or not 0 < image['bytes'] <= 10 * 1024 * 1024
                    or not isinstance(image['sha256'], str)
                    or re.fullmatch(r'[a-f0-9]{64}', image['sha256']) is None):
                raise ValueError('Invalid remote disk image review')
        if operation == 'discard' and (len(body['images']) != self._remote_parent_selected.get('image_count')
                or sum(image['bytes'] for image in body['images'])
                != self._remote_parent_selected.get('image_bytes')):
            raise ValueError('Remote discard manifest differs from selected catalog')
        if operation == 'discard' and any(body.get(key) != self._remote_parent_selected.get(source)
                for key, source in [('snapshot_id', 'id'), ('snapshot_sha256', 'sha256'), ('label', 'label')]):
            raise ValueError('Remote discard target differs from selected catalog')
        if operation == 'save':
            draft = self._parent_draft_snapshot()
            manifest = [{'name': image.name, 'mime_type': image.mime_type, 'bytes': len(image.data),
                         'sha256': hashlib.sha256(image.data).hexdigest()} for image in draft.images]
            if body['content'] != draft.text or body.get('images') != manifest:
                raise ValueError('Remote save review differs from original composer')
        self._remote_parent_scope['session_revision'] = msg['session_revision']
        owner = dict(self._remote_parent_scope)
        review_id = msg['review_id']
        self._remote_parent_review_id = review_id
        consumed = False
        def confirm(_response: dict[str, Any]) -> None:
            nonlocal consumed
            if (consumed or not self._remote_parent_alive() or self._remote_parent_scope != owner
                    or _json(self._remote_parent_composer()) != _json(body['composer'])):
                raise ValueError('Remote parent review/composer changed; no mutation')
            consumed = True
            self._remote_parent_review_id = ''  # Confirmation owns outcome; close must not imply rollback.
            self._remote_parent_control(operation + '_confirm', review_id=review_id, review=body,
                                        confirmed=True, composer=body['composer'])
            panel.review = None
            panel.busy = True
            panel.message = 'Remote disk confirmation settling; composer unchanged, no replay'
        event = {'type': 'sampling_review', 'phase': 'output', 'id': '0' * 32, 'receipt': review_id,
            'body': body, 'sha256': hashlib.sha256(_json(body).encode()).hexdigest(),
            'notice': 'Review full remote ' + operation + ' receipt; DISK snapshot only, no submission.'}
        panel.review = McpSamplingPanel(event, (), confirm, self._close_remote_parent)
        panel.review.focused = True
        panel.review.set_render_budget(panel._budget)
        panel.busy = False

    def _remote_parent_response(self, msg: Mapping[str, Any]) -> bool:
        if msg.get('type') not in {'parent_draft_begin', 'parent_draft_chunk', 'parent_draft_end',
                                  'parent_draft_result', '_remote_parent_decoded'}:
            return False
        panel = self._remote_parent_panel
        if panel is None or msg.get('request_id') != self._remote_parent_request:
            return True
        if not self._remote_parent_alive():
            self._close_remote_parent()
            return True
        try:
            if msg['type'] == '_remote_parent_decoded':
                if msg.get('error') or not isinstance(msg.get('recovery'), ParentRecovery):
                    raise ValueError('Remote parent decode refused')
                recovery = msg['recovery']
                if (self._retained_image_bytes() + self._composer.bytes
                        + sum(len(image.data) for image in recovery.draft.images) > 40 * 1024 * 1024):
                    raise ValueError('Remote parent retained-image capacity changed before review')
                def adopt(draft: ChildDraft, expected: dict[str, Any]) -> None:
                    if (not self._remote_parent_alive() or self._remote_parent_composer() != expected
                            or self._editor.get_text() or self._composer.items
                            or self._retained_image_bytes() + sum(len(image.data) for image in draft.images)
                            > 40 * 1024 * 1024):
                        raise ValueError('Remote parent composer/capacity changed; no adoption')
                    if _sanitize_editor_text(draft.text) != draft.text:
                        raise ValueError('Remote text cannot be adopted losslessly')
                    self._editor.set_text(draft.text)
                    self._composer.restore(draft.images)
                    self._close_remote_parent()
                    self._chat_log.add_system('Remote parent draft recovered; NOT sent')
                panel.review = recovery.panel(lambda: self._remote_parent_scope,
                    self._remote_parent_composer, adopt, self._close_remote_parent)
                panel.review.focused = True
                panel.review.set_render_budget(panel._budget)
                panel.busy = False
                self._remote_parent_deadline = time.monotonic() + 30
            elif msg['type'] != 'parent_draft_result':
                self._remote_parent_receiver.feed({key: value for key, value in msg.items()
                                                  if key != '_connection_generation'})
            elif msg.get('success') is not True:
                raise ValueError('Remote parent read refused')
            elif msg.get('action') == 'upload_chunk':
                upload = self._remote_parent_upload
                if (upload is None or any(msg.get(key) != value for key, value in self._remote_parent_scope.items())
                        or msg.get('upload_id') != upload['refs'][upload['image']]
                        or msg.get('upload_status') != ('ready' if upload['last_final'] else 'partial')):
                    raise ValueError('Parent upload acknowledgment owner/sequence changed')
                if upload['last_final']:
                    upload['image'] += 1
                    upload['offset'] = 0
                self._send_remote_parent_chunk()
            elif msg.get('action') in {'save_prepare', 'discard_prepare'}:
                self._remote_parent_mutation_review(msg)
            elif msg.get('action') in {'save_confirm', 'discard_confirm'}:
                if any(msg.get(key) != value for key, value in self._remote_parent_scope.items()):
                    raise ValueError('Remote mutation response owner changed')
                self._chat_log.add_system('Remote parent disk ' + str(msg['action']) + ' completed; composer unchanged')
                self._close_remote_parent()
            elif msg.get('action') == 'catalog':
                if any(msg.get(key) != value for key, value in self._remote_parent_scope.items()):
                    raise ValueError('Remote catalog owner changed')
                rows = msg.get('catalog')
                if not isinstance(rows, (tuple, list)) or len(rows) > 32:
                    raise ValueError('Invalid remote catalog')
                for row in rows:
                    if (not isinstance(row, dict) or not isinstance(row.get('id'), str)
                            or re.fullmatch(r'[a-f0-9]{32}', row['id']) is None
                            or not isinstance(row.get('sha256'), str)
                            or re.fullmatch(r'[a-f0-9]{64}', row['sha256']) is None
                            or not isinstance(row.get('label'), str) or len(row['label'].encode()) > 4096
                            or type(row.get('image_count')) is not int or not 0 <= row['image_count'] <= 4
                            or type(row.get('image_bytes')) is not int
                            or not 0 <= row['image_bytes'] <= 40 * 1024 * 1024):
                        raise ValueError('Invalid remote parent catalog row')
                self._remote_parent_scope['session_revision'] = msg['session_revision']
                panel.rows = tuple(dict(row) for row in rows)
                panel.busy = False
                panel.message = 'Select snapshot; empty composer required'
                self._remote_parent_deadline = 0
            elif msg.get('action') == 'read':
                if self._remote_parent_read_completed:
                    return True  # Duplicate terminal receipt cannot spawn/replace a decoder or review.
                self._remote_parent_read_completed = True
                receiver, key, result = self._remote_parent_receiver, self._remote_parent_request, dict(msg)
                self._remote_parent_decode_cancel = canceled = threading.Event()
                def decode() -> None:
                    event: dict[str, Any] = {'type': '_remote_parent_decoded', 'request_id': key}
                    try:
                        recovery = receiver.finish(key, result)
                        if not canceled.is_set():
                            event['recovery'] = recovery
                    except (ValueError, RuntimeError, OSError):
                        event['error'] = True
                    finally:
                        if canceled.is_set():
                            receiver.close()  # Worker settled; no active spool is closed prematurely.
                    if not canceled.is_set():
                        self._recv_queue.put(event)
                self._remote_parent_worker = threading.Thread(target=decode, daemon=True,
                                                               name='xdog-parent-recovery')
                self._remote_parent_worker.start()
        except (ValueError, TypeError, KeyError):
            self._chat_log.add_system('Remote parent snapshot owner/data changed; no adoption')
            self._close_remote_parent()
        self._tui.request_render()
        return True

    def _show_parent_save(self) -> None:
        if not self._parent_draft_ui_available():
            raise ValueError('Another modal UI owns input; parent draft unchanged')
        self._open_children()
        panel = self._child_panel
        if panel is None:
            raise ValueError('Parent save view unavailable; no write admitted')
        # Capture exact composer before opening its review; no consumption or mutation.
        self._parent_drafts.prepare_save(alive=lambda: self._child_panel is panel and not self._exit_requested)
        entry = self._parent_drafts.save_entry()
        def act(action: str, reviewed: DraftEntry) -> None:
            if (action != 'save' or self._child_panel is not panel or panel.draft_panel is not browser
                    or panel.terminal_lease != lease or not self._parent_draft_ui_available()):
                raise ValueError('Parent save view changed; reopen full review')
            self._parent_drafts.confirm_save(reviewed)
            panel.back()
        def back() -> None:
            self._parent_drafts.invalidate_read()
            panel.back()
        browser = ChildDraftPanel(lambda: (entry,), act, back, parent=True, save_only=True)
        panel._mode = 'drafts'
        panel._input.focused = False
        panel.draft_panel = browser
        lease = panel.terminal_lease
        self._parent_drafts.bind_read_view(lambda: (
            self._child_panel is panel and panel.draft_panel is browser and panel.terminal_lease == lease
            and self._parent_draft_ui_available()))
        browser.focused = True
        browser._prepare('save', entry)
        self._tui.set_focus(panel)
        self._tui.request_render()

    def _show_parent_drafts(self) -> None:
        if not self._parent_draft_ui_available():
            self._parent_drafts.invalidate_read()
            self._chat_log.add_system('Parent recovery blocked by modal owner; explicitly reload after it settles')
            return
        self._open_children()
        panel = self._child_panel
        if panel is None:
            return
        def act(action: str, entry: DraftEntry) -> None:
            if (self._child_panel is not panel or panel.draft_panel is not browser
                    or panel.terminal_lease != lease or not self._parent_draft_ui_available()):
                raise ValueError('Parent recovery view changed; reopen and review')
            def restore(draft: ChildDraft) -> None:
                retained = self._retained_image_bytes() - self._parent_saved_bytes() + self._composer.bytes
                if retained + sum(len(item.data) for item in draft.images) > 40 * 1024 * 1024:
                    raise ValueError('Parent recovery exceeds local retention; existing draft preserved')
                self._editor.set_text(draft.text)
                self._composer.restore(draft.images)
                self._close_children()
                self._chat_log.add_system('Parent draft recovered; NOT sent · inspect before explicit submission')
            self._parent_drafts.action(action, entry, restore)
        panel._mode = 'drafts'
        panel._input.focused = False
        def back() -> None:
            self._parent_drafts.invalidate_read()
            panel.back()
        browser = ChildDraftPanel(self._parent_drafts.entries, act, back, parent=True)
        panel.draft_panel = browser
        lease = panel.terminal_lease
        self._parent_drafts.bind_read_view(lambda: (
            self._child_panel is panel and not self._exit_requested and panel.terminal_lease == lease
            and panel.draft_panel is browser and self._parent_draft_ui_available()))
        browser.focused = True
        self._tui.set_focus(panel)
        self._tui.request_render()

    def _poll_child_autosave(self, *, flush: bool = False) -> bool:
        if self._child_panel is not None and self._child_autosave.enabled:
            self._child_panel._save_draft()
        entries = self._child_draft_entries() if self._child_autosave.enabled else ()
        return self._child_autosave.poll(self._durable_child_store(), entries,
                                         self._chat_log.add_system, flush=flush)

    def _child_autosave_command(self, value: str) -> None:
        if value not in {'on', 'off', ''}:
            self._chat_log.add_system('Usage: /agents autosave [on|off]')
            return
        if value:
            self._child_autosave.set_enabled(value == 'on')
        self._chat_log.add_system('Child autosave ' + ('ON' if self._child_autosave.enabled else 'OFF')
            + ' · PRIVATE PLAINTEXT · pending/writing edits are not acknowledged durable; '
            'committed is a polled disk receipt, not per-key saving · /agents saved recovery only')
        self._tui.request_render()

    def _durable_child_store(self) -> ChildDraftStore:
        return ChildDraftStore(get_data_dir() / 'saved-child-drafts', create_parent=True)

    def _load_saved_child_drafts(self, selection: str = '') -> None:
        self._open_children()
        panel = self._child_panel
        if panel is None:
            return
        if self._child_draft_io.busy:
            raise ValueError('Durable draft work still settling; no repeated load')
        action = 'catalog' if selection == 'list' else 'load' if selection else 'list'
        if selection not in {'', 'list'} and (len(selection) != 32
                or any(char not in '0123456789abcdef' for char in selection)):
            raise ValueError('Use /agents saved [list|SNAPSHOT_ID]')
        self._child_draft_io.start(action, self._durable_child_store(), owner=panel.owner_token, key=selection)
        self._chat_log.add_system('Loading explicit saved child snapshots; no agent execution')

    def _finish_child_draft_io(self, result: dict[str, Any]) -> None:
        panel = self._child_panel
        if result.get('error'):
            self._chat_log.add_system(str(result['error']))
            if panel is not None and panel.owner_token == result['owner']:
                panel.fail(str(result['error']))
            return
        action = result['action']
        if action == 'save':
            saved = result['saved']
            self._chat_log.add_system('Child draft saved privately: ' + saved.id + ' · /agents saved reviews recovery')
        elif action == 'discard':
            key = result['discarded']
            self._saved_child_drafts.pop(key, None)
            self._child_drafts.pop('saved-draft:' + key, None)
            self._chat_log.add_system('Discarded saved snapshot only; local editable copies unchanged')
        elif action == 'catalog':
            if (panel is None or panel.owner_token != result['owner']
                    or self._child_context != self._mcp_context_key()):
                self._chat_log.add_system('Saved catalog view changed; explicitly list again')
                return
            rows = result['catalog']
            self._chat_log.add_system('Saved child snapshots (metadata only; no drafts adopted):\n' + '\n'.join(
                f"{row['id']} · {row['image_count']} images · {row['image_bytes']} bytes · "
                + str(row['label']) for row in rows)
                + '\nUse /agents saved SNAPSHOT_ID to load one for full reviewed recovery')
        elif action in {'list', 'load'}:
            if (panel is None or panel.owner_token != result['owner']
                    or self._child_context != self._mcp_context_key()):
                self._chat_log.add_system('Saved draft read owner changed; reopen /agents saved explicitly')
                return
            records = result['saved']
            total = sum(len(image.data) for record in records for image in record.draft.images)
            # Replacing catalog rows releases only their EXCLUSIVE image ownership.
            # Reviewed copies and the active child composer can still own the same
            # immutable bytes; subtracting every old saved image undercounts them.
            retained = (self._retained_image_bytes() - self._child_draft_bytes()
                        + self._child_draft_bytes(exclude_saved=True))
            if retained + self._composer.bytes + total > 40 * 1024 * 1024:
                self._chat_log.add_system('Saved draft catalog exceeds local retained-image capacity; no partial load')
                return
            # Saved provenance is always an OLD context: only fresh read-only copy,
            # never resume prior queue/model/permissions through a recovered draft.
            for key in tuple(self._saved_child_drafts):
                self._child_drafts.pop('saved-draft:' + key, None)
            self._saved_child_drafts = {record.id: record for record in records}
            for record in records:
                scope = 'saved-draft:' + record.id
                self._child_drafts[scope] = {('start', record.id): record.draft}
                self._child_draft_labels[scope] = 'SAVED PRIVATE · ' + record.label
            panel.open_drafts()
        if panel is not None and panel.owner_token == result['owner']:
            if action != 'list':
                panel.open_drafts()
            self._tui.request_render()

    def _child_draft_entries(self) -> tuple[DraftEntry, ...]:
        return draft_entries(self._child_drafts, self._child_draft_labels, self._child_context)

    def _child_draft_action(self, action: str, entry: DraftEntry) -> None:
        panel = self._child_panel
        if panel is None or self._exit_requested or self._child_context != self._mcp_context_key():
            raise ValueError('Child draft browser owner changed; reopen and review, no replay')
        if self._child_draft_io.busy:
            raise ValueError('Durable child draft operation still settling; no repeated action')
        cached = self._child_drafts.get(entry.scope, {}).get(entry.key)
        if cached is not entry.original or cached != entry.value:
            raise ValueError('Retained child draft changed; review the current snapshot')
        if action == 'save':
            self._child_draft_io.start('save', self._durable_child_store(), owner=panel.owner_token, entry=entry)
            panel.back()
            return
        if action == 'discard' and entry.scope.startswith('saved-draft:'):
            key = entry.scope.removeprefix('saved-draft:')
            saved = self._saved_child_drafts.get(key)
            if saved is None or saved.draft is not entry.original:
                raise ValueError('Saved draft receipt owner changed; reload and review')
            self._child_draft_io.start('discard', self._durable_child_store(), owner=panel.owner_token, saved=saved)
            panel.back()
            return
        if action == 'discard':
            del self._child_drafts[entry.scope][entry.key]
            panel.open_drafts()
        elif action in {'copy', 'reopen'}:
            if action == 'reopen' and entry.scope != self._child_context:
                raise ValueError('Old contexts cannot resume; explicitly copy fresh/read-only')
            panel.adopt_draft(entry, reopen=action == 'reopen')
        else:
            raise ValueError('Unknown local child draft action')

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
        panel = self._child_panel
        if panel is None:
            return
        if (not self._connection_ready or not self._children_supported or len(self._children_pending) >= 8
                or self._child_context != self._mcp_context_key()
                or action == "start" and (self._active_run_id is not None or self._waiting)
                or action == 'queue' and not self._child_queues_supported):
            panel.fail("Ready updated gateway/idle start required; child form retained")
            return
        command = json.loads(text) if action == 'queue' else {}
        returning = command.get('action') in {'return_prepare', 'return_commit', 'return_cancel'}
        if returning and (not self._child_return_supported or panel.queue is None):
            panel.fail('Updated live image-return gateway required; original not removed')
            return
        identifier = "child-ui-" + uuid.uuid4().hex
        if action == 'queue' and panel.queue is not None:
            self._child_queue_owners[identifier] = panel.queue.owner_token
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
                             "text": text, "access": access, "config": wire_config,
                             **({'child_return_owner': self._child_return_owner,
                                 'queue_view': panel.queue.owner_token}
                                if returning and panel.queue is not None else {})},
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
        self._parent_drafts.invalidate_read()
        self._leave_child_fullscreen()
        self._cancel_child_source()
        if self._child_panel is not None:
            self._child_panel.close()
        self._child_panel = None
        self._child_target = ""
        self._child_ui_requests.clear()
        self._child_recovery_ready.clear()
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
        if action == 'autosave':
            self._child_autosave_command(rest.strip())
            return
        if action == 'saved':
            try:
                self._load_saved_child_drafts(rest.strip())
            except ValueError as exc:
                self._chat_log.add_system(str(exc))
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

    def _plan_command(self, args: str) -> None:
        action, _, text = args.strip().partition(" ")
        if action in {"", "show"}:
            self._open_plan()
            return
        if (action not in {"start", "execute", "revise", "stop"} or action == "start" and not text.strip()
                or action == "execute" and not text.isdigit()
                or action in {"revise", "stop"} and text and not text.isdigit()):
            self._chat_log.add_system("Usage: /plan start OBJECTIVE | execute REVISION | revise | stop")
            return
        revision = (int(text) if action in {"execute", "revise", "stop"} and text.isdigit()
                    else self._plan_snapshot.revision)
        if not self._plan_send(action, revision, text if action == "start" else ""):
            self._preserve_submission("/plan " + args, "Plan command not sent; draft preserved")

    def _command_editor_snapshot(self) -> tuple[str, int, tuple[str, ...]]:
        return (self._editor.get_text(), self._editor._cursor,
                tuple(item.id for item in self._composer.items.values()))

    def _command_editor_replace(self, value: str) -> bool:
        if len(value.encode()) > 65536 or _sanitize_editor_text(value) != value:
            return False
        self._editor.set_text(value)
        self._tui.request_render()
        return True

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

    def _queued_input_review_current(self, item: dict[str, Any]) -> bool:
        if (not self._connection_ready or self._exit_requested or self._session_context_stale):
            return False
        if item.get('child_id'):
            return (self._child_queued_inputs_supported and item.get('session_id') == self._gateway_session_id
                    and item.get('child_owner') == self._child_queued_inputs_owner
                    and type(item.get('_child_queue_generation')) is int
                    and item['_child_queue_generation'] == self._child_queued_inputs_generation)
        return (self._extension_queued_inputs_supported and self._child_panel is None
                and self._active_run_id is not None and item.get('_queue_run') == self._active_run_id
                and self._aborting_run_id != self._active_run_id
                and type(item.get('_queue_generation')) is int
                and item['_queue_generation'] == self._extension_queued_inputs_generation)

    def _retire_extension_inputs(self, owner: str) -> None:
        retired = False
        for key, (run, _images) in tuple(self._extension_queued_inputs.items()):
            if run == owner:
                self._extension_queued_inputs.pop(key, None)
                self._routed_inputs.pop(key, None)
                self._transformed_inputs.pop(key, None)
                retired = True
        if retired:
            self._chat_log.add_system('Unconsumed/uncertain extension inputs not replayed; original draft kept')

    def _prepare_editor_write(self, dialog: dict[str, Any]) -> bool:
        from xdog.agent.extensions.editor_request import METHODS
        identity = dialog.get('editor_write', {})
        child = str(identity.get('child_id', ''))
        if (not self._extension_editor_write_supported or identity.get('session_id') != self._gateway_session_id
                or identity.get('method') not in METHODS or child != dialog.get('child_id', '')
                or type(dialog.get('editor_write_generation')) is not int
                or dialog['editor_write_generation'] != self._extension_editor_write_generation):
            return False
        context, generation, capability_owner = (self._mcp_context_key(), self._extension_editor_write_generation,
                                                 self._extension_editor_write_owner)
        def base_alive() -> bool:
            return (self._connection_ready and not self._exit_requested and not self._session_context_stale
                    and self._extension_editor_write_supported and self._mcp_context_key() == context
                    and self._extension_editor_write_generation == generation
                    and self._extension_editor_write_owner == capability_owner
                    and self._session_mutation_id is None and self._remote_parent_panel is None)
        if child:
            panel = self._child_panel
            if (panel is None or panel.terminal_child != child
                    or dialog.get('child_owner') != capability_owner):
                return False  # No selected original child draft; never borrow the parent composer.
            lease = panel.terminal_lease
            def alive() -> bool:
                return (base_alive() and self._child_panel is panel
                        and panel.terminal_lease == lease and not panel._busy
                        and (panel.composer is None or not panel.composer.reader.pending))
            def snapshot() -> tuple[str, int, tuple[str, ...]]:
                return (panel._input.get_text(), panel._input._cursor,
                        tuple(panel.composer.items) if panel.composer is not None else ())
            def replace(value: str) -> bool:
                if _sanitize_editor_text(value) != value:
                    return False
                panel._input.set_text(value)
                return True
        else:
            editor, composer = self._editor, self._composer
            command, run = dialog.get('_editor_write_command'), dialog.get('_editor_write_run')
            if not command and not run:
                return False
            def alive() -> bool:
                command_alive = (bool(command) and self._extension_pending is not None
                                 and self._extension_pending[0] == command)
                run_alive = bool(run) and self._active_run_id == run
                return (base_alive() and self._editor is editor and self._composer is composer
                        and self._child_panel is None and not composer.reader.pending and (command_alive or run_alive))
            snapshot = self._command_editor_snapshot
            replace = self._command_editor_replace
        return self._editor_write_reviews.prepare(dialog, snapshot, replace, alive)

    def _queue_extension_navigation(self, item: dict[str, Any], answer: Any, *, accepted: bool = False) -> bool:
        pending = self._extension_pending
        owner = pending[0] if pending is not None else ""
        allowed = (pending is not None and not item.get("child_id") and self._active_run_id is None
                   and self._extension_editor_owner == (owner, str(item.get("extension", "")).lower())
                   and self._connection_ready and not self._exit_requested
                   and (self._extension_message_supported if item.get('operation') == 'message' else
                        self._extension_compaction_supported if item.get('operation') == 'compact' else
                        self._extension_name_supported if item.get('operation') == 'rename'
                        else self._extension_navigation_supported))
        if (answer is not True or not allowed or self._extension_navigation is not None
                or self._extension_navigation_review is not None):
            return False
        if item.get('operation') == 'message' and (
                type(item.get('message_generation')) is not int
                or item['message_generation'] != self._extension_message_generation):
            return False
        if item.get('operation') == 'compact' and (
                type(item.get('compaction_generation')) is not int
                or item['compaction_generation'] != self._extension_compaction_generation):
            return False
        if not accepted:
            self._extension_navigation_review = owner, self._mcp_context_key(), dict(item)
            return True
        if item.get('operation') == 'message':
            self._extension_navigation = owner, self._mcp_context_key(), {'action': 'message',
                'host_message': item['host_message'], 'message_generation': item['message_generation'],
                'image_count': len(item.get('image_summaries', [])),
                'message_images_generation': item.get('message_images_generation')}
            return True
        if item.get('operation') == 'compact':
            self._extension_navigation = owner, self._mcp_context_key(), {'action': 'compact',
                'instructions': item['instructions'], 'compaction_generation': item['compaction_generation']}
            return True
        if item.get('operation') == 'rename':
            self._extension_navigation = owner, self._mcp_context_key(), {
                'action': 'rename', 'name': item['name'], 'expected_name': item['expected_name']}
            return True
        target = dict(item["target"])
        if not target["session_id"] and target["action"] != "new":
            target["session_id"] = self._command_session_id()
        self._extension_navigation = owner, self._mcp_context_key(), target
        return True

    def _accept_extension_navigation(self, event: Mapping[str, Any]) -> None:
        review = self._extension_navigation_review
        if review is None or event.get("id") != review[2]["id"]:
            return
        self._extension_navigation_review = None
        item = review[2]
        if (event.get("extension") != item.get("extension")
                or event.get("operation") != item.get("operation")
                or review[1] != self._mcp_context_key()):
            return
        if (self._extension_pending is None or self._extension_pending[0] != review[0]
                or event.get("request_id") != review[0] or event.get("group_id") != self.group_id):
            return
        self._queue_extension_navigation(item, True, accepted=True)

    def _finish_extension_navigation(self, owner: str, success: bool,
                                     delivery: Any = None) -> bool:
        self._extension_navigation_review = None
        queued, self._extension_navigation = self._extension_navigation, None
        if queued is None:
            return False
        if (queued[:2] != (owner, self._mcp_context_key()) or not success or self._exit_requested):
            self._chat_log.add_system('Reviewed extension name discarded; callback/owner changed'
                if queued[2].get('action') == 'rename' else
                'Reviewed extension navigation discarded; callback/owner changed')
            return False
        target = queued[2]
        if target['action'] == 'message':
            if (not self._connection_ready or not self._extension_message_supported
                    or target['message_generation'] != self._extension_message_generation
                    or self._active_run_id is not None or self._session_context_stale
                    or not isinstance(delivery, dict) or set(delivery) != {'token', 'generation'}
                    or not isinstance(delivery['token'], str) or not 0 < len(delivery['token']) <= 128
                    or type(delivery['generation']) is not int
                    or delivery['generation'] != self._extension_message_generation):
                self._chat_log.add_system('Reviewed message discarded; owner/capability/delivery changed, no replay')
                return False
            if target.get('image_count') and (not self._extension_message_images_supported
                    or target.get('message_images_generation') != self._extension_message_images_generation):
                self._chat_log.add_system('Reviewed message images discarded; image grant changed, no replay')
                return False
            self._start_message(target['host_message'], (),
                requested_message={**delivery, 'image_count': target.get('image_count', 0)})
        elif target['action'] == 'compact':
            if (not self._extension_compaction_supported
                    or target['compaction_generation'] != self._extension_compaction_generation):
                self._chat_log.add_system('Reviewed compaction discarded; capability changed, no replay')
                return False
            self._compact_command(target['instructions'], extension_generation=target['compaction_generation'])
        elif target['action'] == 'rename':
            if not self._extension_name_supported:
                self._chat_log.add_system('Reviewed name discarded; extension naming support withdrawn')
                return False
            self._name_session('', selected_name=target['name'], expected_name=target['expected_name'])
        else:
            if not self._extension_navigation_supported:
                self._chat_log.add_system('Reviewed navigation discarded; extension navigation support withdrawn')
                return False
            self._navigate_session(target["action"], target["session_id"], target["branch_id"], target["index"])
        return True

    def _serve_session_request(self, item: dict[str, Any]) -> None:
        value: Any = None
        pending = self._extension_pending
        owner = pending[0] if pending is not None else ""
        allowed = (not item.get("child_id") and pending is not None and self._active_run_id is None
                   and self._extension_editor_owner == (owner, str(item.get("extension", "")).lower())
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
            elif item['operation'] == 'browse':
                self._extension_browser = owner, self._mcp_context_key(), item["scope"]
                value = True
        self._answer_extension_dialog(item["id"], value)

    def _finish_extension_browser(self, owner: str, success: bool) -> None:
        queued, self._extension_browser = self._extension_browser, None
        if (queued is not None and queued[:2] == (owner, self._mcp_context_key())
                and success and not self._exit_requested):
            scope = "sessions" if queued[2] == "sessions" else "tree|" + self._command_session_id() + "|"
            self._open_navigation(scope)

    def _command_session_id(self) -> str:
        return self._gateway_session_id

    def _serve_editor_request(self, item: dict[str, Any]) -> None:
        pending = self._extension_pending
        allowed = (not item.get("child_id") and pending is not None
                   and self._extension_editor_owner == (pending[0], str(item.get("extension", "")).lower())
                   and self._active_run_id is None
                   and self._connection_ready and self._extension_editor_supported and not self._exit_requested)
        owner = self._mcp_context_key() + ":" + (pending[0] if pending is not None else "")
        value = None
        if allowed:
            try:
                value = self._editor_actions.handle(owner, item)
            except ValueError:
                value = None
        self._answer_extension_dialog(item["id"], value)

    def _sync_extension_dialog(self) -> None:
        selected = self._extension_stack.choose(self._extension_dialog_queue)
        if (selected is not None and selected.get('kind') == 'session'
                and selected.get('operation') == 'commands' and 'command_menu' in selected):
            # Read-only client metadata is serviced without unfocusing its
            # suspended custom parent. It is not a new modal input owner.
            self._serve_session_request(selected)
            return
        if selected is None:
            self._extension_dialog_panel = None
        elif self._extension_dialog_panel is None or self._extension_dialog_panel.dialog["id"] != selected["id"]:
            previous = self._extension_dialog_panel
            if previous is not None:
                previous.focused = False
            cached = self._extension_stack.panels.get(selected["id"])
            self._extension_dialog_panel = cached if isinstance(
                cached, (ExtensionDialog, ExtensionNavigation, ExtensionCustom)) else None
        if self._extension_dialog_panel is None and selected is not None:
            item = selected
            if (item["kind"] in {"editor", "session"}
                    and item.get("operation") not in {"navigate", "rename", "select_tools", "compact",
                                                      "message", "propose", "queue_input", "context_message"}):
                if self._outputs.panel is not None or self._custom_presentation.blocked():
                    return
                if item["kind"] == "session":
                    self._serve_session_request(item)
                else:
                    self._serve_editor_request(item)
                return

            if item.get('name_after_run') is True and (not self._extension_name_after_run_supported
                    or item.get('child_id') or self._active_run_id is None
                    or item.get('name_after_run_generation') != self._extension_name_after_run_generation):
                self._answer_extension_dialog(item['id'], False)
                return
            if item.get('operation') == 'compact' and item.get('after_run') is True and (
                    not self._extension_compaction_after_run_supported or item.get('child_id')
                    or self._active_run_id is None
                    or item.get('compaction_after_run_generation') != self._extension_compaction_after_run_generation):
                self._answer_extension_dialog(item['id'], False)
                return
            if item.get('operation') == 'context_message' and (
                    not self._extension_context_messages_supported or item.get('child_id')
                    or item.get('_context_message_generation') != self._extension_context_messages_generation):
                self._answer_extension_dialog(item['id'], False)
                return
            if item.get('operation') == 'queue_input' and not self._queued_input_review_current(item):
                self._answer_extension_dialog(item['id'], False)
                return
            if item.get('operation') == 'propose' and not self._prepare_editor_write(item):
                self._answer_extension_dialog(item['id'], False)
                return
            if item.get('operation') == 'message':
                from xdog.agent.extensions.message_request import host_message
                try:
                    if (not self._extension_message_supported or item.get('child_id')
                            or self._extension_pending is None
                            or item.get('host_message') != host_message(item['extension'], item['text'],
                                image_only=bool(item.get('image_summaries')))
                            or item.get('image_summaries') and (not self._extension_message_images_supported
                                or type(item.get('message_images_generation')) is not int
                                or item['message_images_generation'] != self._extension_message_images_generation)
                            or type(item.get('message_generation')) is not int
                            or item['message_generation'] != self._extension_message_generation):
                        raise ValueError('Requested message owner/capability changed')
                except (ValueError, KeyError, TypeError):
                    self._answer_extension_dialog(item['id'], False)
                    return
            if item.get('operation') == 'select_tools':
                from xdog.agent.extensions.active_tools import answer as tool_snapshot
                from xdog.agent.extensions.active_tools import names
                try:
                    current = tool_snapshot(item['current_tools'])
                    selected_tools = names(item['names'])
                    if ((not self._after_run_tools_review_current(item) if item.get('after_run') is True
                            else not self._extension_active_tools_supported or item.get('child_id')
                                 or self._extension_pending is None)
                            or current['session_id'] != self._gateway_session_id
                            or current['revision'] != item['revision']
                            or selected_tools is not None and not set(selected_tools) <= set(current['configured'])):
                        raise ValueError('Active tool selection owner/capability changed')
                except (ValueError, KeyError, TypeError):
                    self._answer_extension_dialog(item['id'], False)
                    return
            if (('provider_auth_host' in item or 'provider_auth_menu' in item)
                    and not self._provider_auth_review_current(item)):
                self._answer_extension_dialog(item['id'], None)
                return
            def answer(value: Any, key: str = item["id"]) -> None:
                self._answer_extension_dialog(key, value)
            label = f"Child {item['child_id']} · {item['extension']}" if item.get("child_id") else item["extension"]
            if "provider_auth_host" in item:
                from xdog.tui.components.extension_provider_auth import ExtensionProviderAuth
                self._extension_dialog_panel = ExtensionProviderAuth(
                    label, item, answer, open_url=self._open_provider_auth_url)
            elif item["kind"] == "custom":
                def request(event: dict[str, Any]) -> bool:
                    return self._request_extension_custom(item, event)
                self._extension_dialog_panel = ExtensionCustom(label, item, answer, request)
            elif item.get('operation') == 'context_message':
                from xdog.tui.components.extension_context_message import ExtensionContextMessage
                self._extension_dialog_panel = ExtensionContextMessage(
                    label, item, answer, self._command_session_id())
            elif item.get('operation') == 'queue_input':
                from xdog.tui.components.extension_queued_input import ExtensionQueuedInput
                projected = {key: value for key, value in item.items()
                             if key not in {'session_id', '_queue_generation', '_queue_run', 'child_id', 'child_owner',
                                            '_child_queue_generation'}}
                self._extension_dialog_panel = ExtensionQueuedInput(
                    item["extension"], projected, answer, self._command_session_id()
                    + (" / child " + item["child_id"] if item.get("child_id") else ""))
            elif item.get('operation') == 'propose':
                from xdog.tui.components.extension_editor_write import ExtensionEditorWrite
                self._extension_dialog_panel = ExtensionEditorWrite(label, item, answer)
            elif item.get("operation") in {"navigate", "rename", "select_tools", "compact", "message"}:
                self._extension_dialog_panel = ExtensionNavigation(label, item, answer, self._command_session_id())
            else:
                self._extension_dialog_panel = ExtensionDialog(label, item, answer)
            self._extension_stack.panels[item["id"]] = self._extension_dialog_panel
        if self._outputs.panel is None and not self._custom_presentation.blocked():
            if self._extension_dialog_panel is not None:
                self._leave_child_fullscreen()
                self._leave_transcript_fullscreen()
                if self._extension_dialog_panel.dialog.get('overlay') is not None:
                    self._layout.details = None
                    if (isinstance(self._tui._focused, (ExtensionDialog, ExtensionNavigation, ExtensionCustom))
                            and self._tui._focused is not self._extension_dialog_panel):
                        self._tui.set_focus(self._child_panel or self._plan_panel or self._mcp_panel
                                            or self._transcript_panel or self._editor)
                else:
                    self._layout.details = self._extension_dialog_panel
                    self._tui.set_focus(self._extension_dialog_panel)
            elif (isinstance(self._layout.details, (ExtensionDialog, ExtensionNavigation, ExtensionCustom))
                  or self._custom_presentation.overlay_panel is not None):
                self._layout.details = (self._extension_dialog_panel or self._child_panel or self._plan_panel
                    or self._mcp_panel
                or self._transcript_panel or (
                    self._details_panel if self._details_open else None))
                self._tui.set_focus((self._extension_dialog_panel if self._extension_dialog_panel is not None
                                    and self._extension_dialog_panel.dialog.get('overlay') is None else None)
                                    or self._child_panel or self._plan_panel
                    or self._mcp_panel
                                    or self._transcript_panel or self._editor)

        custom = self._extension_dialog_panel
        self._custom_presentation.sync(custom if isinstance(custom, ExtensionCustom) else None,
            self._outputs.panel is None and not self._custom_presentation.blocked())

    def _request_extension_custom(self, item: dict[str, Any], event: dict[str, Any]) -> bool:
        panel = self._extension_dialog_panel
        if ((item.get("fullscreen") and (not isinstance(panel, ExtensionCustom) or not panel.fullscreen)
                or item.get("overlay") is not None and (not isinstance(panel, ExtensionCustom) or not panel.overlay))
                and not (event.get("kind") == "resize" and event.get("height") == 0)):
            return False
        if (not self._connection_ready or not self._extension_custom_supported
                or item.get("session_id", self._gateway_session_id) != self._gateway_session_id
                or not any(row["id"] == item["id"] for row in self._extension_dialog_queue)):
            return False
        request = {"type": "extension_custom_request", "id": item["id"], "event": event,
                   "group_id": self.group_id, "session_id": self._gateway_session_id}
        if item.get("child_id"):
            request.update(type="child_extension_custom_request", child_id=item["child_id"],
                           child_owner=item["child_owner"])
        self._queue_request(request)
        return True

    def _after_run_tools_review_current(self, item: dict[str, Any]) -> bool:
        return (self._connection_ready and not self._exit_requested and self._active_run_id is not None
            and self._extension_tools_after_run_supported and not item.get('child_id')
            and item.get('session_id', self._gateway_session_id) == self._gateway_session_id
            and item.get('active_tools_after_run_generation') == self._extension_tools_after_run_generation)

    def _answer_extension_dialog(self, key: str, value: Any) -> None:
        if not any(item["id"] == key for item in self._extension_dialog_queue):
            return
        item = next(item for item in self._extension_dialog_queue if item["id"] == key)
        if (("provider_auth_host" in item or "provider_auth_menu" in item)
                and not self._provider_auth_review_current(item)):
            value = None
        if item.get("operation") == "queue_input":
            value = value is True and self._queued_input_review_current(item)
        if item.get("operation") == "propose":
            value = self._editor_write_reviews.answer(item, value is True)
        if item is not None and item.get('operation') == 'select_tools':
            if item.get('after_run') is True:
                value = value is True and self._after_run_tools_review_current(item)
            elif (not self._extension_active_tools_supported or item.get('child_id')
                    or self._extension_pending is None or not self._connection_ready or self._exit_requested
                    or self._extension_editor_owner != (self._extension_pending[0],
                                                        str(item.get('extension', '')).lower())):
                value = False
        if (item is not None and item.get("operation") in {"navigate", "rename", "compact", "message"}
                and not (item.get("operation") == "compact" and item.get("after_run") is True)
                and not item.get("name_after_run")):
            value = self._queue_extension_navigation(item, value)
        elif item.get("operation") == "compact" and item.get("after_run") is True:
            value = (value is True and self._extension_compaction_after_run_supported and not item.get("child_id")
                and self._active_run_id is not None and self._connection_ready
                and item.get("compaction_after_run_generation") == self._extension_compaction_after_run_generation)

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
        identifier = "extensions-" + uuid.uuid4().hex
        self._extension_pending = identifier, self._gateway_session_id
        self._extension_auth_owner = ((identifier, self._mcp_context_key(), self._extension_provider_auth_generation)
                                      if action == "auth" else None)
        self._extension_cancel_scope = identifier, self._gateway_session_id
        self._extension_editor_owner = (identifier, command.split("-", 2)[1].lower()) if action == "command" else None
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
            self._extension_navigation = None
            self._extension_navigation_review = None
            self._extension_editor_owner = None
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
        if self._child_panel is not None:
            self._close_children()
        if self._exit_requested or self._tui.has_overlay():
            return
        if self._plan_panel is not None:
            self._close_plan()
        if self._mcp_panel is not None:
            return
        if self._transcript_panel is not None:
            self._close_transcript()
        if self._navigation_panel is not None:
            self._close_navigation()
        if self._review_panel is not None:
            self._close_review()
        if self._attachment_panel is not None:
            self._close_attachment_preview()
        if self._details_open:
            self._close_details()
        self._mcp_document = None
        self._mcp_owner_context = self._mcp_context_key()
        self._mcp_watch_view = uuid.uuid4().hex
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
                and self._mcp_action in {"resources", "templates", "prompts", "read", "prompt",
                                         "complete_prompt", "complete_template", "complete_resource"}):
            self._queue_request({"type": "mcp_context_cancel", "request_id": self._mcp_active})

    def _detach_mcp_auth(self) -> None:
        if self._connection_ready and self._mcp_elicitation_supported:
            for server in tuple(self._mcp_elicitation_servers):
                self._queue_request({'type': 'mcp_elicitation_control', 'action': 'detach', 'server': server,
                    'request_id': 'elicitation-detach-' + uuid.uuid4().hex, 'group_id': self._mcp_auth_group,
                    'session_id': self._mcp_auth_session, 'view': self._mcp_auth_lease})
        self._mcp_elicitation_servers.clear()
        self._mcp_elicitation_requests.clear()
        if self._connection_ready and self._mcp_sampling_supported:
            for server in tuple(self._mcp_sampling_servers):
                self._queue_request({'type': 'mcp_sampling_control', 'action': 'detach', 'server': server,
                    'request_id': 'sampling-detach-' + uuid.uuid4().hex, 'group_id': self._mcp_auth_group,
                    'session_id': self._mcp_auth_session, 'view': self._mcp_auth_lease})
        self._mcp_sampling_servers.clear()
        self._mcp_sampling_requests.clear()
        if self._mcp_auth_lease and self._connection_ready and self._mcp_roots_supported:
            self._queue_request({'type': 'mcp_roots_control', 'action': 'detach', 'group_id': self._mcp_auth_group,
                                 'session_id': self._mcp_auth_session, 'view': self._mcp_auth_lease,
                                 'request_id': 'roots-detach-' + uuid.uuid4().hex})
        self._mcp_root_requests.clear()
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
        view, self._mcp_watch_view = self._mcp_watch_view, ""
        if view and self._mcp_watch_supported:
            self._queue_request({"type": "mcp_resource_watch", "action": "close", "view": view,
                "request_id": "watch-close-" + uuid.uuid4().hex, "group_id": self.group_id,
                "session_id": self._gateway_session_id})
        self._mcp_watch_requests.clear()
        self._detach_mcp_auth()
        self._cancel_mcp_data()
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
        panel = self._mcp_panel
        if panel is None:
            return
        if self._mcp_owner_context != self._mcp_context_key():
            self._close_mcp()
            return
        from dataclasses import asdict
        if action.startswith("auth_"):
            try:
                if not isinstance(data, dict) and not error:
                    raise ValueError("Invalid authorization response")
                if action.startswith("auth_credentials_") and action.endswith("_prepare") and not error:
                    expected = action.removeprefix("auth_").removesuffix("_prepare")
                    if data.get("operation") != expected or not isinstance(data.get("storage_notice"), str):
                        raise ValueError("Missing explicit credential storage consent")
                    self._mcp_auth_review = validate_review(data)
                    data = self._mcp_auth_review
                elif action == "auth_prepare" and not error:
                    if (data.get("registration", "pre-registered") != "pre-registered"
                            and not self._mcp_registration_supported):
                        raise ValueError("Updated registration gateway required; strategy was not silently ignored")
                    if data.get("token_auth_method", "none") != "none" and not self._mcp_client_auth_supported:
                        raise ValueError("Updated client-auth gateway required; method was not silently ignored")
                    if data.get('token_auth_method') == 'private_key_jwt' and not self._mcp_private_jwt_supported:
                        raise ValueError('Updated private-key JWT gateway required; no method downgrade')
                    self._mcp_auth_review = validate_review(data, prepared=True)
                    data = self._mcp_auth_review
                elif action == "auth_refresh_prepare" and not error:
                    if data.get("operation") != "refresh":
                        raise ValueError("Missing explicit refresh operation")
                    self._mcp_auth_review = validate_review(data)
                    data = self._mcp_auth_review
                elif action == "auth_status" and not error and isinstance(data.get("review"), dict):
                    data = {**data, "review": validate_review(data["review"])}
                elif action == "auth_start" and not error:
                    if self._mcp_auth_review is None:
                        raise ValueError("Missing reviewed authorization scope")
                    data = validate_launch(data, self._mcp_auth_review)
                panel.publish_auth(action, data if isinstance(data, dict) else {}, error)
            except ValueError as exc:
                self._detach_mcp_auth()
                panel.publish_auth(action, {}, str(exc))
            self._tui.request_render()
            return
        if action.startswith(('sampling_', 'elicitation_')):
            panel._busy = False
            details = data if isinstance(data, dict) else {}
            panel._message = error or str(details.get('notice', 'Sampling control settled'))
            self._tui.request_render()
            return
        if action.startswith('roots_'):
            try:
                panel.publish_roots(action, data if isinstance(data, dict) else {}, error)
            except ValueError as exc:
                panel.publish_roots(action, {}, str(exc))
            self._tui.request_render()
            return
        if error:
            panel.publish(action, error=error)
        elif isinstance(data, McpCompletion):
            panel.publish(action, rows=(asdict(data),))
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
        if action == 'elicitation_open':
            panel = self._mcp_panel
            review = panel.elicitation_review if panel is not None else None
            context = self._mcp_context_key()
            generation, view = self._connection_generation, self._mcp_auth_lease
            def url_alive() -> bool:
                return (self._mcp_elicitation_supported and self._connection_ready
                        and server in self._mcp_elicitation_servers and self._connection_generation == generation
                        and self._mcp_auth_lease == view and self._active_run_id is None and not self._waiting
                        and self._extension_dialog_panel is None and not self._extension_dialog_queue
                        and self._child_panel is None and not self._tui.has_overlay()
                        and self._tui._focused is panel and self._outputs.panel is None
                        and not self._exit_requested and self._mcp_panel is panel and panel is not None
                        and panel.elicitation_review is review and review is not None
                        and review.focused and not review._disabled and not review._used
                        and review._review._visible and self._mcp_context_key() == context
                        and review.event['body'].get('params', {}).get('url') == name)
            if not url_alive():
                return
            try:
                self._reviewed_url.start(name, url_alive)
            except ValueError:
                self._chat_log.add_system('URL opening unavailable/busy; no repeat admission')
            return  # URL opening is local explicit interaction, not MCP answer or completion.
        if action in {'sampling_on', 'sampling_tasks_on', 'elicitation_on', 'elicitation_tasks_on'}:
            if not self._gateway_session_id:
                if self._mcp_panel is not None:
                    self._mcp_panel._busy = False
                    self._mcp_panel._message = ('An authoritative idle session is required; '
                                                'reopen MCP on an updated gateway')
                return  # Never arm against an empty owner, including older gateways.
        if action.startswith('elicitation_'):
            panel = self._mcp_panel
            if not self._mcp_elicitation_supported or not self._connection_ready or panel is None:
                if panel is not None:
                    panel._busy = False
                    panel._message = 'Elicitation gateway support unavailable; no request sent'
                return
            operation = {'elicitation_on': 'enable', 'elicitation_tasks_on': 'enable', 'elicitation_off': 'detach',
                         'elicitation_answer': 'answer'}.get(action)
            if operation is None or len(self._mcp_elicitation_requests) >= 16 and operation != 'detach':
                panel._busy = False
                panel._message = 'Elicitation control unavailable/busy; no replay'
                return
            identifier = 'elicitation-' + uuid.uuid4().hex
            self._mcp_elicitation_requests[identifier] = action
            if operation == 'enable':
                self._mcp_elicitation_servers.add(server)
            elif operation == 'detach':
                self._mcp_elicitation_servers.discard(server)
            response = json.loads(name) if operation == 'answer' else None
            self._queue_request({'type': 'mcp_elicitation_control', 'request_id': identifier,
                'group_id': self.group_id, 'session_id': self._gateway_session_id, 'view': self._mcp_auth_lease,
                'server': server, 'action': operation,
                **({'tasks': True} if action.endswith('_tasks_on') else {}),
                **({'response': response} if operation == 'answer' else {})})
            return
        if action.startswith('sampling_'):
            panel = self._mcp_panel
            if not self._mcp_sampling_supported or not self._connection_ready or panel is None:
                if panel is not None:
                    panel._busy = False
                    panel._message = 'Sampling gateway support unavailable; no request sent'
                return
            operation = {'sampling_on': 'enable', 'sampling_tasks_on': 'enable',
                         'sampling_off': 'detach', 'sampling_answer': 'answer'}.get(action)
            if operation is None or len(self._mcp_sampling_requests) >= 16 and operation != 'detach':
                panel._busy = False
                panel._message = 'Sampling control unavailable/busy; no replay'
                return
            identifier = 'sampling-' + uuid.uuid4().hex
            self._mcp_sampling_requests[identifier] = action
            if operation == 'enable':
                self._mcp_sampling_servers.add(server)
            elif operation == 'detach':
                self._mcp_sampling_servers.discard(server)
            response = json.loads(name) if operation == 'answer' else None
            self._queue_request({'type': 'mcp_sampling_control', 'request_id': identifier,
                'group_id': self.group_id, 'session_id': self._gateway_session_id, 'view': self._mcp_auth_lease,
                'server': server, 'action': operation,
                **({'tasks': True} if action.endswith('_tasks_on') else {}),
                **({'response': response} if operation == 'answer' else {})})
            return
        if action.startswith('roots_'):
            if not self._mcp_roots_supported or not self._connection_ready or not self._mcp_panel:
                if self._mcp_panel is not None:
                    self._mcp_panel.publish_roots(action, {},
                                                 'Dynamic roots gateway support unavailable; no request sent')
                return
            identifier = 'roots-' + uuid.uuid4().hex
            self._mcp_active, self._mcp_action = identifier, action
            self._mcp_root_requests.add(identifier)
            self._queue_request({'type': 'mcp_roots_control', 'request_id': identifier,
                'group_id': self.group_id, 'session_id': self._gateway_session_id, 'view': self._mcp_auth_lease,
                'server': server, 'action': action.removeprefix('roots_'),
                'review_id': name if action == 'roots_commit' else '',
                **({'roots': json.loads(name)} if action == 'roots_prepare' else {})})
            return
        if action in {"watch_resource", "unwatch_resource", "watch_catalog", "unwatch_catalog",
                      "watch_notifications", "unwatch_notifications"}:
            if (not self._mcp_watch_supported or not self._connection_ready or not self._mcp_watch_view
                    or action.endswith('catalog') and not self._mcp_catalog_watch_supported
                    or action.endswith('notifications') and not self._mcp_combined_watch_supported):
                if self._mcp_panel is not None:
                    self._mcp_panel.publish_watch({}, "Updated resource-watch gateway required", receipt=True)
                return
            rid = "watch-" + uuid.uuid4().hex
            self._mcp_watch_requests.add(rid)
            watch_action = "watch" if action.startswith("watch_") else "unwatch"
            self._queue_request({"type": "mcp_resource_watch", "action": watch_action,
                                 "view": self._mcp_watch_view, "request_id": rid,
                                 "group_id": self.group_id, "session_id": self._gateway_session_id,
                                 "server": server, "uri": '' if action.endswith(('catalog', 'notifications')) else name,
                                 "catalog": name if action.endswith('catalog') else '',
                                 **({'notifications': json.loads(name)} if action.endswith('notifications') else {})})
            return
        if action == "completion_cancel":
            self._cancel_mcp_data()
            self._mcp_active = None
            return
        if action == "auth_leave":
            self._detach_mcp_auth()
            self._mcp_auth_lease = uuid.uuid4().hex
            return
        self._cancel_mcp_data()
        identifier = "view-" + uuid.uuid4().hex
        self._mcp_active, self._mcp_action = identifier, action
        if action == "resource_completion_preview":
            try:
                self._mcp_publish(action, resource_completion_document(server, name, arguments))
            except (ValueError, RuntimeError) as exc:
                self._mcp_publish(action, error=str(exc))
            return  # Local operator-edited data; no IPC/remote read/model/tool request.
        if not self._connection_ready or not self._mcp_supported:
            self._recv_queue.put({"type": "mcp_view_failed", "id": identifier,
                                  "error": "Ready updated gateway required"})
            return
        if action == "complete_resource" and not self._mcp_resource_completion_supported:
            self._recv_queue.put({"type": "mcp_view_failed", "id": identifier,
                                  "error": "Updated opaque-resource-completion gateway required"})
            return
        if (action in {"complete_prompt", "complete_template", "complete_resource"}
                and not self._mcp_completion_supported):
            self._recv_queue.put({"type": "mcp_view_failed", "id": identifier,
                                  "error": "Updated argument-completion gateway required"})
            return
        if action.startswith("auth_"):
            active_auth = self._mcp_panel.auth if self._mcp_panel is not None else None
            private_review = (self._mcp_auth_review or {}).get('token_auth_method') == 'private_key_jwt'
            private_review = private_review or (active_auth is not None
                and active_auth.review.get('token_auth_method') == 'private_key_jwt')
            if (private_review and not self._mcp_private_jwt_supported
                    and action not in {'auth_cancel', 'auth_status'}):
                self._recv_queue.put({'type': 'mcp_view_failed', 'id': identifier,
                                      'error': 'Private-key signing support withdrawn; no request sent'})
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
                                 "action": action.removeprefix("auth_"), "server": server, "review_id": name,
                                 "token_auth_method": (self._mcp_auth_review or {}).get("token_auth_method", "none"),
                                 "client_secret_env": (self._mcp_auth_review or {}).get("client_secret_env", "")})
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

    def _compact_command(self, instructions: str, *, extension_generation: int | None = None) -> None:
        if instructions.strip() == 'cancel' and extension_generation is None:
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
                             'session_id': self._gateway_session_id, 'instructions': instructions,
                             **({'extension_compaction_generation': extension_generation}
                                if extension_generation is not None else {})})
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
        if self._plan_panel is not None:
            self._close_plan()
        if self._transcript_panel is not None:
            if query:
                self._transcript_panel.search(query)
            return
        if self._attachment_panel is not None:
            self._close_attachment_preview()
        if self._navigation_panel is not None:
            self._close_navigation()
        if self._review_panel is not None:
            self._close_review()
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
            self._layout.details = (self._extension_dialog_panel or self._child_panel or self._plan_panel
                    or self._mcp_panel
                or self._transcript_panel or (
                self._details_panel if self._details_open else None))
            self._tui.set_focus((self._extension_dialog_panel if self._extension_dialog_panel is not None
                                    and self._extension_dialog_panel.dialog.get('overlay') is None else None)
                                    or self._child_panel or self._plan_panel
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
        if self._transcript_panel is not None:
            self._close_transcript()
        if self._tui.has_overlay():
            return
        if self._mcp_panel is not None:
            self._close_mcp()
        if self._plan_panel is not None:
            self._close_plan()
        if self._attachment_panel is not None:
            self._close_attachment_preview()
        if self._navigation_panel is not None:
            self._close_navigation()
        if self._review_panel is not None:
            self._close_review()
        if self._details_open:
            self._close_details()
        self._attachment_panel = AttachmentPanel(item, self._close_attachment_preview)
        self._layout.details = self._attachment_panel
        self._tui.set_focus(self._attachment_panel)
        self._update_hints()
        self._tui.request_render()

    def _close_attachment_preview(self) -> None:
        self._attachment_panel = None
        self._layout.details = (self._navigation_panel or self._review_panel
                                or (self._details_panel if self._details_open else None))
        self._tui.set_focus(self._navigation_panel or self._review_panel
                            or (self._details_panel if self._details_open else self._editor))
        self._update_hints()
        self._tui.request_render()

    def _open_navigation(self, scope: str = "sessions") -> None:
        if self._child_panel is not None:
            self._close_children()
        if self._transcript_panel is not None:
            self._close_transcript()
        if self._attachment_panel is not None:
            self._close_attachment_preview()
        if not self._connection_ready or not self._session_controls_supported:
            self._chat_log.add_system("Session browser requires a ready updated gateway")
            self._tui.request_render()
            return
        if self._tui.has_overlay():
            self._chat_log.add_system("Close the active dialog before session navigation")
            self._tui.request_render()
            return
        if self._mcp_panel is not None:
            self._close_mcp()
        if self._plan_panel is not None:
            self._close_plan()
        if self._navigation_panel is not None:
            self._close_navigation()
        if self._review_panel is not None:
            self._close_review()
        if self._details_open:
            self._close_details()
        self._navigation_provider = NavigationFeed(self._request_navigation)
        self._navigation_panel = NavigationPanel(self._navigation_provider, self._activate_navigation,
                                                 self._close_navigation, scope=scope,
                                                 on_new=lambda: self._navigate_session("new"),
                                                 on_view=self._view_navigation)
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
        self._layout.details = self._review_panel or (self._details_panel if self._details_open else None)
        self._tui.set_focus(self._review_panel or (self._details_panel if self._details_open else self._editor))
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

    def _activate_navigation(self, entry: NavigationEntry) -> None:
        action = "resume" if entry.kind == "session" else "branch" if entry.kind == "branch" else "goto"
        self._navigate_session(action, entry.session_id, entry.branch_id, entry.index)

    def _navigate_session(self, action: str, session_id: str = "", branch_id: str = "", index: int = -1) -> None:
        if (not self._connection_ready or not self._session_controls_supported or self._active_run_id is not None
                or self._pending_command is not None):
            self._chat_log.add_system("Session changes require a ready idle group; selection was not sent")
            self._tui.request_render()
            return
        request_id = uuid.uuid4().hex
        self._navigation_requests[request_id] = (None, 0, "mutate")
        self._session_mutation_id = request_id
        self._pending_command = "session:" + request_id
        self._queue_request({"type": "session_control", "request_id": request_id, "group_id": self.group_id,
                             "action": action, "session_id": session_id, "branch_id": branch_id, "index": index,
                             "expected_session": self._gateway_session_id})
        self._set_waiting(True)
        self._tui.request_render()

    def _name_session(self, args: str, *, selected_name: str | None = None,
                      expected_name: str | None = None) -> None:
        if not args and selected_name is None:
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
            name = session_name(selected_name if selected_name is not None else '' if args.strip() == '-' else args)
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
                             'action': 'rename', 'expected_session': self._gateway_session_id, 'name': name,
                             **({'expected_name': expected_name} if expected_name is not None else {})})
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
                self._chat_log.add_system('Updated navigation-cancel gateway required; inspect before retrying')
                self._tui.request_render()
                return
            self._queue_request({'type': 'session_control_cancel', 'request_id': self._session_mutation_id,
                                 'group_id': self.group_id, 'expected_session': self._gateway_session_id})
            self._set_activity_status('canceling navigation · awaiting settlement')
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
                            self._chat_log.add_system("Gateway session changed; select/resume to load its context")
                            self._gateway_session_id = actual
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
                self._chat_log.add_system("Navigation committed while cancellation settled; synchronizing context"
                    if msg.get('receipt_canceled') is True else "Session operation committed; synchronizing context")
        else:
            if request_id != self._session_mutation_id:
                return
            self._close_navigation()
            if self._review_panel is not None:
                self._close_review()
            self._output_epoch += 1
            self._close_transcript()
            self._chat_log.clear_all()
            self._editor.reset_history()
            self._banner_added = False
            self._state["session_id"] = str(msg.get("session_id", ""))
            self._state["session_name"] = msg.get("session_name", "")
            self._gateway_session_id = self._state["session_id"]
            self._extension_snapshot = {**self._extension_snapshot,
                                        "terminal_input": False, "terminal_generation": 0}
            self._terminal_children = {}
            self._child_terminal_owner = ""
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

    def _open_review(self) -> None:
        if self._child_panel is not None:
            self._close_children()
        if self._transcript_panel is not None:
            self._close_transcript()
        if self._attachment_panel is not None:
            self._close_attachment_preview()
        if self._exit_requested:
            return
        if self._file_completions is None:
            self._chat_log.add_system("Repository review needs gateway workspace metadata; no guessed cwd")
            self._tui.request_render()
            return
        if self._tui.has_overlay():
            self._chat_log.add_system("Close the active dialog before opening repository review")
            self._tui.request_render()
            return
        if self._mcp_panel is not None:
            self._close_mcp()
        if self._plan_panel is not None:
            self._close_plan()
        if self._navigation_panel is not None:
            self._close_navigation()
        if self._review_panel is not None:
            self._review_backend.refresh() if self._review_backend is not None else None
            return
        if self._details_open:
            self._close_details()
        self._review_backend = RepositoryReview(self._file_completions.root)
        self._review_panel = ReviewPanel(self._review_backend, self._close_review)
        self._layout.details = self._review_panel
        self._tui.set_focus(self._review_panel)
        self._update_hints()
        self._tui.request_render()

    def _close_review(self) -> None:
        if self._review_backend is not None:
            self._review_backend.close()
        self._review_backend = None
        self._review_panel = None
        self._layout.details = self._details_panel if self._details_open else None
        self._tui.set_focus(self._details_panel if self._details_open else self._editor)
        self._update_hints()
        self._tui.request_render()

    def _close_details(self) -> None:
        self._details_open = False
        self._details_expanded = False
        self._layout.details = None
        self._tui.set_focus(self._editor)
        self._editor.set_focus(True)
        self._update_hints()
        self._tui.request_render()

    def _update_hints(self) -> None:
        if self._remote_parent_panel is not None:
            hint = 'Remote parent draft · full review before copy · Esc closes · NOT submission'
        elif self._outputs.panel is not None:
            hint = "Output review · Tab path · PgUp/PgDn preview · Enter saves locally · Esc cancels"
        elif (isinstance(self._extension_dialog_panel, ExtensionCustom) and self._extension_dialog_panel.overlay
              and not self._extension_dialog_panel.focused):
            hint = "Background overlay · parent draft editable · Esc/Ctrl+C cancel view · NOT tool approval"
        elif self._extension_dialog_panel is not None:
            hint = "Extension dialog · Enter answers · Esc cancels · NOT tool approval"
        elif self._child_panel is not None:
            hint = "Child browser · Enter inspect · n new · c cancel · / search · Esc parent draft"
        elif self._plan_panel is not None:
            hint = "Plan focused · arrows/scroll · e execution · r revise · s stop · Esc close"
        elif self._mcp_panel is not None:
            hint = "MCP focused · c/d/r servers · u/t/p catalogs · Enter preview · i draft · e export · Esc back"
        elif isinstance(self._transcript_panel, ToolInventoryPanel):
            hint = "Tools · / schema search · n/N match · f screen · r refresh · Esc close · NOT approval"
        elif self._transcript_panel is not None:
            hint = "Transcript · / search · n/N match · arrows/pages · v/a select · f fullscreen · Esc close"
        elif self._attachment_panel is not None:
            hint = "Image preview · Esc return to draft · /attachments remove/clear"
        elif self._navigation_panel is not None:
            hint = "Sessions/tree · ↑/↓ select · →/← tree/back · / search · v read-only · Enter activate · Esc close"
        elif self._review_panel is not None:
            hint = "Review · ↑/↓ file · n/p hunk · PgUp/PgDn · s/w lane · / filter · r refresh · Esc close"
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
        if self._editor.focused and self._plan_snapshot.mode != "off":
            hint = ("READ-ONLY plan · " if self._plan_snapshot.mode == "planning" else "EXECUTING plan · ") + hint
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
        panel = self._extension_dialog_panel
        if (isinstance(panel, ExtensionCustom) and panel.dialog.get('overlay') is not None
                and panel.dialog.get('live_overlay') and not panel.focused):
            self._answer_extension_dialog(panel.dialog['id'], None)
            return  # Cancel this owned background view, not unrelated parent/child work.
        if self._attachment_panel is not None:
            self._close_attachment_preview()
            return
        if self._navigation_panel is not None:
            self._close_navigation()
            return
        if self._review_panel is not None:
            self._close_review()
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
        self._queue_request({
            "type": "abort",
            "group_id": self._state.get("group_id", "main"),
            "run_id": run_id,
        })
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
        self._retire_extension_inputs(run_id)
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
        return (self._child_draft_io.failures, self._child_autosave.io.failures,
                self._parent_drafts.io.failures, self._parent_drafts.autosave.io.failures)

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
        self._poll_child_autosave(flush=True)
        self._parent_drafts.poll(self._show_parent_drafts, flush=True)
        if self._remote_parent_worker is not None and self._remote_parent_worker.is_alive():
            self._close_remote_parent()
            if self._draft_quit_failures is None:
                self._draft_quit_failures = self._draft_write_failures()
                self._chat_log.add_system('Remote parent decode cleanup settling before quit; no late adoption')
            return
        if self._child_draft_io.busy or self._child_autosave.busy or self._parent_drafts.busy:
            if self._draft_quit_failures is None:
                self._draft_quit_failures = self._draft_write_failures()
                self._chat_log.add_system('Saving pending drafts before quitting; storage failure keeps the UI open')
            return
        self._draft_quit_failures = None
        self._close_remote_parent()
        self._exit_requested = True
        self._argument_feed.close()
        self._cancel_name_read(send=False)
        self._custom_presentation.sync(None, False)
        self._terminal_input.close()
        self._editor_actions.invalidate()
        if self._child_panel is not None:
            self._close_children()
        self._composer.close()
        self._extension_dialog_queue.clear()
        self._extension_stack.clear()
        self._extension_dialog_panel = None
        if self._plan_panel is not None:
            self._close_plan()
        if self._mcp_panel is not None:
            self._close_mcp()
        self._outputs.close()
        if self._transcript_panel is not None:
            self._close_transcript()
        if self._navigation_provider is not None:
            self._navigation_provider.close()
        if self._review_backend is not None:
            self._review_backend.close()
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
        self._retire_extension_inputs(owner)
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
                self._extension_queued_inputs.pop(input_id, None)
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
            extension_input = self._extension_queued_inputs.pop(input_id, None)
            images = self._routed_images.pop(input_id, ())
            if kind == "input_consumed":
                content = self._transformed_inputs.pop(input_id, content)
                self._chat_log.close_assistant(item.owner)
                image_count = extension_input[1] if extension_input is not None else len(images)
                self._chat_log.add_user(content + (f"\n[Images: {image_count}]" if image_count else ""))
                if extension_input is None:
                    self._editor.add_to_history(content)
            elif extension_input is not None:
                self._transformed_inputs.pop(input_id, None)
                self._chat_log.add_system('Extension input returned without consumption; draft kept, not replayed')
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
            if command == "/terminal-input":
                self._terminal_input.recovery_command(args, self._restore_terminal_text)
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
            if command == "/extensions" or command.startswith("/ext-"):
                self._extension_command(command[1:], args)
                return
            if command == '/draft':
                self._parent_draft_command(args)
                return
            if command == "/agents":
                self._agents_command(args)
                self._tui.request_render()
                return
            if command == "/plan":
                self._plan_command(args)
                self._tui.request_render()
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
            if command in {"/resume", "/new", "/fork", "/branch", "/goto"}:
                try:
                    values = args.split()
                    active = str(self._state.get("session_id", ""))
                    if command == "/resume" and len(values) == 1:
                        self._navigate_session("resume", values[0])
                    elif command == "/new" and not values:
                        self._navigate_session("new")
                    elif command == "/fork":
                        self._navigate_session("fork", active, index=int(values[0]) - 1 if values else -1)
                    elif command == "/branch" and len(values) in {1, 2}:
                        self._navigate_session("branch", values[0] if len(values) == 2 else active, values[-1])
                    elif command == "/goto" and len(values) == 1:
                        self._navigate_session("goto", active, index=int(values[0]) - 1)
                    else:
                        raise ValueError(
                            "Usage: /resume ID · /new · /fork [INDEX] · /branch [SESSION] ID · /goto INDEX"
                        )
                except ValueError as exc:
                    self._chat_log.add_system(str(exc))
                    self._tui.request_render()
                return
            if command == "/diff":
                if args.strip() == "close":
                    self._close_review()
                elif args.strip() in {"", "refresh"}:
                    self._open_review()
                else:
                    self._chat_log.add_system("Usage: /diff [refresh|close] — read-only repository changes")
                    self._tui.request_render()
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
            self._preserve_submission(value, "Session context changed; resume in /sessions before sending")
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

        if self._active_run_id is not None:
            if self._aborting_run_id == self._active_run_id:
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

    def _start_message(self, value: str, images: tuple[Attachment, ...], *,
                       requested_message: dict[str, Any] | None = None) -> None:
        """Canonical native turn transport, without slash parsing or composer capture."""
        self._clear_todos()  # clear any finalized todos from previous turn
        self._clear_goal()   # clear goal widget from previous turn
        self._active_images = images
        image_count = requested_message.get("image_count", 0) if requested_message is not None else len(images)
        if not (requested_message or {}).get('canonical_context'):
            self._chat_log.add_user(value + (f"\n[Images: {image_count}]" if image_count else ""))
        self._active_run_id = f"run-{uuid.uuid4().hex}"
        # Extension requests are never restored into the unsent user draft or replayed.
        self._active_text = value if requested_message is None else None
        self._aborting_run_id = None
        self._set_waiting(True)
        self._streaming_output_chars = 0
        # Estimate input for this turn: all prior session context + this message
        self._turn_input_chars = self._usage["input"] * 4 + len(value)
        self._queue_request({
            "type": "message",
            "group_id": self._state.get("group_id", "main"),
            **({'content': value} if requested_message is None else {
                **({'extension_context_token': requested_message['token'],
                    'extension_context_generation': requested_message['generation']}
                   if requested_message.get('canonical_context') else
                   {'extension_message_token': requested_message['token'],
                    'extension_message_generation': requested_message['generation']})}),
            "run_id": self._active_run_id,
            **({"session_id": self._gateway_session_id} if self._session_controls_supported else {}),
        }, images)

    def _dispatch_next_pending(self) -> None:
        if (
            self._exit_requested or not self._connection_ready or self._pending_command is not None
            or self._active_run_id is not None or not self._pending_messages
        ):
            return
        keys = self._pending_ids.sync(len(self._pending_messages))
        images = self._local_images.pop(keys[0], ())
        next_message = self._pending_messages.popleft()
        self._pending_ids.pop()
        self._update_queue_summary()
        self._handle_submit(next_message, queued=True, images=images)

    # ── Per-frame polling (matching OpenClaw's event loop) ──

    def _poll(self) -> None:
        self._editor_write_reviews.prune()
        if self._remote_parent_panel is not None and (not self._remote_parent_alive()
                or self._remote_parent_deadline and time.monotonic() >= self._remote_parent_deadline):
            self._close_remote_parent()
        if self._remote_parent_worker is None or not self._remote_parent_worker.is_alive():
            self._remote_parent_receiver.poll()
            if self._remote_parent_panel is None:
                self._remote_parent_receiver.close()
        self._child_draft_io.poll(self._finish_child_draft_io)
        self._poll_child_autosave()
        self._parent_drafts.poll(self._show_parent_drafts)
        if self._finish_deferred_draft_quit():
            return
        draft_states = (('parent', self._parent_drafts.autosave.durability),
                        ('child', self._child_autosave.durability))
        durability = ' · '.join(f'{kind} draft:{state}' for kind, state in draft_states
                                if state not in {'off', 'empty'})
        draft_changed = self._footer.set_durability(durability)
        changed = bool(self._recv_queue.drain(self._handle_response))
        changed = draft_changed or changed
        changed = self._terminal_input.poll() or changed
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
            if isinstance(self._extension_dialog_panel, ExtensionCustom):
                changed = self._extension_dialog_panel.poll() or changed
        if self._child_panel is not None:
            if self._child_context != self._mcp_context_key():
                self._close_children()
            else:
                if self._child_source_pending is not None and time.monotonic() >= self._child_source_deadline:
                    child = self._child_source_pending[1]
                    self._cancel_child_source()
                    self._child_panel.source(child, (), "Child source timed out; refresh explicitly")
                changed = self._child_panel.poll() or changed
        if self._plan_panel is not None and self._plan_context != self._mcp_context_key():
            self._close_plan()
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
        if self._review_panel is not None:
            changed = self._review_panel.poll() or changed

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
            if role == "user" and (content or entry.get("image_count")):
                if entry.get("image_count"):
                    content += f"\n[Images: {entry['image_count']}]"
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
            self._terminal_input.invalidate()
            self._terminal_supported = False
            self._terminal_owner = ""
            self._terminal_child_supported = False
            self._terminal_children = {}
            self._child_terminal_owner = ""
            self._close_remote_parent()
            self._remote_parent_caps = {}
            self._extension_active_tools_supported = False
            self._extension_tools_after_run_supported = False
            self._extension_tools_after_run_generation = 0
            self._extension_compaction_supported = False
            self._extension_compaction_generation = 0
            self._extension_compaction_after_run_supported = False
            self._extension_compaction_after_run_generation = 0
            self._extension_message_supported = False
            self._extension_message_generation = 0
            self._extension_message_images_supported = False
            self._extension_message_images_generation = 0
            self._extension_provider_auth_supported = False
            self._extension_provider_auth_generation = 0
            self._extension_auth_owner = None
            self._extension_queued_inputs_supported = False
            self._extension_queued_inputs_generation = 0
            self._child_queued_inputs_supported = False
            self._child_queued_inputs_generation = 0
            self._child_queued_inputs_owner = ""
            self._child_extension_inputs.clear()
            self._extension_editor_write_supported = False
            self._extension_editor_write_generation = 0
            self._extension_editor_write_owner = ''
            self._editor_write_reviews.clear()
            self._connection_generation = generation
            self._mcp_elicitation_supported = False
            self._mcp_elicitation_servers.clear()
            self._mcp_elicitation_requests.clear()
            if self._mcp_panel is not None and self._mcp_panel.elicitation_review is not None:
                self._mcp_panel.elicitation_review.disable('Elicitation connection changed; no replay')
            self._mcp_sampling_supported = False
            self._mcp_sampling_servers.clear()
            self._mcp_sampling_requests.clear()
            if self._mcp_panel is not None and self._mcp_panel.sampling_review is not None:
                self._mcp_panel.sampling_review.disable('Sampling connection changed; no replay')
            self._mcp_roots_supported = False
            self._mcp_root_requests.clear()
            if self._mcp_panel is not None and self._mcp_panel.roots_review is not None:
                self._mcp_panel.roots_review.invalidate()
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
            if self._plan_pending is not None:
                self._chat_log.add_system("Plan control receipt lost; inspect current mode before retrying")
            self._plan_pending = None
            self._child_presets = ChildPresets()
            self._child_presets_supported = False
            self._child_model_supported = False
            self._child_config_supported = False
            self._child_images_supported = False
            self._child_image_recovery_supported = False
            self._child_task_recovery_supported = False
            self._child_return_supported = False
            self._child_return_owner = ''
            self._child_queue_owners.clear()
            self._child_recovery_ready.clear()
            self._child_source_supported = False
            if self._child_panel is not None:
                self._close_children()
            self._children_supported = False
            self._child_queues_supported = False
            self._children_pending.clear()
            self._plan_supported = False
            if self._plan_panel is not None:
                self._close_plan()
            self._extension_navigation = None
            self._extension_navigation_review = None
            self._extension_editor_owner = None
            self._extension_pending = None
            self._extension_auth_owner = None
            self._extension_cancel_scope = None
            self._extension_cancel_supported = False
            self._extension_model_pending = None
            self._extension_models_supported = False
            self._native_models_supported = False
            self._reasoning_pending = None
            self._reasoning_supported = False
            self._custom_presentation.sync(None, False)
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
        if self._remote_parent_response(msg):
            return
        if msg_type in {"mcp_resource_watch_result", "mcp_resource_watch_notice"}:
            if (self._mcp_panel is None or msg.get("view") != self._mcp_watch_view
                    or msg.get("group_id") != self.group_id or msg.get("session_id") != self._gateway_session_id
                    or self._mcp_owner_context != self._mcp_context_key()):
                return
            receipt = msg_type.endswith('_result')
            if receipt and msg.get("request_id") not in self._mcp_watch_requests:
                return
            self._mcp_watch_requests.discard(str(msg.get("request_id", "")))
            self._mcp_panel.publish_watch(msg.get("data", {}), str(msg.get("error", "")), receipt=receipt)
            self._tui.request_render()
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
        if msg_type == "child_terminal_registry":
            child, token = msg.get("child_id"), msg.get("child_owner")
            if (not self._terminal_child_supported or msg.get("terminal_owner") != self._terminal_owner
                    or msg.get("group_id") != self.group_id or msg.get("session_id") != self._gateway_session_id
                    or not isinstance(child, str) or re.fullmatch(r"[a-f0-9]{12}", child) is None
                    or not isinstance(token, str) or re.fullmatch(r"[a-f0-9]{32}", token) is None
                    or self._child_terminal_owner and token != self._child_terminal_owner):
                return
            info = msg.get("terminal")
            rows: dict[str, Any]
            if msg.get("closing") is True:
                if (not isinstance(info, dict) or info.get("active") is not False
                        or type(info.get("generation")) is not int
                        or not 0 <= info["generation"] <= 2**53):
                    return
                rows = {key: value for key, value in self._terminal_children.items() if key != child}
            else:
                rows = {**self._terminal_children, child: info}
            self._accept_child_terminal_metadata({"terminal_children": rows, "child_terminal_owner": token})
            return
        if msg_type == "terminal_input_result":
            self._terminal_input_result(msg)
            return
        if msg_type == 'extension_name_after_run':
            if (msg.get('group_id') == self.group_id and msg.get('session_id') == self._gateway_session_id
                    and msg.get('name_after_run_generation') == self._extension_name_after_run_generation):
                self._chat_log.add_system('Reviewed name proposal: ' + str(msg.get('status', 'unknown')))
            return
        if msg_type == 'extension_active_tools_after_run':
            if (msg.get('group_id') == self.group_id and msg.get('session_id') == self._gateway_session_id
                    and self._extension_tools_after_run_supported
                    and msg.get('active_tools_after_run_generation') == self._extension_tools_after_run_generation):
                self._chat_log.add_system('Extension tool selection: ' + str(msg.get('status', 'unknown')))
                self._tui.request_render()
            return
        if msg_type == 'extension_compaction_after_run':
            if (msg.get('group_id') != self.group_id or msg.get('session_id') != self._gateway_session_id
                    or not self._extension_compaction_after_run_supported
                    or msg.get('compaction_after_run_generation') != self._extension_compaction_after_run_generation):
                return
            if msg.get('status') == 'started':
                key = msg.get('request_id')
                if not isinstance(key, str) or re.fullmatch('extcompact-[a-f0-9]{32}', key) is None:
                    return
                self._compact_pending = key, self._gateway_session_id
                self._compact_deadline = time.monotonic() + 600
                self._set_activity_status('compacting after original run · Esc cancels')
                self._update_hints()
            self._chat_log.add_system('Extension compaction: ' + str(msg.get('status', 'unknown')))
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
        if msg_type in {'extension_custom_control', 'child_extension_custom_control'}:
            item = next((item for item in self._extension_dialog_queue if item['id'] == msg.get('id')), None)
            if (item is None or item.get('kind') != 'custom' or not item.get('live_overlay')
                    or not self._extension_custom_live_overlay_supported or msg.get('group_id') != self.group_id
                    or msg.get('session_id') != self._gateway_session_id):
                return
            if msg_type.startswith('child_'):
                if (msg.get('child_id') != item.get('child_id') or msg.get('child_owner') != item.get('child_owner')):
                    return
            elif item.get('child_id'):
                return
            panel = self._extension_stack.panels.get(item['id'])
            from xdog.tui.components.extension_custom import overlay_state
            previous_overlay = item.get('overlay_state')
            overlay_update = overlay_state(msg.get('state'),
                previous_overlay.get('sequence', 0) if isinstance(previous_overlay, dict) else 0)
            if overlay_update is None:
                return
            item['overlay_state'] = overlay_update
            if isinstance(panel, ExtensionCustom):
                panel.accept_control(overlay_update)
            self._sync_extension_dialog()
            self._update_hints()
            self._tui.request_render()
            return
        if msg_type in {"extension_custom_frame", "extension_custom_error", "extension_custom_ack",
                        "child_extension_custom_frame", "child_extension_custom_error", "child_extension_custom_ack"}:
            panel = self._extension_dialog_panel
            if (not isinstance(panel, ExtensionCustom) or msg.get("id") != panel.dialog["id"]
                    or msg.get("group_id") != self.group_id or msg.get("session_id") != self._gateway_session_id):
                return
            if msg_type.startswith("child_"):
                if (msg.get("child_id") != panel.dialog.get("child_id")
                        or msg.get("child_owner") != panel.dialog.get("child_owner")):
                    return
            elif panel.dialog.get("child_id"):
                return
            if msg_type.endswith("_error"):
                self._chat_log.add_system("Extension custom rendering/input failed; view canceled")
            if panel.accept(msg):
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
                if (dialog["kind"] == "form" and (not self._extension_forms_supported
                        or multi_choice_form(dialog) and not self._extension_multi_forms_supported)
                        or dialog["kind"] == "custom" and (not self._extension_custom_supported
                            or custom_rich(dialog) and not self._extension_custom_rich_supported
                            or dialog.get("overlay") is not None and not self._extension_custom_overlay_supported
                            or dialog.get("live_overlay") and not self._extension_custom_live_overlay_supported)):
                    self._queue_request({"type": "child_extension_dialog_answer", "id": dialog["id"], "value": None,
                        "group_id": self.group_id, "session_id": msg["session_id"], "child_id": msg["child_id"],
                        "child_owner": msg["child_owner"]})
                    self._chat_log.add_system("Updated custom-view gateway required; dialog canceled"
                        if dialog["kind"] == "custom" else "Updated typed-form gateway required; dialog canceled")
                    return
                item = {**dialog, "extension": str(msg.get("extension", "")), "child_id": msg["child_id"],
                        "child_owner": msg["child_owner"], "session_id": msg["session_id"]}
                if item.get("operation") == "queue_input":
                    item["_child_queue_generation"] = msg.get("child_queued_input_generation")
                if (item.get("parent_id") and not self._extension_custom_nested_supported
                        or not self._extension_stack.accepts(item, self._extension_dialog_queue)):
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
        if msg_type in {'extension_editor_write_commit', 'extension_editor_write_discard',
                        'child_extension_editor_write_commit', 'child_extension_editor_write_discard'}:
            identity = msg.get('editor_write', {})
            if not isinstance(identity, dict):
                return
            if (not self._extension_editor_write_supported or not self._connection_ready
                    or msg.get('group_id') != self.group_id or msg.get('session_id') != self._gateway_session_id
                    or type(msg.get('editor_write_generation')) is not int
                    or msg['editor_write_generation'] != self._extension_editor_write_generation
                    or identity.get('child_id', '') != msg.get('child_id', '')
                    or bool(identity.get('child_id')) != msg_type.startswith('child_')
                    or msg_type.startswith('child_') and msg.get('child_owner') != self._extension_editor_write_owner):
                self._editor_write_reviews.discard(dict(msg))
                return
            if msg_type.endswith('_commit'):
                applied = self._editor_write_reviews.apply(dict(msg))
                self._chat_log.add_system('Reviewed editor text applied; nothing sent' if applied else
                                          'Reviewed editor proposal discarded; original draft/owner changed')
            else:
                self._editor_write_reviews.discard(dict(msg))
            self._tui.request_render()
            return
        if msg_type == "extension_dialog_accepted":
            self._accept_extension_navigation(msg)
            return
        if msg_type == "extension_dialog_closed":
            review = self._extension_navigation_review
            if review is not None and msg.get("id") == review[2]["id"]:
                self._extension_navigation_review = None
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
                    if item.get('operation') == 'context_message':
                        item['_context_message_generation'] = msg.get('context_message_generation')
                        if (not self._extension_context_messages_supported
                                or type(item['_context_message_generation']) is not int
                                or item['_context_message_generation'] != self._extension_context_messages_generation):
                            self._queue_request({'type': 'extension_dialog_answer', 'id': item['id'], 'value': None,
                                'group_id': self.group_id, 'session_id': self._gateway_session_id})
                            return
                    if item.get("operation") == "queue_input":
                        item.update(_queue_generation=msg.get("queued_input_generation"), _queue_run=msg.get("run_id"))
                    if item.get("operation") == "propose":
                        item.update(_editor_write_command=msg.get("request_id"),
                                    _editor_write_run=msg.get("run_id"))
                    if (item["kind"] == "form" and (not self._extension_forms_supported
                            or multi_choice_form(item) and not self._extension_multi_forms_supported)
                            or item["kind"] == "custom" and (not self._extension_custom_supported
                                or custom_rich(item) and not self._extension_custom_rich_supported
                                or item.get("overlay") is not None and not self._extension_custom_overlay_supported
                                or item.get("live_overlay") and not self._extension_custom_live_overlay_supported)):
                        self._queue_request({"type": "extension_dialog_answer", "id": item["id"], "value": None,
                                             "group_id": self.group_id, "session_id": self._gateway_session_id})
                        self._chat_log.add_system("Updated custom-view gateway required; dialog canceled"
                            if item["kind"] == "custom" else "Updated typed-form gateway required; dialog canceled")
                        return
                except (ValueError, TypeError):
                    self._chat_log.add_system("Invalid extension dialog ignored")
                    return
                if (item.get("parent_id") and not self._extension_custom_nested_supported
                        or not self._extension_stack.accepts(item, self._extension_dialog_queue)):
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
                        self._terminal_supported = False
                        self._terminal_input.invalidate()
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
                selection = msg.get('active_tool_selection')
                if isinstance(selection, dict) and type(selection.get('applied')) is bool:
                    self._chat_log.add_system('Active tool selection applied; permissions unchanged'
                        if selection['applied'] else 'Active tool selection not applied; owner/inventory changed')
            if msg.get('context_pending') is True and msg.get('success') is True:
                # Result delivery permits commit, but UI stays busy until owned disk settles.
                self._extension_pending = extension_pending
                self._context_results[extension_pending[0]] = (
                    self._gateway_session_id, self._mcp_context_key(), self._extension_context_messages_generation)
                if len(self._context_results) > 8:
                    self._context_results.pop(next(iter(self._context_results)))
            can_finish = msg.get('success') is True
            if self._extension_navigation is not None and self._extension_navigation[2]['action'] == 'compact':
                can_finish = can_finish and msg.get('compaction_queued') is True
            if self._extension_navigation is not None and self._extension_navigation[2]['action'] == 'message':
                can_finish = can_finish and msg.get('message_queued') is True
            if self._finish_extension_navigation(extension_pending[0], can_finish, msg.get('message_delivery')):
                self._extension_browser = None
            else:
                self._finish_extension_browser(extension_pending[0], msg.get("success") is True)
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
        if msg_type in {'child_recovery_ready', 'child_recovery_failed'}:
            key = str(msg.get('request_id', ''))
            recovery_pending = self._children_pending.get(key)
            if (key in self._child_ui_requests and recovery_pending is not None and recovery_pending[0] == 'queue'
                    and msg.get('session_id') == recovery_pending[1] == self._gateway_session_id
                    and msg.get('group_id') == self.group_id
                    and self._child_context == self._mcp_context_key() and self._child_panel is not None
                    and self._child_panel.queue is not None
                    and self._child_queue_owners.get(key) == self._child_panel.queue.owner_token):
                if msg_type == 'child_recovery_ready':
                    self._child_recovery_ready[key] = msg
                else:
                    self._child_panel.complete('queue', str(msg.get('error', 'Child image copy failed')))
            return
        if msg_type == "child_result":
            request_id = str(msg.get("request_id", ""))
            recovered_bytes = self._child_recovery_ready.pop(request_id, None)
            queue_owner = self._child_queue_owners.pop(request_id, '')
            browser_job = request_id in self._child_ui_requests
            self._child_ui_requests.discard(request_id)
            child_pending = self._children_pending.pop(request_id, None)
            if (child_pending is None or msg.get("group_id") != self.group_id
                    or child_pending[1] != self._gateway_session_id and not (
                        child_pending[0] == "start" and not child_pending[1]
                        and msg.get("session_id") == self._gateway_session_id)):
                return
            if queue_owner and (self._child_panel is None or self._child_panel.queue is None
                                or self._child_panel.queue.owner_token != queue_owner):
                return  # Late replies cannot adopt/cancel a replacement queue view's draft.
            if msg.get('child_return_owner') is not None and (
                    not self._child_return_supported or msg['child_return_owner'] != self._child_return_owner
                    or msg.get('queue_view') != queue_owner):
                if self._child_panel is not None:
                    self._child_panel.complete('queue', 'Return connection/view changed; backup retained')
                return
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
                self._accept_child_terminal_metadata(msg)
                if browser_job and self._child_panel is not None:
                    self._child_context = self._mcp_context_key()
                    try:
                        if child_pending[0] == "presets":
                            self._child_presets = ChildPresets.from_snapshot(msg["data"].get("presets"))
                            self._child_panel.publish_presets(self._child_presets.snapshot(),
                                                              str(msg["data"].get("error", "")))
                        elif child_pending[0] == 'queue':
                            images = ()
                            queue_data = msg['data'].get('queue')
                            if (isinstance(queue_data, dict) and 'image_receipt' in queue_data
                                    and 'returned' not in queue_data):
                                source_row = queue_data.get('recovered') or queue_data.get('return_prepared') or {}
                                if (recovered_bytes is None or recovered_bytes.get('receipt')
                                        != queue_data['image_receipt']
                                        or recovered_bytes.get('queue_revision') != queue_data.get('revision')
                                        or recovered_bytes.get('input_id') != source_row.get('id')
                                        or recovered_bytes.get('child_id') != queue_data.get('child_id')):
                                    raise ValueError('Verified child recovery bytes/receipt missing; original retained')
                                images = tuple(recovered_bytes['images'])
                            self._child_panel.complete('queue', data=msg['data'], images=images)
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
                        child = next(row for row in roster if row.id == msg["data"].get("child_id"))
                    except (ValueError, TypeError, StopIteration):
                        self._chat_log.add_system("Invalid child start receipt; inspect roster before retrying")
                    else:
                        self._chat_log.add_system(
                            f"Child {child.id} started ({child.access}); /agents inspect {child.id}")
                else:
                    self._chat_log.add_system(json.dumps(msg["data"], indent=2, ensure_ascii=True))
            if browser_job and self._child_panel is not None and not msg.get("success"):
                if child_pending[0] == "presets":
                    self._child_presets = ChildPresets()
                    self._child_panel.publish_presets((), str(msg.get("error", "Preset catalog failed")))
                self._child_panel.complete(child_pending[0], str(msg.get("error", "Child control failed")))
            self._tui.request_render()
            return
        if msg_type == "plan_result":
            pending = self._plan_pending
            if pending is None or msg.get("request_id") != pending[0] or msg.get("group_id") != self.group_id:
                return
            self._plan_pending = None
            if pending[2] != self._gateway_session_id:
                self._chat_log.add_system("Plan session changed; refresh before retrying")
                return
            if msg.get("success") is not True:
                self._chat_log.add_system(str(msg.get("error", "Plan control failed")))
            else:
                try:
                    if not isinstance(msg.get("plan"), dict):
                        raise ValueError("Missing plan state")
                    state = PlanState.from_dict(msg["plan"])
                    actual = msg.get("session_id")
                    if not isinstance(actual, str):
                        raise ValueError("Invalid plan session")
                except (ValueError, TypeError):
                    self._chat_log.add_system("Invalid plan response; no transition trusted")
                    return
                if actual != self._gateway_session_id:
                    if pending[1] != "start" or pending[2]:
                        self._chat_log.add_system("Unexpected plan session; refresh before retrying")
                        return
                    self._gateway_session_id = actual
                    self._state["session_id"] = actual
                    self._output_epoch += 1
                self._plan_snapshot = state
                self._plan_context = self._mcp_context_key()
                if pending[1] != "show":
                    self._chat_log.add_system(
                        f"Plan {state.mode} · revision {state.revision}; existing tool policy retained")
                    if self._plan_panel is None:
                        self._open_plan()
                self._update_hints()
            if self._plan_refresh_needed:
                self._plan_refresh_needed = False
                self._plan_send("show", self._plan_snapshot.revision)
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
        if msg_type == 'mcp_elicitation_result':
            elicitation_id = str(msg.get('request_id', ''))
            operation = self._mcp_elicitation_requests.pop(elicitation_id, None)
            if (operation is None or msg.get('group_id') != self.group_id
                    or msg.get('session_id') != self._gateway_session_id
                    or msg.get('view') != self._mcp_auth_lease or self._mcp_panel is None
                    or not self._mcp_elicitation_supported or self._mcp_owner_context != self._mcp_context_key()):
                return
            if not msg.get('success'):
                server = str(msg.get('server', ''))
                self._mcp_elicitation_servers.discard(server)
                elicitation_review = self._mcp_panel.elicitation_review
                if elicitation_review is not None:
                    elicitation_review.disable('Elicitation control refused; inspect owner, no replay')
                if server:
                    self._queue_request({'type': 'mcp_elicitation_control', 'action': 'detach', 'server': server,
                        'request_id': 'elicitation-detach-' + uuid.uuid4().hex, 'group_id': self.group_id,
                        'session_id': self._gateway_session_id, 'view': self._mcp_auth_lease})
            # An answer ack must not unbusy a newer input/output review that
            # raced it on the connection. Reviews own their own lifecycle.
            if operation == 'elicitation_answer' and msg.get('success'):
                return  # Review/closed events own progress; an ack cannot overwrite completed status.
            if operation != 'elicitation_answer' or self._mcp_panel.elicitation_review is None:
                self._mcp_publish(operation, msg,
                                  '' if msg.get('success') else str(msg.get('error', 'Elicitation refused')))
            return
        if msg_type == 'mcp_elicitation_event':
            if (not self._mcp_elicitation_supported or not self._connection_ready or self._mcp_panel is None
                    or msg.get('group_id') != self.group_id or msg.get('session_id') != self._gateway_session_id
                    or msg.get('view') != self._mcp_auth_lease or self._mcp_owner_context != self._mcp_context_key()):
                return
            value = msg.get('event')
            if not isinstance(value, dict) or value.get('server') not in self._mcp_elicitation_servers:
                return
            if value.get('type') in {'elicitation_url_pending', 'elicitation_url_complete'}:
                self._chat_log.add_system(str(value.get('notice', 'URL elicitation notice')))
                self._tui.request_render()
                return
            if value.get('type') == 'elicitation_closed':
                self._mcp_panel.elicitation_closed(str(value.get('id', '')))
            else:
                try:
                    if (self._extension_dialog_panel is not None or self._child_panel is not None
                            or self._tui.has_overlay()):
                        raise ValueError('Protected host focus unavailable')
                    self._mcp_panel.publish_elicitation(value)
                except (ValueError, TypeError):
                    self._mcp_send('elicitation_off', str(value.get('server', '')), '', {})
                    self._mcp_panel._busy = False
                    self._mcp_panel._message = 'Elicitation review unavailable; canceled, no replay'
            self._tui.request_render()
            return
        if msg_type == 'mcp_sampling_result':
            sampling_id = str(msg.get('request_id', ''))
            operation = self._mcp_sampling_requests.pop(sampling_id, None)
            if (operation is None or msg.get('group_id') != self.group_id
                    or msg.get('session_id') != self._gateway_session_id
                    or msg.get('view') != self._mcp_auth_lease or self._mcp_panel is None
                    or not self._mcp_sampling_supported or self._mcp_owner_context != self._mcp_context_key()):
                return
            if not msg.get('success'):
                server = str(msg.get('server', ''))
                self._mcp_sampling_servers.discard(server)
                sampling_review = self._mcp_panel.sampling_review
                if sampling_review is not None:
                    sampling_review.disable('Sampling control refused; inspect owner, no replay')
                if server:
                    self._queue_request({'type': 'mcp_sampling_control', 'action': 'detach', 'server': server,
                        'request_id': 'sampling-detach-' + uuid.uuid4().hex, 'group_id': self.group_id,
                        'session_id': self._gateway_session_id, 'view': self._mcp_auth_lease})
            # An answer ack must not unbusy a newer input/output review that
            # raced it on the connection. Reviews own their own lifecycle.
            if operation == 'sampling_answer' and msg.get('success'):
                return  # Review/closed events own progress; an ack cannot overwrite completed status.
            if operation != 'sampling_answer' or self._mcp_panel.sampling_review is None:
                self._mcp_publish(operation, msg,
                                  '' if msg.get('success') else str(msg.get('error', 'Sampling refused')))
            return
        if msg_type == 'mcp_sampling_event':
            if (not self._mcp_sampling_supported or not self._connection_ready or self._mcp_panel is None
                    or msg.get('group_id') != self.group_id or msg.get('session_id') != self._gateway_session_id
                    or msg.get('view') != self._mcp_auth_lease or self._mcp_owner_context != self._mcp_context_key()):
                return
            value = msg.get('event')
            if not isinstance(value, dict) or value.get('server') not in self._mcp_sampling_servers:
                return
            if value.get('type') == 'sampling_closed':
                self._mcp_panel.sampling_closed(str(value.get('id', '')))
            else:
                try:
                    if (self._extension_dialog_panel is not None or self._child_panel is not None
                            or self._tui.has_overlay()):
                        raise ValueError('Protected host focus unavailable')
                    self._mcp_panel.publish_sampling(value, tuple(msg.get('models', ())))
                except (ValueError, TypeError):
                    self._mcp_send('sampling_off', str(value.get('server', '')), '', {})
                    self._mcp_panel._busy = False
                    self._mcp_panel._message = 'Sampling review unavailable; canceled, no replay'
            self._tui.request_render()
            return
        if msg_type == 'mcp_roots_result':
            root_identifier = str(msg.get('request_id', ''))
            if root_identifier not in self._mcp_root_requests:
                return
            self._mcp_root_requests.discard(root_identifier)
            if (root_identifier != self._mcp_active or msg.get('group_id') != self.group_id
                    or msg.get('view') != self._mcp_auth_lease or not self._mcp_panel
                    or not self._mcp_roots_supported or self._mcp_owner_context != self._mcp_context_key()):
                return
            self._mcp_publish(self._mcp_action, msg.get('data', {}),
                              '' if msg.get('success') else str(msg.get('error', 'Root disclosure unavailable')))
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
        if msg_type in {"todo", "goal"} and msg.get("run_id"):
            run_id = str(msg["run_id"])
            if run_id != self._active_run_id or run_id == self._aborting_run_id:
                return

        if msg_type in {'child_extension_input_queued', 'child_extension_input_consumed',
                        'child_extension_input_returned'}:
            key, child, session, token = (msg.get('id'), msg.get('child_id'),
                                          msg.get('session_id'), msg.get('child_owner'))
            if (not isinstance(key, str) or re.fullmatch('extq-[a-f0-9]{32}', key) is None
                    or not isinstance(child, str) or not isinstance(session, str) or not isinstance(token, str)
                    or msg.get('group_id') != self.group_id or session != self._gateway_session_id):
                return
            if msg_type == 'child_extension_input_queued':
                if (not self._child_queued_inputs_supported or token != self._child_queued_inputs_owner
                        or type(msg.get('child_queued_input_generation')) is not int
                        or msg['child_queued_input_generation'] != self._child_queued_inputs_generation):
                    return
                self._child_extension_inputs[key] = child, session, token
                self._chat_log.add_system(f'Child {child}: extension input {key} queued, not consumed')
            elif self._child_extension_inputs.pop(key, None) == (child, session, token):
                self._chat_log.add_system(f'Child {child}: extension input {key} '
                    + ('consumed' if msg_type.endswith('_consumed') else
                       'returned without replay; inspect this child’s queue/history'))
            panel = self._child_panel
            if panel is not None and panel.queue is not None and panel.queue.child == child:
                panel.queue.refresh()
            self._tui.request_render()
            return
        if msg_type == 'extension_input_queued':
            key, run, count = msg.get('id'), msg.get('run_id'), msg.get('image_count')
            if (not isinstance(key, str) or re.fullmatch('extq-[a-f0-9]{32}', key) is None
                    or run != self._active_run_id or msg.get('group_id') != self.group_id
                    or msg.get('session_id') != self._gateway_session_id
                    or type(msg.get('success')) is not bool or type(count) is not int or not 0 <= count <= 4
                    or msg.get('delivery') not in {'steer', 'follow_up'} or not isinstance(msg.get('content'), str)
                    or len(msg['content'].encode()) > 17000):
                return
            if (msg['success'] and self._extension_queued_inputs_supported and self._connection_ready
                    and type(msg.get('queued_input_generation')) is int
                    and msg['queued_input_generation'] == self._extension_queued_inputs_generation):
                if key not in self._routed_inputs:
                    self._routed_inputs[key] = QueueEntry(key, msg['content'], msg['delivery'], 'accepted', str(run))
                    self._extension_queued_inputs[key] = str(run), count
                    self._chat_log.add_system(f'Extension input {key} queued; not yet consumed')
            else:
                self._extension_queued_inputs.pop(key, None)
                self._routed_inputs.pop(key, None)
                self._chat_log.add_system('Extension queue admission failed/withdrawn; no replay or draft change')
            self._update_queue_summary()
            self._tui.request_render()
            return
        if msg_type in {"input_transformed", "input_rejected", "input_handled"}:
            run = str(msg.get("run_id", ""))
            if run != self._active_run_id or msg.get("group_id") != self.group_id:
                return
            key = str(msg.get("id", ""))
            if key in self._extension_queued_inputs and msg_type in {'input_handled', 'input_rejected'}:
                self._extension_queued_inputs.pop(key, None)
                self._routed_inputs.pop(key, None)
                self._transformed_inputs.pop(key, None)
                self._chat_log.add_system('Extension input not admitted to model; original draft/images kept')
                self._update_queue_summary()
                self._tui.request_render()
                return
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
            owner, parent_generation = msg.get('parent_draft_owner'), msg.get('parent_draft_generation')
            caps = {key: msg.get(key, 0) for key in ('parent_saved_drafts', 'parent_saved_images',
                                                                 'parent_saved_save', 'parent_saved_discard',
                                                                 'parent_saved_uploads')}
            if (any(type(value) is not int or value not in (0, 1) for value in caps.values())
                    or not isinstance(owner, str) or re.fullmatch(r'[a-f0-9]{32}', owner) is None
                    or type(parent_generation) is not int or parent_generation < 0):
                caps = {}
            else:
                caps.update(parent_draft_owner=owner, parent_draft_generation=parent_generation)
            if caps != self._remote_parent_caps:
                self._close_remote_parent()
            self._remote_parent_caps = caps
            self._extension_active_tools_supported = (type(msg.get("extension_active_tools")) is int
                                                       and msg["extension_active_tools"] == 1)
            tools_after_generation = msg.get('active_tools_after_run_generation', -1)
            self._extension_tools_after_run_generation = (tools_after_generation
                if type(tools_after_generation) is int else -1)
            self._extension_tools_after_run_supported = (type(msg.get('extension_active_tools_after_run')) is int
                and msg['extension_active_tools_after_run'] == 1 and self._extension_tools_after_run_generation >= 0)
            self._extension_compaction_supported = (type(msg.get('extension_compaction')) is int
                                                     and msg['extension_compaction'] == 1)
            child_queue_generation = msg.get('child_extension_queued_inputs_generation', -1)
            child_queue_owner = msg.get('child_extension_queued_inputs_owner', '')
            self._child_queued_inputs_supported = (type(msg.get('child_extension_queued_inputs')) is int
                and msg['child_extension_queued_inputs'] == 1 and type(child_queue_generation) is int
                and child_queue_generation >= 0 and isinstance(child_queue_owner, str)
                and re.fullmatch('[a-f0-9]{32}', child_queue_owner) is not None)
            self._child_queued_inputs_generation = (child_queue_generation
                                                    if type(child_queue_generation) is int else -1)
            self._child_queued_inputs_owner = child_queue_owner if self._child_queued_inputs_supported else ''
            context_generation = msg.get('extension_context_messages_generation', -1)
            self._extension_context_messages_supported = (type(msg.get('extension_context_messages')) is int
                and msg['extension_context_messages'] == 1 and type(context_generation) is int
                and context_generation >= 0)
            self._extension_context_messages_generation = context_generation if type(context_generation) is int else -1
            queue_generation = msg.get('extension_queued_inputs_generation', -1)
            self._extension_queued_inputs_supported = (type(msg.get('extension_queued_inputs')) is int
                and msg['extension_queued_inputs'] == 1 and type(queue_generation) is int and queue_generation >= 0)
            self._extension_queued_inputs_generation = queue_generation if type(queue_generation) is int else -1
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
            write_generation = msg.get('extension_editor_write_generation', -1)
            write_owner = msg.get('extension_editor_write_owner', '')
            write_supported = (type(msg.get('extension_editor_write')) is int and msg['extension_editor_write'] == 1
                               and type(write_generation) is int and write_generation >= 0
                               and isinstance(write_owner, str)
                               and re.fullmatch('[a-f0-9]{32}', write_owner) is not None)
            if (not write_supported or write_generation != self._extension_editor_write_generation
                    or write_owner != self._extension_editor_write_owner):
                self._editor_write_reviews.clear()
            self._extension_editor_write_supported = write_supported
            self._extension_editor_write_generation = write_generation if type(write_generation) is int else -1
            self._extension_editor_write_owner = write_owner if write_supported else ''
            self._extension_message_supported = (type(msg.get('extension_message')) is int
                                                 and msg['extension_message'] == 1)
            image_generation = msg.get('extension_message_images_generation', -1)
            self._extension_message_images_supported = (type(msg.get('extension_message_images')) is int
                and msg['extension_message_images'] == 1 and type(image_generation) is int and image_generation >= 0)
            self._extension_message_images_generation = image_generation if type(image_generation) is int else -1
            message_generation = msg.get('extension_message_generation', -1)
            self._extension_message_generation = message_generation if type(message_generation) is int else -1
            self._extension_message_supported &= self._extension_message_generation >= 0
            compact_generation = msg.get('extension_compaction_generation', 0)
            self._extension_compaction_generation = (compact_generation if type(compact_generation) is int
                                                      and compact_generation >= 0 else -1)
            self._extension_compaction_supported &= self._extension_compaction_generation >= 0
            self._extension_name_after_run_supported = (type(msg.get('extension_name_after_run')) is int
                                                        and msg.get('extension_name_after_run') == 1)
            name_generation = msg.get('name_after_run_generation', 0)
            self._extension_name_after_run_generation = name_generation if type(name_generation) is int else -1
            self._extension_compaction_after_run_supported = (
                type(msg.get('extension_compaction_after_run')) is int
                and msg['extension_compaction_after_run'] == 1)
            generation = msg.get('compaction_after_run_generation', -1)
            self._extension_compaction_after_run_generation = generation if type(generation) is int else -1
            self._extension_compaction_after_run_supported &= self._extension_compaction_after_run_generation >= 0
            self._extension_models_supported = type(msg.get("extension_models")) is int and msg["extension_models"] == 1
            self._native_models_supported = type(msg.get("native_models")) is int and msg["native_models"] == 1
            self._reasoning_supported = type(msg.get("reasoning_controls")) is int and msg["reasoning_controls"] == 1
            self._mcp_watch_supported = type(msg.get("mcp_resource_watch")) is int and msg["mcp_resource_watch"] == 1
            terminal_owner = msg.get('terminal_owner', '')
            terminal_supported = (type(msg.get('terminal_input')) is int and msg['terminal_input'] == 1
                                  and isinstance(terminal_owner, str)
                                  and re.fullmatch(r'[a-f0-9]{32}', terminal_owner) is not None)
            if not terminal_supported or terminal_owner != self._terminal_owner:
                self._terminal_input.invalidate()
            self._terminal_supported = terminal_supported
            self._terminal_release_supported = (terminal_supported
                and type(msg.get("terminal_release_input")) is int and msg["terminal_release_input"] == 1)
            self._terminal_owner = terminal_owner if terminal_supported else ''
            child_token = msg.get('child_terminal_owner', '')
            child_supported = (type(msg.get('terminal_child_input')) is int and msg['terminal_child_input'] == 1
                               and isinstance(child_token, str)
                               and re.fullmatch(r'[a-f0-9]{32}', child_token) is not None)
            if self._terminal_child() and (not child_supported or child_token != self._child_terminal_owner):
                self._terminal_input.invalidate()
            self._terminal_child_supported = child_supported
            if not child_supported or child_token != self._child_terminal_owner:
                self._terminal_children = {}
            self._child_terminal_owner = child_token if child_supported else ""
            self._extension_supported = type(msg.get("extension_controls")) is int and msg["extension_controls"] == 1
            if isinstance(msg.get("extension_snapshot"), dict):
                try:
                    self._extension_snapshot = validate_snapshot(msg["extension_snapshot"])
                except (ValueError, TypeError):
                    self._extension_supported = False
                    self._terminal_supported = False
                    self._terminal_input.invalidate()
                    self._chat_log.add_system("Invalid extension registrations; controls disabled")
            if isinstance(msg.get("plan_error"), str):
                self._chat_log.add_system(msg["plan_error"])
            new_return_owner = msg.get('child_return_owner')
            new_return_supported = (type(msg.get('child_image_return')) is int and msg.get('child_image_return') == 1
                                    and isinstance(new_return_owner, str) and len(new_return_owner) == 32)
            if self._child_return_owner and (not new_return_supported or new_return_owner != self._child_return_owner):
                if self._child_panel is not None and self._child_panel.queue is not None:
                    self._child_panel.queue.fail('Live return owner changed; local backup kept, inspect queue')
                self._child_recovery_ready.clear()
            self._child_return_supported = new_return_supported
            self._child_return_owner = (new_return_owner
                                        if new_return_supported and isinstance(new_return_owner, str) else '')
            self._child_task_recovery_supported = (type(msg.get('child_task_recovery')) is int
                                                   and msg.get('child_task_recovery') == 1)
            self._child_image_recovery_supported = (type(msg.get('child_image_recovery')) is int
                                                     and msg.get('child_image_recovery') == 1)
            if self._child_panel is not None:
                self._child_panel.queue_image_recovery_enabled = self._child_image_recovery_supported
                self._child_panel.queue_task_recovery_enabled = self._child_task_recovery_supported
                self._child_panel.queue_image_return_enabled = self._child_return_supported
                if self._child_panel.queue is not None:
                    self._child_panel.queue.image_recovery_enabled = self._child_image_recovery_supported
                    self._child_panel.queue.task_recovery_enabled = self._child_task_recovery_supported
                    self._child_panel.queue.image_return_enabled = self._child_return_supported
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
            self._plan_supported = msg.get("plan_controls") == 1 and type(msg.get("plan_controls")) is int
            try:
                if self._plan_supported and not isinstance(msg.get("plan"), dict):
                    raise ValueError("Missing advertised plan state")
                self._plan_snapshot = PlanState.from_dict(msg.get("plan"))
            except (ValueError, TypeError):
                self._plan_supported = False
                self._chat_log.add_system("Invalid gateway plan state; controls disabled")
            actual = str(msg.get("session_id") or "")
            if self._has_connected and actual != self._gateway_session_id and msg.get("session_controls") == 1:
                self._session_context_stale = True
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
            self._mcp_private_jwt_supported = (type(msg.get('mcp_private_key_jwt')) is int
                                               and msg.get('mcp_private_key_jwt') == 1)
            private_panel = self._mcp_panel.auth if self._mcp_panel is not None else None
            if (not self._mcp_private_jwt_supported and private_panel is not None
                    and private_panel.review.get('token_auth_method') == 'private_key_jwt'
                    and self._mcp_panel is not None):
                self._mcp_auth_requests.clear()
                self._mcp_auth_review = None
                self._mcp_active = None
                self._mcp_panel._busy = False
                private_panel.disable('Private-key signing support withdrawn; reopen fresh consent. '
                                      'Completed tokens are not remotely revoked.')
            self._mcp_refresh_supported = type(msg.get("mcp_refresh")) is int and msg.get("mcp_refresh") == 1
            self._mcp_client_auth_supported = (type(msg.get("mcp_client_auth")) is int
                                               and msg.get("mcp_client_auth") == 1)
            self._mcp_credentials_supported = (type(msg.get("mcp_credentials")) is int
                                               and msg.get("mcp_credentials") == 1)
            self._mcp_registration_supported = (type(msg.get("mcp_registration")) is int
                                                 and msg.get("mcp_registration") == 1)
            self._mcp_auth_supported = type(msg.get("mcp_auth")) is int and msg.get("mcp_auth") == 1
            self._extension_shortcuts_supported = (type(msg.get("extension_shortcuts")) is int
                                                   and msg.get("extension_shortcuts") == 1)
            self._extension_navigation_supported = (type(msg.get("extension_navigation")) is int
                                                    and msg.get("extension_navigation") == 1)
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
            self._extension_name_supported = (type(msg.get('extension_session_name')) is int
                                              and msg.get('extension_session_name') == 1)
            self._extension_session_supported = (type(msg.get("extension_session")) is int
                                                  and msg.get("extension_session") == 1)
            self._extension_editor_supported = (type(msg.get("extension_editor")) is int
                                                and msg.get("extension_editor") == 1)
            self._extension_command_results_supported = (type(msg.get("extension_command_results")) is int
                                                          and msg.get("extension_command_results") == 1)
            self._extension_custom_nested_supported = (type(msg.get("extension_custom_nested")) is int
                                                       and msg.get("extension_custom_nested") == 1)
            self._extension_custom_live_overlay_supported = (type(msg.get("extension_custom_live_overlay")) is int
                                                             and msg.get("extension_custom_live_overlay") == 1)
            self._extension_custom_overlay_supported = (type(msg.get("extension_custom_overlay")) is int
                                                        and msg.get("extension_custom_overlay") == 1)
            self._extension_custom_rich_supported = (type(msg.get("extension_custom_rich")) is int
                                                     and msg.get("extension_custom_rich") == 1)
            self._extension_custom_supported = (type(msg.get("extension_custom")) is int
                                                and msg.get("extension_custom") == 1)
            self._extension_multi_forms_supported = (type(msg.get("extension_multi_forms")) is int
                                                     and msg.get("extension_multi_forms") == 1)
            self._extension_forms_supported = (type(msg.get("extension_forms")) is int
                                               and msg.get("extension_forms") == 1)
            self._mcp_resource_completion_supported = (type(msg.get("mcp_resource_completion")) is int
                                                       and msg.get("mcp_resource_completion") == 1)
            self._mcp_completion_supported = (type(msg.get("mcp_completion")) is int
                                              and msg.get("mcp_completion") == 1)
            self._mcp_data_supported = type(msg.get("mcp_context")) is int and msg["mcp_context"] == 1
            self._mcp_combined_watch_supported = (type(msg.get("mcp_combined_watch")) is int
                                                 and msg.get("mcp_combined_watch") == 1)
            self._tool_inventory_supported = (type(msg.get('tool_inventory')) is int and msg.get('tool_inventory') == 1)
            self._tool_inventory_selection_supported = (type(msg.get('tool_inventory_selection')) is int
                                                        and msg.get('tool_inventory_selection') == 1)
            if isinstance(self._transcript_panel, ToolInventoryPanel) and not self._tool_inventory_supported:
                self._cancel_tools()
                self._transcript_panel.invalidate_inventory('Tool-inventory capability withdrawn; no cached fallback')
            self._mcp_catalog_watch_supported = (type(msg.get("mcp_catalog_watch")) is int
                                                and msg.get("mcp_catalog_watch") == 1)
            self._mcp_content_types_supported = (type(msg.get("mcp_content_types")) is int
                                                  and msg["mcp_content_types"] == 1)
            self._mcp_elicitation_supported = (type(msg.get('mcp_elicitation')) is int
                                               and msg.get('mcp_elicitation') == 1)
            if not self._mcp_elicitation_supported:
                self._mcp_elicitation_servers.clear()
                self._mcp_elicitation_requests.clear()
                if self._mcp_panel is not None and self._mcp_panel.elicitation_review is not None:
                    self._mcp_panel.elicitation_review.disable('Elicitation capability withdrawn; no replay')
            self._mcp_sampling_supported = type(msg.get('mcp_sampling')) is int and msg.get('mcp_sampling') == 1
            if not self._mcp_sampling_supported:
                self._mcp_sampling_servers.clear()
                self._mcp_sampling_requests.clear()
                if self._mcp_panel is not None and self._mcp_panel.sampling_review is not None:
                    self._mcp_panel.sampling_review.disable('Sampling capability withdrawn; no replay')
            self._mcp_roots_supported = type(msg.get('mcp_roots')) is int and msg.get('mcp_roots') == 1
            if not self._mcp_roots_supported and self._mcp_panel and self._mcp_panel.roots_review:
                self._mcp_panel.roots_review.invalidate()
                self._mcp_panel._cancel_roots()
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
            # The local socket peer owns the group's workspace. Never suggest
            # paths relative to the directory from which the TUI was launched.
            workspace = msg.get("workspace_dir")
            if isinstance(workspace, str) and workspace and not self._exit_requested:
                if self._file_completions is None or str(self._file_completions.root) != workspace:
                    if self._file_completions is not None:
                        self._file_completions.close()
                    if self._review_panel is not None:
                        self._close_review()
                    self._file_completions = WorkspaceFileCompletionProvider(workspace)
                    self._editor.set_autocomplete_provider(self._file_completions)
            elif self._file_completions is not None:
                if self._review_panel is not None:
                    self._close_review()
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

            self._update_header()
            self._update_footer()
            self._set_connection_status(connection_status)
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

        # Goal widget update (persistent across turns)
        if msg_type == "goal":
            goal = msg.get("goal")
            if goal:
                self._render_goal(goal)
            return

        if msg_type == 'extension_context_delivery_result':
            key = str(msg.get('request_id', ''))
            if (key in self._context_results and msg.get('group_id') == self.group_id
                    and msg.get('session_id') == self._gateway_session_id):
                if self._extension_pending is not None and self._extension_pending[0] == key:
                    self._extension_pending = None
                if msg.get('success') is not True or msg.get('continuation') is not True:
                    self._context_results.pop(key, None)
                if msg.get('success') is not True:
                    self._chat_log.add_system('Canonical context delivery failed/uncertain; inspect source, no replay')
                self._update_hints()
                self._tui.request_render()
            return
        if msg_type == 'extension_context_continuation':
            receipt = self._context_results.pop(str(msg.get('request_id', '')), None)
            if (receipt is not None and self._connection_ready and self._extension_context_messages_supported
                    and receipt == (self._gateway_session_id, self._mcp_context_key(),
                                    self._extension_context_messages_generation)
                    and msg.get('group_id') == self.group_id and msg.get('session_id') == receipt[0]
                    and type(msg.get('generation')) is int and msg['generation'] == receipt[2]
                    and isinstance(msg.get('token'), str) and re.fullmatch('[a-f0-9]{32}', msg['token'])
                    and self._active_run_id is None and self._extension_pending is None and not self._waiting):
                self._start_message('', (), requested_message={'token': msg['token'],
                    'generation': msg['generation'], 'canonical_context': True})
            return
        if msg_type == 'extension_context_admitted':
            if (msg.get('group_id') == self.group_id and msg.get('run_id') == self._active_run_id
                    and self._active_run_id is not None and msg.get('session_id') == self._gateway_session_id):
                from xdog.agent.extensions.context_message import display_text
                try:
                    body = display_text({key: value for key, value in msg.items()
                                         if key not in {'type', 'group_id', 'run_id'}})
                except ValueError:
                    return
                if body:
                    self._chat_log.add_system(body)
                    self._tui.request_render()
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
                    self._terminal_supported = False
                    self._terminal_input.invalidate()
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
                    self._restore_uncertain_inputs(run_id)
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
            if self._plan_panel is not None and self._plan_supported:
                if self._plan_pending is None:
                    self._plan_send("show", self._plan_snapshot.revision)
                else:
                    self._plan_refresh_needed = True
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
            self._restore_uncertain_inputs(str(run_id))
            content = msg.get("content", "")
            self._set_waiting(False)
            self._active_run_id = None
            self._active_text = None
            self._active_images = ()
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
            self._restore_uncertain_inputs(str(run_id))
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
            if msg_type == "internal_error":
                self._terminal_input.invalidate()
                self._terminal_supported = False
                self._terminal_owner = ""
                if self._mcp_requests:
                    self._chat_log.add_system("MCP control outcome uncertain; inspect /mcp list before retrying")
                    self._mcp_requests.clear()
                self._pending_output = None
                self._output_epoch += 1
            if msg_type == "internal_error":
                self._connection_ready = False
                self._mcp_elicitation_supported = False
                self._mcp_elicitation_servers.clear()
                self._mcp_elicitation_requests.clear()
                if self._mcp_panel is not None and self._mcp_panel.elicitation_review is not None:
                    self._mcp_panel.elicitation_review.disable('Elicitation disconnected; response withheld')
                self._mcp_sampling_supported = False
                self._mcp_sampling_servers.clear()
                self._mcp_sampling_requests.clear()
                if self._mcp_panel is not None and self._mcp_panel.sampling_review is not None:
                    self._mcp_panel.sampling_review.disable('Sampling disconnected; output withheld')
                self._mcp_roots_supported = False
                self._mcp_root_requests.clear()
                if self._mcp_panel is not None and self._mcp_panel.roots_review is not None:
                    self._mcp_panel.roots_review.invalidate()
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
                self._restore_pending_draft(run_id)
                self._active_text = None
                self._chat_log.add_system(
                    "drafts restored; prior tool effects may have completed — review before resending"
                )
                for tool in self._chat_log._tools.values():
                    tool.set_canceled()
                self._update_queue_summary()
            if msg_type == "error" and self._active_run_id == run_id:
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
            return

        if msg_type == "reset_ack":
            if self._pending_command != "reset":
                return
            self._pending_command = None
            self._set_waiting(False)
            self._clear_todos()
            self._clear_goal()
            # Use session ID from gateway if provided
            gateway_session_id = msg.get("session_id")
            if gateway_session_id:
                self._state["session_id"] = gateway_session_id
            else:
                self._state["session_id"] = str(uuid.uuid4())
            self._close_transcript()
            self._gateway_session_id = str(self._state["session_id"])
            self._extension_snapshot = {**self._extension_snapshot,
                                        "terminal_input": False, "terminal_generation": 0}
            self._terminal_children = {}
            self._child_terminal_owner = ""
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

        child_receiver = SourceReceiver(lambda event: self._recv_queue.put({
            **event, "type": event["type"].replace("source_", "child_source_", 1),
            "_connection_generation": generation,
        }))
        recovery_receiver = ChildRecoveryReceiver(lambda event: self._recv_queue.put({
            **event, '_connection_generation': generation}))
        mcp_receiver = McpDataReceiver(lambda event: self._recv_queue.put({
            **event, "_connection_generation": generation,
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
                    if message['type'] in {'child_recovery_begin', 'child_recovery_chunk', 'child_recovery_end'}:
                        await asyncio.to_thread(recovery_receiver.feed, message)
                        continue
                    if message['type'] == 'child_result':
                        await asyncio.to_thread(recovery_receiver.cancel, str(message.get('request_id', '')))
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
                hello = {"type": "ping", "group_id": self.group_id, "extension_forms": 1, 'child_image_return': 1,
                         "extension_multi_forms": 1, "extension_custom": 1,
                         "extension_custom_rich": 1, "extension_custom_nested": 1, "extension_custom_overlay": 1,
                         "extension_custom_live_overlay": 1,
                         "extension_command_results": 1, "extension_editor": 1, "extension_editor_write": 1,
                         "reasoning_controls": 1,
                         "extension_session": 1, "extension_navigation": 1, "extension_session_inspection": 1,
                         "extension_session_name": 1, "extension_active_tools": 1, "extension_compaction": 1,
                         "extension_compaction_after_run": 1, "extension_active_tools_after_run": 1,
                         "extension_name_after_run": 1,
                         "extension_context_messages": 1, "extension_context_delivery_modes": 1,
                "extension_message": 1, "extension_message_images": 1,
                         "extension_queued_inputs": 1, "child_extension_queued_inputs": 1,
                         "extension_provider_auth": 1,
                         "session_name_read": 1,
                         "extension_command_catalog": 1,
                         "extension_argument_completion": 1, "extension_argument_cancel": 1,
                         "extension_argument_context": 1,
                         "terminal_input": 1, "terminal_child_input": 1, "terminal_release_input": 1,
                         "mcp_content_types": 1,
                         "mcp_catalog_watch": 1, "mcp_combined_watch": 1, "mcp_roots": 1,
                         "mcp_sampling": 1, "mcp_elicitation": 1,
                         "parent_saved_drafts": 1, "parent_saved_images": 1,
                         "parent_saved_save": 1, "parent_saved_discard": 1, "parent_saved_uploads": 1,
                         "tool_inventory": 1, "mcp_private_key_jwt": 1,
                         "skill_controls": 1, "session_compaction": 1, "saved_source": 1, "saved_timeline": 1}
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
                    if request['type'] == 'child_control' and request.get('action') == 'queue':
                        command = json.loads(request['text'])
                        if command.get('action') in {'recover_images', 'recover_task', 'return_prepare'}:
                            await asyncio.to_thread(recovery_receiver.expect, {
                                'request_id': request['request_id'], 'group_id': request['group_id'],
                                'session_id': request['session_id'], 'child_id': request['child_id'],
                                'input_id': command['id'], 'queue_revision': command['revision'],
                                **({'child_return_owner': request['child_return_owner'],
                                    'queue_view': request['queue_view']}
                                   if command['action'] == 'return_prepare' else {})})
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
            await asyncio.to_thread(recovery_receiver.close)
        return disconnected


def run_tui_app(
    socket_path: str, group_id: str = "main", *, model: str = ""
) -> None:
    app = ChatApp(socket_path, group_id)
    if model:
        app._state["model"] = model
    app.run()
