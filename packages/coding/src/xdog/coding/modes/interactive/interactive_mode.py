"""Interactive mode: TUI-based coding agent interface.

Uses tui.TUI as the rendering engine and subscribes to agent.Agent
events for streaming responses, tool execution display, and session
management.

Component tree:
    TUI -> InlineLayout
        transcript -> work summary -> auxiliary panel -> status -> editor -> hints

RunStatus owns UI lifecycle and elapsed time. Queue locks, turn stamps and
worker cancellation signals protect asynchronous execution separately.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from xdog.agent import (
    AgentEndEvent,
    AgentEvent,
    InputConsumedEvent,
    InputHandledEvent,
    InputRejectedEvent,
    InputTransformedEvent,
    MessageEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
    TurnEndEvent,
)
from xdog.agent.extensions.artifact_export import ArtifactLoader, export_snapshot
from xdog.agent.extensions.command_result import ExtensionCommandResult
from xdog.agent.extensions.runtime import validate_snapshot
from xdog.agent.mcp.context import McpDocument, McpEntry
from xdog.agent.mcp.oauth import normalize_auth_reply
from xdog.agent.supervision_config import ChildConfig
from xdog.ai.types import (
    AssistantMessage,
    ImageContent,
    TextContent,
    ThinkingContent,
    UserMessage,
)
from xdog.coding.config import get_config_dir
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.child_agents import CodingChildren
from xdog.coding.core.permissions import PermissionDecision, PermissionRequest
from xdog.coding.core.slash_commands import execute_command, parse_slash_command
from xdog.coding.modes.interactive.components.chat_log import ChatLog
from xdog.coding.modes.interactive.components.custom_editor import CustomEditorComponent
from xdog.coding.modes.interactive.components.footer import FooterComponent
from xdog.coding.modes.interactive.components.permission_prompt import PermissionPromptComponent
from xdog.coding.modes.interactive.components.tool_execution import ToolExecutionComponent
from xdog.coding.modes.interactive.run_status import RunPhase, RunStatus
from xdog.coding.modes.interactive.session_presenter import SessionHistory, capture_history
from xdog.coding.modes.interactive.theme import create_default_theme
from xdog.tui.attachments import Attachment, AttachmentComposer
from xdog.tui.child_browser import ChildSourceWorker, child_entries
from xdog.tui.child_drafts import ChildDraft
from xdog.tui.components.attachment_panel import AttachmentPanel
from xdog.tui.components.child_panel import ChildPanel
from xdog.tui.components.details_panel import DetailsPanel
from xdog.tui.components.extension_command import ExtensionCommand
from xdog.tui.components.extension_dialog import ExtensionDialog
from xdog.tui.components.extension_stack import ExtensionDialogStack
from xdog.tui.components.extension_view import ExtensionView
from xdog.tui.components.inline_layout import CompactText, InlineLayout
from xdog.tui.components.mcp_panel import McpPanel
from xdog.tui.components.navigation_panel import NavigationPanel
from xdog.tui.components.output_panel import OutputPanel
from xdog.tui.components.parent_draft_panel import ParentDraftPanel
from xdog.tui.components.text import Text
from xdog.tui.components.tool_inventory import ToolInventoryPanel, inventory_records
from xdog.tui.components.transcript_panel import TranscriptPanel
from xdog.tui.event_queue import EventQueue, thaw_event
from xdog.tui.extension_shortcuts import ExtensionShortcuts
from xdog.tui.external_editor import edit_draft
from xdog.tui.file_completion import WorkspaceFileCompletionProvider
from xdog.tui.keys import KeyEvent, KeyEventType
from xdog.tui.navigation import NavigationEntry, NavigationWorker
from xdog.tui.parent_draft_presenter import ParentDraftPresenter
from xdog.tui.parent_drafts import ParentDrafts
from xdog.tui.prompt_keymap import PromptKeymap
from xdog.tui.queue_controls import QUEUE_HELP, LocalQueueIds, QueueEntry, parse_queue_command
from xdog.tui.terminal_logging import capture_terminal_logs
from xdog.tui.transcript_actions import TranscriptActions
from xdog.tui.transcript_browser import TranscriptRecord
from xdog.tui.transcript_transfer import TransferOptions, parse_transfer
from xdog.tui.tui import TUI, Container
from xdog.tui.url_open import ReviewedUrlOpener
from xdog.tui.utils import sanitize_terminal_text

logger = logging.getLogger(__name__)

# Native panel operations only; protocol completion remains available to backend callers.
_MCP_PANEL_ACTIONS = frozenset({
    "list", "connect", "disconnect", "refresh", "resources", "templates", "prompts", "read", "prompt",
})

# Waiting phrases for the shimmer animation
@dataclass(frozen=True, slots=True)
class _TurnStamp:
    generation: int
    cancel_epoch: int


@dataclass(frozen=True, slots=True)
class _CommandState:
    id: int
    name: str
    mutating: bool
    cancel_requested: bool = False


@dataclass
class _ArgumentJob:
    canceled: threading.Event
    loop: asyncio.AbstractEventLoop | None = None
    task: asyncio.Task[dict[str, Any]] | None = None
    thread: threading.Thread | None = None


def _command_is_read_only(command: str, args: str) -> bool:
    if command == "inbox":
        # This command never mutates the agent directly. Cancel/retry use the
        # independently fenced InboxCoordinator; obsolete input fails parsing.
        return True
    if command == "agents":
        return args.strip().partition(" ")[0] != "start"
    if command == "readonly":
        return args.strip() in {"", "status"}
    if command == "mcp":
        return not args.strip() or args.strip() in {"list", "status"}
    if command in {"help", "session", "sessions", "skills", "tools"}:
        return True
    return command in {"model", "thinking", "unload", "branch", "name"} and not args.strip()


_TURN_EVENT_TYPES = frozenset({
    "assistant_start",
    "text_update",
    "assistant_end",
    "tool_call",
    "tool_update",
    "tool_result",
    "permission_request",
    "turn_footer_update",
    "queued_message_started",
    "restore_draft",
    "queue_changed",
    "turn_end",
    "error",
})


WAITING_PHRASES = [
    "pondering",
    "conjuring",
    "reasoning",
    "analyzing",
    "thinking",
    "crafting",
    "exploring",
    "synthesizing",
    "contemplating",
    "processing",
]


def _context_tokens_from_messages(messages: list[Any]) -> int:
    """Tokens occupying the context window, per the latest assistant turn.

    Cached prefix tokens still occupy the window, so ``input`` alone would
    under-report; cache buckets are included.
    """
    for msg in reversed(messages):
        usage = getattr(msg, "usage", None)
        if usage is None:
            continue
        total = int(usage.input) + int(usage.cache_read) + int(usage.cache_write)
        if total > 0:
            return total
    return 0


class InteractiveMode:
    """Main interactive TUI application for the coding agent.

    Wraps AgentSession and provides a full terminal interface with:
    - Streaming message display
    - Tool execution visualization
    - Slash command handling
    - Session management
    - Ctrl+C double-press exit logic
    """

    def __init__(self, session: AgentSession, *, verbose: bool = False) -> None:
        self._session = session
        self._verbose = verbose
        self._theme = create_default_theme()

        # State
        self._exit_requested = False
        self._draft_quit_failures: tuple[int, ...] | None = None
        self._last_ctrl_c_at = 0.0
        self._run_status = RunStatus()
        self._last_busy_label = ""
        self._streaming_text = ""
        self._streaming_thinking = ""
        self._stream_sequence = 0
        self._stream_id = "assistant-0"
        self._tool_components: dict[str, ToolExecutionComponent] = {}
        self._permission_prompt: PermissionPromptComponent | None = None
        self._permission_child: tuple[CodingChildren, str] | None = None
        self._permission_alive: Callable[[], bool] | None = None
        self._permission_waiters: deque[
            tuple[PermissionRequest, CodingChildren | None, str, Callable[[], bool]]
        ] = deque()
        self._details_expanded = verbose
        self._details_open = verbose
        self._navigation_panel: NavigationPanel | None = None
        self._navigation_provider: NavigationWorker | None = None
        self._child_ui_lease = uuid.uuid4().hex
        self._child_panel: ChildPanel | None = None
        self._child_context = ""
        self._child_source_worker: ChildSourceWorker | None = None
        self._child_source_id = ""
        self._child_target = ""
        self._child_roster: dict[str, Any] = {}
        self._child_jobs: set[str] = set()
        self._mcp_auth_lease = ""
        self._mcp_auth_stop = threading.Event()
        self._mcp_auth_review: dict[str, Any] | None = None
        self._mcp_auth_copying = False
        self._reviewed_url = ReviewedUrlOpener()
        self._mcp_panel: McpPanel | None = None
        self._mcp_document: McpDocument | None = None
        self._mcp_sequence = 0
        self._mcp_active: str | None = None
        self._mcp_action = ""
        self._mcp_owner_context = ""
        self._tools_thread: threading.Thread | None = None
        self._tools_request = ''
        self._tools_context = ''
        self._mcp_thread: threading.Thread | None = None
        self._mcp_cancel: threading.Event | None = None
        from xdog.tui.saved_source import SavedSourceWorker
        self._saved_worker: SavedSourceWorker
        self._saved_scope: tuple[str, str] | None = None
        self._saved_request = ''
        self._saved_context = ''
        self._saved_records: tuple[TranscriptRecord, ...] = ()
        self._transcript_panel: TranscriptPanel | None = None
        self._attachment_panel: AttachmentPanel | None = None
        self._child_image_starting = False
        self._child_drafts: dict[str, dict[tuple[str, str], ChildDraft]] = {}
        self._parent_drafts = ParentDrafts(
            lambda: self._session.session_manager._dir / 'saved-parent-drafts', self._parent_draft_snapshot,
                                           self._mcp_context_key, lambda text: self._chat_log.add_system(text),
            self._parent_draft_capacity)
        self._parent_draft_ui = ParentDraftPresenter(
            self._parent_drafts, available=self._parent_draft_ui_available, prepare=self._prepare_parent_draft_view,
            mount=self._mount_parent_draft_view, unmount=self._unmount_parent_draft_view,
            restore=self._restore_parent_draft_composer, changed=self._parent_draft_changed)
        self._local_images: dict[str, tuple[Attachment, ...]] = {}
        self._routed_images: dict[str, tuple[Attachment, ...]] = {}
        self._active_images: tuple[Attachment, ...] = ()
        self._active_input_origin = "user"
        self._dispatch_input_origin = "user"
        self._dispatch_images: tuple[Attachment, ...] = ()
        self._dispatch_failed_message: AssistantMessage | None = None
        self._pending_messages: deque[tuple[str, bool]] = deque()
        self._pending_ids = LocalQueueIds()
        self._transformed_inputs: dict[str, str] = {}
        self._routed_inputs: dict[str, QueueEntry] = {}
        self._input_sequence = 0
        self._input_requests: dict[str, tuple[str, str, str]] = {}
        self._queue_lock = threading.Lock()
        self._worker_active = False
        self._worker_thread: threading.Thread | None = None
        self._worker_loop_ref: asyncio.AbstractEventLoop | None = None
        self._worker_task: asyncio.Task[None] | None = None
        self._command: _CommandState | None = None
        self._command_sequence = 0
        self._command_thread: threading.Thread | None = None
        self._command_loop_ref: asyncio.AbstractEventLoop | None = None
        self._command_task: asyncio.Task[None] | None = None
        self._cancel_requested = False
        self._worker_generation = 0
        self._cancel_epoch = 0
        self._active_stamp = _TurnStamp(0, 0)
        self._dispatch_stamp: _TurnStamp | None = None

        # Event queue for thread-safe UI updates from agent events
        self._event_queue = EventQueue()
        from xdog.coding.modes.interactive.session_events import NativeSessionEvents
        self._session_events = NativeSessionEvents(self)
        self._saved_worker = SavedSourceWorker(self._event_queue.put)

        # Build component tree
        self._tui = TUI()

        self._header = Text("", 1, 0)
        self._chat_log = ChatLog(self._theme)
        self._output_epoch = 0
        self._artifact_loader = ArtifactLoader(self._event_queue.put)
        self._outputs = TranscriptActions(self._event_queue.put, self._chat_log.add_system,
                                          lambda: str(self._session.session_id) + f":{self._output_epoch}:child:"
                                          + (self._child_panel.child_id if self._child_panel is not None else ""),
                                          lambda: self._tui.request_render())

        self._chat_log.set_details_expanded(False)
        self._status_container = Container()
        self._footer = FooterComponent(self._theme)
        self._keymap = PromptKeymap.load(get_config_dir() / "keybindings.json")
        self._editor = CustomEditorComponent(self._theme)
        original_commands = self._editor._command_provider
        self._editor._command_provider = lambda: {**{name: text for name, text in original_commands().items()
                                                                   if name.removeprefix("/") != "plan"},
            "readonly": "Host access scope: /readonly on|off|status (not tool approval)",
            'draft': 'Parent draft autosave [on|off] and reviewed saved recovery',
            **self._session.extensions.commands()}
        self._editor.set_keymap(self._keymap)
        self._composer = AttachmentComposer(self._event_queue.put, self._attachment_changed,
                                            self._chat_log.add_system, self._editor.handle_paste,
                                            self._open_attachment_preview)
        self._file_completions = WorkspaceFileCompletionProvider(self._session.working_dir)
        self._editor.set_autocomplete_provider(self._file_completions)
        self._argument_jobs: dict[int, _ArgumentJob] = {}
        self._argument_threads: set[threading.Thread] = set()
        self._argument_dispose_pending = False
        self._argument_jobs_lock = threading.Lock()
        from xdog.tui.argument_completion import ArgumentCompletionFeed
        self._argument_feed = ArgumentCompletionFeed(self._mcp_context_key,
            lambda: self._session.extensions.command_completions() if self._editor.focused and not self._is_busy
                and self._command is None and not self._exit_requested else {},
            self._request_arguments, self._cancel_arguments)
        self._editor.set_argument_completion_provider(self._argument_feed)
        self._message_queue_container = Container()
        self._queue_text = CompactText("", 0, 0)
        self._message_queue_container.add_child(self._queue_text)
        self._permission_container = Container()
        self._work_container = Container()
        self._extension_snapshot: dict[str, Any] = {}
        self._extension_dialog_queue: list[dict[str, Any]] = []
        self._extension_stack = ExtensionDialogStack()
        self._extension_dialog_panel: ExtensionDialog | None = None
        self._extension_view = ExtensionView(lambda: self._extension_snapshot)
        self._work_container.add_child(self._extension_view)
        self._details_panel = DetailsPanel(self._chat_log.detail_records, max_rows=8)
        self._banner_added = False

        # Compatibility handles retained for callers/tests; activity and metadata
        # now occupy one stable footer row.
        self._status_text: Text | None = None
        self._status_loader = None
        self._hints = CompactText("", 0, 0)
        self._layout = InlineLayout(
            transcript=self._chat_log,
            work=self._work_container,
            status=self._footer,
            editor=self._editor,
            permission=None,
            details=self._details_panel if self._details_open else None,
            queue=None,
            hints=self._hints,
        )
        self._tui.add_child(self._layout)
        self._tui.set_focus(self._editor)
        self._editor.set_focus(True)
        self._update_hints()
        self._tui.add_input_listener(self._handle_global_input)

        # Wire editor callbacks
        self._editor.on_submit = self._handle_submit
        self._editor.on_ctrl_c = self._handle_ctrl_c
        self._editor.on_ctrl_d = self._request_exit
        self._editor.on_escape = self._handle_escape

        # Subscribe to agent events. Permission handlers are bound per turn so
        # a canceled turn cannot surface an approval panel during its successor.
        self._unsubscribe: Callable[[], None] | None = self._session.agent.subscribe(self._on_agent_event)
        self._bind_child_approvals()


    @property
    def _is_busy(self) -> bool:
        return self._run_status.busy or self._child_image_starting

    @property
    def _busy_started(self) -> float | None:
        return self._run_status.started

    @property
    def _awaiting_permission(self) -> bool:
        return self._permission_prompt is not None or self._run_status.awaiting_permission

    def _handle_global_input(self, event: Any) -> dict[str, object] | None:
        """Handle bounded details, suspension, and active-turn cancellation."""
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

        if self._permission_prompt is not None:
            return None
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
        if action in {"steer", "follow_up", "queue"} and self._permission_prompt is None:
            if event.event_type == KeyEventType.PRESS:
                if action == "queue":
                    self._queue_command("")
                else:
                    self._submit_delivery_draft(action)
            return {"consume": True}
        if action == "paste_clipboard":
            if self._editor.focused and event.event_type == KeyEventType.PRESS:
                self._composer.command("paste", "", self._session.working_dir)
            return {"consume": True}
        if self._attachment_panel is not None and action not in {"suspend", "details", "sessions", "tree"}:
            self._attachment_panel.handle_input(event)
            self._tui.request_render()
            return {"consume": True}
        if action in {"bindings", "external_editor", "suspend", "details", "sessions", "tree"}:
            if event.event_type != KeyEventType.PRESS:
                return {"consume": True}
            if action in {"sessions", "tree"}:
                self._open_navigation("sessions" if action == "sessions" else f"tree|{self._session.session_id}|")
                return {"consume": True}
            if action == "bindings":
                self._show_keybindings()
                return {"consume": True}
            if action == "external_editor":
                self._open_external_editor()
                return {"consume": True}
        if self._editor.focused and self._editor.autocomplete_active and action not in {"suspend", "details"}:
            return None
        if self._navigation_panel is not None and action not in {"suspend", "details"}:
            if action == "interrupt":
                self._close_navigation()
            else:
                self._navigation_panel.handle_input(event)
                self._tui.request_render()
            return {"consume": True}
        if self._details_open and self._permission_prompt is None:
            if event.matches("escape") or action == "interrupt":
                self._close_details()
                return {"consume": True}
            if any(event.matches(key) for key in ("left", "right", "pageup", "pagedown", "home", "end")):
                self._details_panel.handle_input(event)
                self._tui.request_render()
                return {"consume": True}
        if event.matches("escape") and self._is_busy:
            self._handle_escape()
            return {"consume": True}
        if action == "suspend":
            if not self._tui.suspend():
                self._set_status_text("suspend is not supported on this platform")
                self._tui.request_render()
            return {"consume": True}
        if action == "details":
            if self._details_open:
                self._close_details()
            else:
                self._open_details()
            return {"consume": True}
        return None

    def _effective_extension_shortcuts(self) -> ExtensionShortcuts:
        rows = self._extension_snapshot.get("shortcuts", [])
        return ExtensionShortcuts(rows if isinstance(rows, list) else [], self._keymap)

    def _extension_shortcut_input(self, event: KeyEvent) -> bool:
        if (self._exit_requested or not self._editor.focused or self._editor.autocomplete_active
                or self._tui.has_overlay() or self._outputs.pending or self._extension_dialog_panel is not None
                or self._navigation_panel is not None or self._transcript_panel is not None
                or self._details_open or self._child_panel is not None
                 is not None or self._mcp_panel is not None or self._attachment_panel is not None):
            return False
        if self._permission_prompt is not None or self._command is not None or self._worker_active or self._is_busy:
            return False
        command = self._effective_extension_shortcuts().command(event)
        if command is None:
            return False
        self._handle_slash_command(command, "")
        return True

    def _show_keybindings(self) -> None:
        self._chat_log.add_system(self._keymap.help_text() + "\n\n" + self._effective_extension_shortcuts().help_text())
        self._tui.request_render()

    def _open_external_editor(self) -> None:
        if self._parent_draft_panel is not None:
            self._close_parent_drafts()
        if self._exit_requested:
            return
        if not self._editor.focused or self._permission_prompt is not None or self._tui.has_overlay():
            self._chat_log.add_system("Return to the prompt before opening the external editor; draft preserved")
            self._tui.request_render()
            return
        original = self._editor.get_text()

        def edit() -> None:
            result = edit_draft(
                original, cwd=self._session.working_dir,
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

    def _navigation_entries(self, scope: str) -> tuple[NavigationEntry, ...]:
        from xdog.coding.core.navigation import navigation_entries
        return navigation_entries(self._session, scope)

    def _attachment_changed(self) -> None:
        count = len(self._composer.items)
        self._editor.set_attachment_summary(
            f"Images: {count} · /attachments inspect/remove/preview" if count else "",
        )
        self._tui.request_render()

    def _retained_image_bytes(self) -> int:
        with self._queue_lock:
            local = tuple(self._local_images.values())
        items = {item.id: item for group in (*local, *self._routed_images.values(), self._active_images)
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
        try:
            self._session.validate_image_inputs(tuple((item.mime_type, len(item.data)) for item in images))
            return True
        except ValueError as exc:
            self._chat_log.add_system(str(exc) + "; images/draft kept editable")
            self._tui.request_render()
            return False

    @staticmethod
    def _image_parts(images: tuple[Attachment, ...]) -> tuple[ImageContent, ...]:
        return tuple(ImageContent(data=base64.b64encode(item.data).decode("ascii"), mime_type=item.mime_type)
                     for item in images)

    def _bind_extension_dialogs(self) -> None:
        session = self._session
        async def pending_messages() -> bool | None:
            from xdog.agent.supervision import settle_on
            with self._queue_lock:
                loop = self._worker_loop_ref if self._worker_active else self._command_loop_ref
            if loop is None:
                return None
            async def inspect() -> bool | None:
                if self._exit_requested or self._session is not session:
                    return None
                with self._queue_lock:
                    return session.agent.has_queued_messages() or bool(self._pending_messages)
            return await settle_on(loop, inspect())
        session.extensions.pending_messages = pending_messages
        from xdog.coding.modes.interactive.provider_auth import NativeProviderAuth
        self._provider_auth = NativeProviderAuth(self)
        self._session.provider_auth_handler = self._provider_auth
        session_id = self._session.session_id
        self._session.extension_dialogs.publish = lambda event: self._event_queue.put(
            {**event, "extension_session": session_id})
        self._session.extensions.command_menu_handler = self._session.extension_dialogs.ask_command_menu
        self._session.extensions.dialog_handler = self._session.extension_dialogs.ask
        self._session.extensions.dialog_cancel = self._session.extension_dialogs.cancel_extension


    def _dialog_contains(self, dialog: dict[str, Any]) -> bool:
        if dialog.get("child_id"):
            return self._session.children.extensions.contains(dialog["child_id"], dialog["id"], self._child_ui_lease)
        return self._session.extension_dialogs.contains(dialog["id"])


    def _serve_session_request(self, item: dict[str, Any]) -> None:
        value: Any = None
        command = self._command
        owner = str(command.id) if command is not None else ""
        allowed = (not item.get("child_id") and command is not None and not command.cancel_requested
                   and command.name.lower().startswith("ext-" + str(item.get("extension", "")).lower() + "-")
                   and not self._worker_active and not self._exit_requested)
        metadata = {"session_id": self._session.session_id, "workspace": str(self._session.working_dir), "group_id": ""}
        if item.get('operation') == 'commands' and 'command_menu' in item:
            from xdog.agent.extensions.command_catalog import invocation_scope
            identity = invocation_scope(item['command_menu'])
            allowed = (identity is not None and not identity['child_id'] and not item.get('child_id')
                and identity['session_id'] == self._session.session_id and not self._exit_requested
                and self._dialog_contains(item))
            owner = identity['invocation'] if identity is not None else ''
        if allowed:
            if item['operation'] == 'commands':
                from xdog.agent.extensions.command_catalog import page
                from xdog.tui.command_help import normalize_command_menu
                try:
                    value = page(normalize_command_menu(self._editor._command_provider()),
                                 {key: item.get(key, default) for key, default in
                                  (('query', ''), ('offset', 0), ('revision', ''))},
                                 session_id=self._command_session_id(), owner=owner + ':' + self._mcp_context_key())
                except (ValueError, TypeError):
                    value = None
            elif item["operation"] == "snapshot":
                value = metadata
            elif item['operation'] == 'active_tools':
                from xdog.agent.extensions.active_tools import snapshot as tool_snapshot
                try:
                    value = tool_snapshot(self._session.agent, self._session.session_id)
                except (ValueError, RuntimeError):
                    value = None
            elif item['operation'] == 'name':
                captured = self._session._extension_session_name()
                value = ({'session_id': captured['session_id'], 'name': captured['name']}
                         if captured is not None else None)
            elif item["operation"] in ("model", "usage"):
                from xdog.agent.extensions.inspection import snapshot
                try:
                    value = snapshot(self._session.agent, self._session.session_id, item["operation"],
                        self._session.model_info, context_window=self._session.context_window,
                        max_prompt_tokens=self._session.max_prompt_tokens)
                except (ValueError, TypeError):
                    value = None
        self._answer_extension_dialog(item["id"], value)


    def _command_session_id(self) -> str:
        return self._session.session_id


    def _sync_extension_dialog(self) -> None:
        self._extension_dialog_queue = [item for item in self._extension_dialog_queue
            if self._dialog_contains(item)]
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
            if (('provider_auth_host' in item or 'provider_auth_menu' in item) and not self._provider_auth.owns(item)):
                self._answer_extension_dialog(item['id'], None)
                return
            def answer(value: Any, key: str = item['id']) -> None:
                self._answer_extension_dialog(key, value)
            label = f"Child {item['child_id']} · {item['extension']}" if item.get('child_id') else item['extension']
            if 'provider_auth_host' in item:
                from xdog.tui.components.extension_provider_auth import ExtensionProviderAuth
                self._extension_dialog_panel = ExtensionProviderAuth(
                    label, item, answer, open_url=self._provider_auth.open_url)
            else:
                self._extension_dialog_panel = ExtensionDialog(label, item, answer)
            self._extension_stack.panels[item['id']] = self._extension_dialog_panel
        if self._permission_prompt is None and self._outputs.panel is None and not self._tui.has_overlay():
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
                self._restore_auxiliary_focus()


    def _answer_extension_dialog(self, key: str, value: Any) -> None:
        item = next((item for item in self._extension_dialog_queue if item['id'] == key), None)
        if item is None or not self._extension_stack.accepts(item, self._extension_dialog_queue):
            return
        if item is not None and item.get("child_id"):
            self._session.children.extensions.answer(item["child_id"], key, self._child_ui_lease, value)
        else:
            self._session.extension_dialogs.resolve(key, value)
        self._extension_dialog_queue = [item for item in self._extension_dialog_queue if item["id"] != key]
        self._extension_dialog_panel = None
        self._sync_extension_dialog()
        self._update_hints()
        self._tui.request_render()

    def _cancel_arguments(self, request: Any) -> None:
        with self._argument_jobs_lock:
            job = self._argument_jobs.get(request.serial)
            if job is None or job.canceled.is_set():
                return
            job.canceled.set()
            if job.loop is not None and job.task is not None:
                try:
                    job.loop.call_soon_threadsafe(job.task.cancel)
                except RuntimeError:
                    pass  # Already-settled owner publishes its one completion receipt.

    def _request_arguments(self, request: Any) -> None:
        captured = self._session
        job = _ArgumentJob(threading.Event())
        with self._argument_jobs_lock:
            self._argument_jobs[request.serial] = job
        def complete() -> None:
            async def query() -> dict[str, Any]:
                with self._argument_jobs_lock:
                    job.loop = asyncio.get_running_loop()
                    job.task = asyncio.current_task()
                    canceled = job.canceled.is_set()
                if canceled:
                    raise asyncio.CancelledError
                return await captured.extensions.complete_command(request.command, request.prefix, request.identity,
                                                                  argument_input=request.context())
            try:
                result = asyncio.run(query())
                rows = result['items'] if result['status'] == 'ok' else None
                status = '' if rows is not None else 'Argument registration changed; edit/Tab to retry'
            except (ValueError, RuntimeError, TimeoutError, asyncio.CancelledError):
                rows, status = None, 'Argument completion canceled/unavailable; no data replayed'
            finally:
                with self._argument_jobs_lock:
                    self._argument_jobs.pop(request.serial, None)
                    job.loop = job.task = None
                    dispose = self._argument_dispose_pending and not self._argument_jobs
                    if dispose:
                        self._argument_dispose_pending = False
                if dispose:
                    self._session.dispose()  # Cancellation settled BEFORE retiring the extension manager.
            if not self._exit_requested:
                self._event_queue.put({'type': 'argument_completion', 'request': request, 'rows': rows,
                    'status': status, 'snapshot': captured.extensions.snapshot(), 'session_token': id(captured)})
            with self._argument_jobs_lock:
                self._argument_threads.discard(threading.current_thread())
        job.thread = threading.Thread(target=complete, name='xdog-argument-completion', daemon=True)
        with self._argument_jobs_lock:
            self._argument_threads.add(job.thread)
        job.thread.start()

    def _mcp_context_key(self) -> str:
        return str(self._session.session_id) + ":" + str(self._output_epoch)

    def _open_mcp(self) -> None:
        if self._parent_draft_panel is not None:
            self._close_parent_drafts()
        if self._child_panel is not None:
            self._close_children()
        if self._exit_requested or self._permission_prompt is not None or self._tui.has_overlay():
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
        self._mcp_auth_stop = threading.Event()
        self._mcp_auth_review = None
        self._mcp_panel = McpPanel(self._mcp_send, self._close_mcp, self._mcp_insert, self._mcp_export,
                                   self._mcp_auth_copy)
        self._layout.details = self._mcp_panel
        self._tui.set_focus(self._mcp_panel)
        self._update_hints()
        self._tui.request_render()




    def _detach_mcp_auth(self) -> None:
        self._mcp_auth_stop.set()
        manager, lease = self._session.mcp, self._mcp_auth_lease
        self._mcp_auth_review = None
        if self._mcp_auth_copying and self._outputs.pending:
            self._outputs.cancel("Authorization URI copy canceled with its view")
        self._mcp_auth_copying = False
        if manager is not None and lease:
            def detach() -> None:
                try:
                    asyncio.run(manager.detach_auth(lease))
                except (Exception, asyncio.CancelledError):
                    pass  # A retired/closed manager cannot authorize a successor.
            threading.Thread(target=detach, daemon=True, name="xdog-auth-detach").start()

    def _mcp_auth_copy(self, url: str) -> None:
        panel = self._mcp_panel
        if (panel is None or panel.auth is None or panel.auth.url != url
                or self._mcp_auth_stop.is_set() or self._mcp_owner_context != self._mcp_context_key()):
            return
        self._mcp_auth_copying = True
        self._outputs.start(TransferOptions("copy", "response", "text"),
                            (TranscriptRecord("oauth-url", "assistant", "Reviewed OAuth URI", url),),
                            self._session.working_dir)

    def _close_mcp(self) -> None:
        self._reviewed_url.cancel()
        self._detach_mcp_auth()
        if self._mcp_cancel is not None:
            self._mcp_cancel.set()
        if self._mcp_panel is not None:
            self._mcp_panel.close()
        self._mcp_panel = None
        self._mcp_document = None
        self._mcp_active = None
        self._layout.details = self._transcript_panel or (self._details_panel if self._details_open else None)
        self._restore_auxiliary_focus()
        self._update_hints()
        self._tui.request_render()

    def _mcp_insert(self) -> None:
        document = self._mcp_document
        if document is None or self._mcp_owner_context != self._mcp_context_key():
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
        if document is None or self._mcp_owner_context != self._mcp_context_key():
            return
        import json
        from dataclasses import asdict

        self._outputs.start(TransferOptions("export", "all", "json"),
                            (TranscriptRecord("mcp-raw", "mcp", document.name,
                                              json.dumps(asdict(document), ensure_ascii=False)),),
                            Path(self._session.working_dir))

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

        if action == "auth_cancel":
            self._mcp_auth_stop.set()  # Stop credential commitment immediately, not after worker delivery.
        if action == "auth_leave":
            self._detach_mcp_auth()
            self._mcp_auth_lease = uuid.uuid4().hex
            self._mcp_auth_stop = threading.Event()
            return
        if self._mcp_cancel is not None:
            self._mcp_cancel.set()
        manager = self._session.mcp
        self._mcp_sequence += 1
        identifier = str(self._mcp_sequence)
        self._mcp_active, self._mcp_action = identifier, action
        if manager is None:
            self._event_queue.put({"type": "mcp_view_result", "id": identifier, "action": action,
                                   "error": "MCP manager unavailable"})
            return
        if (action in {"connect", "disconnect", "refresh"}
                and (self._is_busy or self._worker_active or self._command is not None)):
            self._event_queue.put({"type": "mcp_view_result", "id": identifier, "action": action,
                                   "error": "MCP connection/schema changes require an idle owner"})
            return
        if action in {"connect", "disconnect", "refresh"}:
            self._handle_slash_command("mcp", f"{action} {server}")
            return
        owner_session = self._session
        auth_lease, auth_stop, auth_context = self._mcp_auth_lease, self._mcp_auth_stop, self._mcp_owner_context
        def alive() -> bool:
            return (not auth_stop.is_set() and not self._exit_requested and self._session is owner_session
                    and owner_session.mcp is manager and auth_context == self._mcp_context_key()
                    and not owner_session.agent.is_streaming and not owner_session.children.supervisor.active)
        async def request() -> Any:
            if action.startswith("auth_"):
                return await owner_session.manage_mcp_auth(
                    server, action.removeprefix("auth_"), name, auth_lease, alive)
            if action in {"list", "connect", "disconnect", "refresh"}:
                await self._session.manage_mcp(action, server)
                return manager.status()
            if action in {"resources", "templates", "prompts"}:
                kind = {"resources": "resource", "templates": "template", "prompts": "prompt"}[action]
                return await manager.catalog(server, kind)
            if action == "read":
                return await manager.resource(server, name)
            return await manager.prompt(server, name, arguments)
        canceled = threading.Event()
        self._mcp_cancel = canceled
        async def owned_request() -> Any:
            task = asyncio.create_task(request())
            try:
                while not canceled.is_set():
                    done, _pending = await asyncio.wait({task}, timeout=0.025)
                    if done:
                        return task.result()
                raise asyncio.CancelledError
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        def worker() -> None:
            try:
                data = asyncio.run(owned_request())
                event = {"type": "mcp_view_result", "id": identifier, "action": action, "data": data}
            except asyncio.CancelledError:
                return
            except Exception as exc:
                event = {"type": "mcp_view_result", "id": identifier, "action": action, "error": str(exc)}
            if not self._exit_requested and not canceled.is_set():
                self._event_queue.put(event)
        self._mcp_thread = threading.Thread(target=worker, daemon=True, name="xdog-coding-mcp-view")
        self._mcp_thread.start()

    def _child_context_key(self) -> str:
        return self._mcp_context_key() + ":" + self._session.children.token

    def _open_children(self, target: str = "") -> None:
        if self._parent_draft_panel is not None:
            self._close_parent_drafts()
        if self._exit_requested or self._permission_prompt is not None or self._tui.has_overlay():
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
        self._child_context = self._child_context_key()
        self._child_drafts = {self._child_context: self._child_drafts.get(self._child_context, {})}
        self._child_target = target
        self._child_roster = {}
        self._child_source_worker = ChildSourceWorker(self._event_queue.put)
        self._child_panel = ChildPanel(self._child_send, self._child_read, self._close_children,
                                       self._child_output, self._child_fullscreen,
                                       configure=self._child_resolve_config,
                                       configured_start=self._child_configured_start,
                                       image_send=self._child_image_send, root=self._session.working_dir,
                                       drafts=self._child_drafts.setdefault(self._child_context, {}),
                                       retained_bytes=self._child_retained_bytes,
)
        self._child_panel.queue_enabled = True
        self._layout.details = self._child_panel
        self._tui.set_focus(self._child_panel)
        self._child_publish_roster()
        self._child_panel.publish_presets(self._session.children.supervisor.presets.snapshot(),
                                          self._session.children.preset_error)
        self._update_hints()
        self._tui.request_render()

    def _child_publish_roster(self) -> bool:
        panel = self._child_panel
        if panel is None:
            return False
        value = self._session.children.supervisor.snapshot()
        if value == self._child_roster:
            return False
        self._child_roster = value
        panel.publish(child_entries(value), str(value.get("error", "")))
        if self._child_target:
            target, self._child_target = self._child_target, ""
            panel.inspect(target)
        return True

    def _parent_draft_ui_available(self) -> bool:
        return (not self._exit_requested and not self._tui.has_overlay()
                and self._extension_dialog_panel is None and not self._extension_dialog_queue
                and self._outputs.panel is None and not self._outputs.pending
                and self._permission_prompt is None and not self._permission_waiters)

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
        panel = self._child_panel
        if panel is None or self._child_context != self._child_context_key():
            return
        if (len(self._child_jobs) >= 4 or self._exit_requested or action == 'start'
                and (self._command is not None or self._worker_active or self._is_busy)):
            panel.fail('Idle parent/available child control owner required; child image draft retained')
            return
        owner, session, context = self._session.children, self._session, self._child_context
        identifier = uuid.uuid4().hex
        self._child_jobs.add(identifier)
        if action == 'start':
            self._child_image_starting = True
        release = self._session_events.reserve_producer('child-image')
        def work() -> None:
            error = ''
            data: Any = None
            async def run() -> dict[str, Any]:
                typed = tuple(ImageContent(data=base64.b64encode(item.data).decode('ascii'), mime_type=item.mime_type)
                              for item in images)
                if action == 'start':
                    await session.initialize_extensions()
                    session._rebuild_system_prompt()
                    if (self._exit_requested or self._session is not session
                            or session.children is not owner or self._child_context_key() != context):
                        raise ValueError('Child image session changed before admission; no replay')
                    child_id = await owner.supervisor.start(text, access=access, config=config, images=typed)
                    return {'child_id': child_id, 'accepted_input': owner.supervisor.admission_receipt(child_id)}
                result = await owner.supervisor.control(action, child, text, images=typed)
                return {'id': child, 'accepted_input': result['accepted_input']}
            try:
                data = asyncio.run(run())
            except (Exception, asyncio.CancelledError) as exc:
                error = str(exc) or 'Child image operation canceled after settlement; inspect before retrying'
            finally:
                release()
            self._event_queue.put({'type': 'child_ui_result', 'id': identifier, 'action': action,
                                   'context': context, 'panel_owner': panel.owner_token,
                                   'image_start': action == 'start',
                                   'error': error, 'data': data})
        threading.Thread(target=work, daemon=True, name='xdog-child-image-control').start()

    def _child_resolve_config(self, value: dict[str, Any]) -> dict[str, Any]:
        return self._session.children.supervisor.presets.resolve(value).to_dict()

    def _child_configured_start(self, task: str, access: str, config: dict[str, Any]) -> None:
        import shlex
        chosen = ChildConfig.from_dict(config)
        options = ["--label", chosen.label, "--instructions", chosen.instructions]
        if chosen.preset is not None:
            options.extend(("--preset", chosen.preset))
        if chosen.model is not None:
            options.extend(("--model", chosen.model))
        if chosen.thinking is not None:
            options.extend(("--thinking", chosen.thinking))
        if chosen.tools is not None:
            options.extend(("--tools", ",".join(chosen.tools) if chosen.tools else "none"))
        if not chosen.inherit_context:
            options.append("--fresh")
        if access == "inherit":
            options.append("--inherit")
        self._child_send("start", "", shlex.join(options) + " -- " + task, "read-only")

    def _child_send(self, action: str, child: str, text: str, access: str) -> None:
        if action == 'queue':
            try:
                command = json.loads(text)
            except (ValueError, TypeError):
                return
            if not isinstance(command, dict) or command != {'action': 'list'}:
                return  # Native list-only; Agent/SDK queue management stays backend-owned.
        panel = self._child_panel
        if panel is None or self._child_context != self._child_context_key():
            return
        if action == "list":
            self._child_publish_roster()
            panel.publish(child_entries(self._child_roster), str(self._child_roster.get("error", "")))
            return
        if action == "start":
            if self._command is not None or self._worker_active or self._is_busy:
                panel.fail("Wait for the parent/command owner to become idle before starting a child")
                return
            self._handle_slash_command("agents", "start " + ("--inherit " if access == "inherit" else "") + text)
            return
        if len(self._child_jobs) >= 4:
            panel.fail("Child controls still settling; inspect/cancel when a slot is free")
            return
        identifier = uuid.uuid4().hex
        self._child_jobs.add(identifier)
        owner, context = self._session.children, self._child_context
        queue_panel = panel.queue
        release = self._session_events.reserve_producer('child-control')
        def work() -> None:
            error = ""
            data: Any = None
            try:
                data = asyncio.run(owner.supervisor.control(action, child, text))
            except (Exception, asyncio.CancelledError) as exc:
                error = str(exc) or "Child control canceled after settlement"
            finally:
                release()
            self._event_queue.put({"type": "child_ui_result", "id": identifier, "action": action,
                                   "context": context, "error": error, "data": data,
                                   'panel_owner': panel.owner_token,
                                   'queue_owner': queue_panel.owner_token if queue_panel is not None else ''})
        threading.Thread(target=work, daemon=True, name="xdog-child-control").start()

    def _child_read(self, child: str) -> None:
        if self._child_panel is None or self._child_source_worker is None:
            return
        self._child_source_id = uuid.uuid4().hex
        try:
            captured = self._session.children.supervisor.capture_source(child)
            self._child_panel.reading()
            self._child_source_worker.request(self._child_source_id, child, self._child_context, captured.to_dict)
        except Exception as exc:
            self._child_panel.source(child, (), str(exc))

    def _child_fullscreen(self) -> None:
        panel = self._child_panel.preview if self._child_panel is not None else None
        if panel is not None and self._tui.request_fullscreen(not panel.fullscreen):
            panel.fullscreen = not panel.fullscreen
            self._layout.fullscreen_view = self._child_panel if panel.fullscreen else None
            self._tui.request_render()

    def _leave_child_fullscreen(self) -> None:
        if self._child_panel is not None and self._child_panel.preview is not None:
            if self._child_panel.preview.fullscreen:
                self._child_fullscreen()

    def _close_children(self) -> None:
        self._leave_child_fullscreen()
        if self._child_source_worker is not None:
            self._child_source_worker.close()
        if self._child_panel is not None:
            self._child_panel.close()
        self._child_panel = None
        self._child_source_worker = None
        self._child_source_id = ""
        self._layout.details = None
        self._restore_auxiliary_focus()
        self._update_hints()
        self._tui.request_render()

    def _child_output(self, action: str, scope: str) -> None:
        panel = self._child_panel.preview if self._child_panel is not None else None
        if panel is None or not panel.settled or not panel.records:
            return
        self._outputs.start(TransferOptions(action, scope, "text" if action == "copy" else
                                            "html" if action == "share" else "md"), panel.records,
                            self._session.working_dir, panel.selection, panel.entry_id)

    def _open_tools(self, query: str = '') -> None:
        self._open_transcript(query, tools=True)

    def _refresh_tools(self) -> None:
        panel = self._transcript_panel
        if not isinstance(panel, ToolInventoryPanel) or self._exit_requested:
            return
        if self._tools_thread is not None and self._tools_thread.is_alive():
            return  # One bounded read; repeat refresh never spawns another worker.
        identifier = self._tools_request = uuid.uuid4().hex
        context = self._tools_context = self._mcp_context_key()
        session = self._session
        panel.begin_inventory_read()
        def read() -> None:
            try:
                snapshot = session._extension_tools()
                if snapshot is None:
                    raise ValueError('Tool inventory unavailable; no empty/approved fallback')
                records = inventory_records(snapshot)
                event = {'type': 'tools_ready', 'id': identifier, 'context': context, 'records': records}
            except (ValueError, RuntimeError, TypeError, OSError):
                event = {'type': 'tools_failed', 'id': identifier, 'context': context}
            self._event_queue.put(event)
        self._tools_thread = threading.Thread(target=read, name='xdog-coding-tools', daemon=True)
        self._tools_thread.start()

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
        if self._exit_requested or self._permission_prompt is not None or self._tui.has_overlay():
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
            self._saved_context = self._mcp_context_key()
            self._saved_records = ()
        panel_type = ToolInventoryPanel if tools else TranscriptPanel
        self._transcript_panel = panel_type((lambda: ()) if tools else
            (lambda: self._saved_records) if saved is not None else self._chat_log.transcript_records,
            self._close_transcript, self._toggle_transcript_fullscreen, query=query,
            on_refresh=self._refresh_tools if tools else self._refresh_saved if saved is not None else None,
            on_output=self._output_from_browser)
        if saved is not None:
            self._transcript_panel.caption = ('Session timeline · read-only' if saved[1] == '@timeline'
                                              else 'Saved context · read-only')
            self._refresh_saved()
        if tools:
            self._refresh_tools()
        self._layout.details = self._transcript_panel
        self._tui.set_focus(self._transcript_panel)
        self._update_hints()
        self._tui.request_render()

    def _view_navigation(self, entry: NavigationEntry) -> None:
        if entry.session_id == self._session.session_id and not entry.branch_id:
            self._open_transcript()
        else:
            self._open_transcript(saved=(entry.session_id, entry.branch_id))

    def _refresh_saved(self) -> None:
        panel, scope = self._transcript_panel, self._saved_scope
        if panel is None or scope is None or self._exit_requested:
            return
        self._saved_request = uuid.uuid4().hex
        self._saved_context = self._mcp_context_key()
        panel.replace_records(())
        panel.set_source_status('Saved source loading · no activation', busy=True)
        manager = self._session.session_manager
        live_messages = (tuple(self._session.agent.state.messages) if scope[1] == '@timeline'
                         and scope[0] == self._session.session_id else None)
        live_compaction = self._session.session_data.compaction
        live_branch = self._session.session_data.active_branch
        def load() -> tuple[TranscriptRecord, ...]:
            from xdog.coding.core.transcript_source import source_records
            return source_records(manager, scope[0], scope[1], live_messages=live_messages,
                                  live_compaction=live_compaction, live_branch=live_branch)
        self._saved_worker.request(self._saved_request, self._saved_context, load)

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
        self._saved_worker.invalidate()
        self._saved_request = ''
        self._saved_scope = None
        self._saved_records = ()
        self._tools_request = ''
        self._leave_transcript_fullscreen()
        if self._transcript_panel is not None:
            self._transcript_panel.close()
        self._transcript_panel = None
        self._layout.details = self._details_panel if self._details_open else None
        self._restore_auxiliary_focus()
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
        if panel is not None and self._permission_prompt is None and not self._tui.has_overlay():
            self._leave_child_fullscreen()
            self._leave_transcript_fullscreen()
            self._layout.details = panel
            self._tui.set_focus(panel)
        elif isinstance(self._layout.details, OutputPanel):
            self._layout.details = (self._parent_draft_panel or self._child_panel
                                       or self._mcp_panel
                                       or self._transcript_panel) or (
                self._details_panel if self._details_open else None)
            self._restore_auxiliary_focus()
        self._update_hints()

    def _load_command_artifact(self, args: str) -> None:
        import shlex
        try:
            parts = shlex.split(args)
            if len(parts) != 1:
                raise ValueError("Usage: /artifact PATH (read-only; never executes imported content)")
            path = Path(parts[0]).expanduser()
            root = self._session.working_dir
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
        self._outputs.start_snapshot(options, self._session.working_dir, prepare)

    def _begin_output(self, options: TransferOptions) -> None:
        if options.scope == "artifact":
            self._export_command_artifact(options)
            return
        panel = self._transcript_panel
        if options.scope in {"selection", "entry"} and (panel is None or not panel.settled):
            self._chat_log.add_system("Open a settled transcript and select content first")
            return
        if self._saved_scope is not None and (not panel or not panel.settled
                or self._saved_context != self._mcp_context_key()):
            self._chat_log.add_system('Wait for a current saved source before copying/exporting')
            return
        if isinstance(panel, ToolInventoryPanel) and not panel.settled:
            self._chat_log.add_system('Wait for a current configured inventory before copying/exporting')
            return
        if isinstance(panel, ToolInventoryPanel) and self._tools_context != self._mcp_context_key():
            panel.invalidate_inventory('Tool inventory owner changed; refresh explicitly')
            return
        if isinstance(panel, ToolInventoryPanel) and options.scope == 'response':
            options = replace(options, scope='entry')
        records = panel.records if panel is not None else self._chat_log.transcript_records()
        self._outputs.start(options, records, Path(self._session.working_dir),
                            panel.selection if panel is not None else None,
                            panel.entry_id if panel is not None else "")

    def _transcript_command(self, args: str) -> None:
        parts = args.split()
        if parts and parts[0] == 'timeline' and len(parts) <= 2:
            self._open_transcript(saved=(parts[1] if len(parts) == 2 else self._session.session_id, '@timeline'))
            return
        if parts and parts[0] in {'saved', 'branch'}:
            import re
            if (parts[0] == 'branch' and len(parts) == 2
                    or parts[0] == 'saved' and len(parts) in {2, 3}):
                scope = ((self._session.session_id, parts[1]) if parts[0] == 'branch'
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
        if self._permission_prompt is not None or self._tui.has_overlay():
            return
        if self._mcp_panel is not None:
            self._close_mcp()
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
        self._restore_auxiliary_focus()
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
        if self._exit_requested:
            return
        if self._permission_prompt is not None or self._tui.has_overlay():
            self._chat_log.add_system("Close the active dialog before opening session navigation")
            self._tui.request_render()
            return
        if self._mcp_panel is not None:
            self._close_mcp()
        if self._navigation_panel is not None:
            self._close_navigation()
        if self._details_open:
            self._close_details()
        self._navigation_provider = NavigationWorker(self._navigation_entries)
        self._navigation_panel = NavigationPanel(self._navigation_provider, self._activate_navigation,
                                                 self._close_navigation, scope=scope,
                                                 on_new=lambda: self._handle_slash_command("new", ""),
                                                 on_view=self._view_navigation)
        self._layout.details = self._navigation_panel
        self._tui.set_focus(self._navigation_panel)
        self._update_hints()
        self._tui.request_render()

    def _activate_navigation(self, entry: NavigationEntry) -> None:
        if entry.kind == "session":
            command, args = "resume", entry.session_id
        elif entry.kind == "branch":
            command, args = "branch", f"{entry.session_id} {entry.branch_id}"
        else:
            command, args = "goto", f"{entry.session_id} {entry.branch_id or '-'} {entry.index + 1}"
        self._handle_slash_command(command, args)

    def _close_navigation(self) -> None:
        if self._navigation_provider is not None:
            self._navigation_provider.close()
        self._navigation_provider = None
        self._navigation_panel = None
        self._layout.details = (self._details_panel if self._details_open else None)
        if self._permission_prompt is None:
            self._restore_auxiliary_focus()
        self._update_hints()
        self._tui.request_render()


    def _restore_auxiliary_focus(self) -> None:
        self._tui.set_focus(
            self._permission_prompt or self._outputs.panel
            or (self._extension_dialog_panel if self._extension_dialog_panel is not None
                else None)
            or self._parent_draft_panel or self._child_panel
                                       or self._mcp_panel
            or self._transcript_panel
            or self._attachment_panel
            or self._navigation_panel
            or (self._details_panel if self._details_open else self._editor)
        )

    def _open_details(self) -> None:
        if self._parent_draft_panel is not None:
            self._close_parent_drafts()
        if self._child_panel is not None:
            self._close_children()
        if self._mcp_panel is not None:
            self._close_mcp()
        if self._transcript_panel is not None:
            self._close_transcript()
        if self._attachment_panel is not None:
            self._close_attachment_preview()
        if self._navigation_panel is not None:
            self._close_navigation()
        self._details_open = True
        self._details_panel.show_latest(from_start=True)
        self._layout.details = self._details_panel
        self._editor.set_focus(False)
        self._tui.set_focus(self._details_panel)
        self._hints.set_text("Details focused · ←/→ entry · PgUp/PgDn scroll · End latest · Esc input")
        self._tui.request_render()

    def _close_details(self) -> None:
        self._details_open = False
        self._layout.details = None
        self._update_hints()
        self._tui.set_focus(self._editor)
        self._editor.set_focus(True)
        self._tui.request_render()

    def run(self) -> None:
        with capture_terminal_logs(self._event_queue):
            self._run_owned_terminal()

    def _run_owned_terminal(self) -> None:
        """Own the interactive session through keyboard exit, EOF, or failure."""
        if self._exit_requested:
            return
        try:
            session_events = getattr(self, "_session_events", None)
            if session_events is not None:
                session_events.start()
            self._update_header()
            self._update_footer()
            model_name = self._session.model or "unknown"
            thinking = self._session.agent.options.thinking or "off"
            self._chat_log.add_system(
                f"session:{self._session.session_id[:8]} | {model_name} | thinking:{thinking}"
            )
            self._replay_history()
            if self._keymap.error:
                self._chat_log.add_system(self._keymap.error)
            self._tui.on_tick(self._poll)
            self._tui.start()
        finally:
            try:
                self._request_exit()
            finally:
                self._session.permissions.set_request_handler(None)
                self._session.permissions.deny_all()
                if self._unsubscribe is not None:
                    self._unsubscribe()
                    self._unsubscribe = None
                events = getattr(self, '_session_events', None)
                if events is not None:
                    events.close()
                asyncio.run(self._session.children.supervisor.aclose())
                with self._argument_jobs_lock:
                    argument_threads = tuple(self._argument_threads)
                for argument_thread in argument_threads:
                    if argument_thread is not threading.current_thread():
                        # Terminal has restored; owned async cancellation is settling.
                        argument_thread.join(timeout=5.0)
                for thread in (self._worker_thread, self._command_thread):
                    if thread is not None and thread.ident is not None and thread is not threading.current_thread():
                        thread.join(timeout=2.0)

    def _replay_history(self, snapshot: SessionHistory | None = None) -> None:
        self._output_epoch += 1
        if self._transcript_panel is not None:
            self._close_transcript()
        """Replay messages, including persisted tool calls and their results."""
        from xdog.coding.core.sessions.store import InputStore
        from xdog.coding.modes.interactive.session_presenter import human_input_text
        events = self._session_events
        if snapshot is None and events.initial_history is not None:
            snapshot, events.initial_history = events.initial_history, None
        events.initial_history = None
        if snapshot is not None:
            if (snapshot.session_id != self._session.session_id
                    or snapshot.branch_id != self._session.session_data.active_branch):
                self._chat_log.add_system('History view owner changed; stale snapshot discarded')
                return
            records, messages, inbox = snapshot.records, snapshot.messages, snapshot.inbox
        else:
            runtime = self._session.runtime
            store = runtime.controller.store if runtime is not None else InputStore(self._session)
            records = tuple(row for row in store.records() if row.branch_id == self._session.session_data.active_branch)
            messages = tuple(self._session.messages)
            inbox = ()
            manager = getattr(self._session, 'session_manager', None)
            if isinstance(getattr(manager, '_dir', None), Path) and isinstance(self._session.working_dir, Path):
                from xdog.coding.core.inbox_commands import session_inbox
                disk = session_inbox(self._session)
                if (disk.root / self._session.session_id).exists():
                    inbox = disk.records(self._session.session_id, self._session.working_dir)
        events.presenter.replay(records, messages, inbox)
        for message in messages:
            if isinstance(message, UserMessage):
                text = human_input_text(message)
                if text:
                    self._editor.add_to_history(text)

    # -- Header / Footer --

    def _update_header(self) -> None:
        """Add the startup banner to durable history exactly once."""
        if self._banner_added:
            return
        model_name = self._session.model or "unknown"
        self._header.set_text(self._theme.header(f"  coding | {model_name}"))
        self._chat_log.add_child(self._header)
        self._banner_added = True

    def _update_footer(self) -> None:
        model_name = self._session.model or "unknown"
        thinking = self._session.agent.options.thinking or "off"
        self._footer.update(
            model=model_name,
            session_id=self._session.session_id,
            message_count=len(self._session.messages),
            thinking=str(thinking),
            permission_mode=self._session.permissions.mode,
            working_dir=str(self._session.working_dir),
            context_tokens=_context_tokens_from_messages(self._session.messages),
            max_context=self._session.context_limit,
        )

    # -- Status management --

    def _set_status_text(self, text: str) -> None:
        """Set the static activity portion of the single status row."""
        self._last_busy_label = text
        self._footer.set_activity(text)

    def _set_status_busy(self, message: str = "thinking...") -> None:
        """Set the busy activity portion without replacing status metadata."""
        if message == self._last_busy_label:
            return
        self._last_busy_label = message
        self._footer.set_activity(message)

    def _update_hints(self) -> None:
        if self._permission_prompt is not None or self._awaiting_permission:
            hint = "Permission focused · ↑/↓ choose · Enter confirm · Esc deny"
        elif self._outputs.panel is not None:
            hint = "Output review · Tab path · PgUp/PgDn preview · Enter saves locally · Esc cancels"
        elif self._extension_dialog_panel is not None:
            hint = "Extension dialog · Enter answers · Esc cancels · NOT tool approval"
        elif self._parent_draft_panel is not None:
            hint = "Primary draft · explicit save/restore review · p image preview · Esc cancel · NOT sent"
        elif self._child_panel is not None:
            hint = "Children focused · Enter inspect · n new · c cancel · / search · Esc parent draft"
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
        elif self._details_open:
            hint = "Details focused · ←/→ entry · PgUp/PgDn scroll · End latest · Esc input"
        elif self._command is not None and self._command.mutating:
            hint = f"Esc cancel command · draft preserved · {self._keymap.label('details')} details"
        elif self._is_busy:
            hint = (
                f"{self._keymap.label('send')} queue · {self._keymap.label('steer')} steer · "
                f"{self._keymap.label('follow_up')} follow-up · Esc cancel · /queue"
            )
        else:
            hint = (
                f"{self._keymap.label('send')} send · {self._keymap.label('newline')} newline · "
                f"{self._keymap.label('details')} details · {self._keymap.label('external_editor')} editor · /keys"
            )
        if self._editor.focused and self._session.access.state.read_only:
            hint = "READ ONLY · " + hint
        self._hints.set_text(hint)

    def _activity_label(self) -> str:
        if self._command is not None and self._command.mutating:
            return f"running /{self._command.name}"
        if self._tool_components:
            names = [tool.detail_title for tool in self._tool_components.values()]
            return "running " + names[0] + (f" +{len(names) - 1}" if len(names) > 1 else "")
        return self._run_status.phase.value

    def _set_busy(self, busy: bool) -> None:
        if busy:
            self._run_status = self._run_status.start(time.monotonic())
            self._set_status_busy("waiting for response")
        else:
            self._run_status = self._run_status.finish()
            for tool in self._tool_components.values():
                tool.set_canceled()
            self._tool_components.clear()
            if self._permission_child is None:
                self._permission_container.clear()
                self._layout.permission = None
                self._permission_prompt = None
                self._restore_auxiliary_focus()
            self._set_status_text("awaiting child tool permission" if self._permission_child else "ready")
        self._update_hints()

    def _format_elapsed(self) -> str:
        if self._busy_started is None:
            return "0s"
        total = max(0, int(time.monotonic() - self._busy_started))
        if total < 60:
            return f"{total}s"
        m, s = divmod(total, 60)
        return f"{m}m {s}s"

    # -- Input handling --

    def _handle_submit(self, text: str) -> None:
        if getattr(self._session, "_auth_changing", False) is True:
            self._preserve_input(text, "authorization operation is still settling; draft preserved")
            return
        """Handle editor submission."""
        value = text.strip()
        if not value and not self._composer.items:
            return

        self._editor.add_to_history(value)
        parsed = parse_slash_command(value)
        session_events = getattr(self, "_session_events", None)
        if session_events is not None and (parsed is None or parsed[0] != 'inbox'):
            session_events.human_input()

        # Inbox queries never grant credit; approvals grant only their selected IDs.
        # Other human input retains the existing automatic-turn budget behavior.
        if parsed is not None:
            cmd, args = parsed
            self._handle_slash_command(cmd, args)
            return

        # Typed images are detached only after capability validation.
        images = tuple(self._composer.items.values())
        if not self._validate_draft_images(images):
            self._preserve_input(value, "Submission not sent")
            return
        self._composer.take()
        self._start_turn(value, images=images)

    def _queue_entries(self) -> tuple[QueueEntry, ...]:
        with self._queue_lock:
            ids = self._pending_ids.sync(len(self._pending_messages))
            local = tuple(QueueEntry(key, text, "next_turn")
                          for key, (text, _echo) in zip(ids, self._pending_messages))
        return (*local, *self._routed_inputs.values())

    def _queued_image_label(self, key: str) -> str:
        with self._queue_lock:
            count = len(self._local_images.get(key, ()))
        count += len(self._routed_images.get(key, ()))
        return f" · images: {count}" if count else ""

    def _queue_command(self, args: str) -> None:
        try:
            command = parse_queue_command(args)
        except ValueError as exc:
            self._chat_log.add_system(str(exc))
            self._tui.request_render()
            return
        entries = self._queue_entries()
        if command.action == "list":
            rows = [f"Queue ({len(entries)}):", QUEUE_HELP]
            rows.extend(f"{entry.id} [{entry.delivery} · {entry.state}{self._queued_image_label(entry.id)}] "
                        f"{entry.content}" for entry in entries)
            self._chat_log.add_system("\n".join(rows))
        elif self._session_events.change(command.id, command.action, command.content, command.direction):
            pass  # Receipt and SessionEvents update the display mirror.
        else:
            with self._queue_lock:
                ids = self._pending_ids.sync(len(self._pending_messages))
                position = ids.index(command.id) if command.id in ids else None
                if position is not None:
                    values = list(self._pending_messages)
                    keys = list(ids)
                    if command.action == "edit":
                        values[position] = (command.content, values[position][1])
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
            if position is None:
                if command.id not in self._routed_inputs:
                    self._chat_log.add_system("Queue item no longer pending; inspect /queue again")
                else:
                    self._runtime_input_request(command.action, command.id, command.content, command.direction)
            else:
                self._chat_log.add_system(f"Queue {command.id}: {command.action} applied")
        self._update_message_queue()
        self._tui.request_render()

    def _submit_delivery_draft(self, delivery: str) -> None:
        if not self._editor.focused or self._permission_prompt is not None:
            return
        content = self._editor.get_text().strip()
        if content or self._composer.items:
            self._editor.set_text("")
            self._send_runtime_input(delivery, content)

    def _send_runtime_input(self, delivery: str, content: str) -> None:
        if not content.strip() and not self._composer.items:
            self._chat_log.add_system(f"/{'steer' if delivery == 'steer' else 'follow-up'} requires text")
            self._tui.request_render()
            return
        images = tuple(self._composer.items.values())
        if not self._validate_draft_images(images):
            self._preserve_input(content, "Explicit delivery not sent")
            return
        self._composer.take()
        self._input_sequence += 1
        input_id = f"i{self._input_sequence}"
        self._routed_images[input_id] = images
        self._routed_inputs[input_id] = QueueEntry(input_id, content, delivery, "sending", str(self._worker_generation))
        self._runtime_input_request("add", input_id, content, delivery=delivery)

    def _runtime_input_request(
        self, action: str, input_id: str, content: str = "", direction: int = 0, *, delivery: str = "",
    ) -> None:
        with self._queue_lock:
            loop = self._worker_loop_ref
            generation = self._worker_generation
            active = self._worker_active and not self._cancel_requested and not self._exit_requested
        if loop is None or not active:
            if action == "add":
                self._routed_inputs.pop(input_id, None)
                self._composer.restore(self._routed_images.pop(input_id, ()))
                self._preserve_input(content, "No ready active turn; explicit input kept in draft")
            else:
                self._chat_log.add_system("Input owner is settling; wait for queue receipt")
                self._tui.request_render()
            return
        request_id = f"c{len(self._input_requests)}-{time.monotonic_ns()}"
        self._input_requests[request_id] = (action, input_id, content)

        queued_images = self._routed_images.get(input_id, ())
        def apply() -> None:
            success = False
            error = "Input owner settled or canceled"
            snapshot: list[dict[str, str]] = []
            try:
                with self._queue_lock:
                    valid = self._worker_active and self._worker_generation == generation and not self._cancel_requested
                if valid and not self._exit_requested:
                    agent = self._session.agent
                    if action == "add":
                        if len(agent.pending_inputs()) >= 50 or len(content.encode("utf-8")) > 64 * 1024:
                            raise ValueError("Runtime queue is full or input exceeds 64 KiB")
                        from typing import cast

                        from xdog.agent import Delivery
                        self._session.validate_image_inputs(
                            tuple((item.mime_type, len(item.data)) for item in queued_images)
                        )
                        agent.queue_input(input_id, content, cast(Delivery, delivery),
                                          self._image_parts(queued_images))
                        success = True
                    else:
                        success = agent.change_input(input_id, action, content, direction)
                    snapshot = [dict(id=item.id, content=item.content, delivery=item.delivery)
                                for item in agent.pending_inputs()]
                    error = "Input already selected/consumed or at queue boundary"
            except Exception as exc:
                error = str(exc)
            self._event_queue.put({"type": "input_ack", "request_id": request_id, "success": success,
                                   "error": error, "snapshot": snapshot, "generation": generation})

        try:
            loop.call_soon_threadsafe(apply)
        except RuntimeError:
            self._event_queue.put({"type": "input_ack", "request_id": request_id, "success": False,
                                   "error": "Input owner closed", "snapshot": [], "generation": generation})
        self._update_message_queue()
        self._tui.request_render()

    def _handle_input_event(self, event: Mapping[str, Any]) -> None:
        kind = event["type"]
        if kind == "input_ack":
            request = self._input_requests.pop(str(event.get("request_id", "")), None)
            if request is None:
                return
            action, input_id, content = request
            if not event.get("success"):
                if action == "add" and self._routed_inputs.pop(input_id, None) is not None:
                    self._composer.restore(self._routed_images.pop(input_id, ()))
                    self._preserve_input(content, str(event.get("error", "Input rejected")))
                elif action == "edit":
                    self._preserve_input(content, "Edit was not applied; replacement text kept in draft")
                else:
                    self._chat_log.add_system(str(event.get("error", "Input rejected")))
            elif action == "remove":
                self._routed_inputs.pop(input_id, None)
                self._routed_images.pop(input_id, None)
            else:
                ordered: dict[str, QueueEntry] = {}
                for raw in event.get("snapshot", ()):
                    key = str(raw["id"])
                    previous = self._routed_inputs.get(key)
                    if previous is not None:
                        ordered[key] = QueueEntry(
                            key, str(raw["content"]), str(raw["delivery"]), "accepted", previous.owner,
                        )
                # Do not lose concurrently submitted, not-yet-acknowledged inputs.
                self._routed_inputs = {**ordered, **{key: value for key, value in self._routed_inputs.items()
                                                   if key not in ordered}}
                self._chat_log.add_system(f"Input {input_id}: {action} accepted (not proof of consumption)")
        elif kind == "input_consumed":
            input_id = str(event["id"])
            item = self._routed_inputs.pop(input_id, None)
            if item is not None:
                images = self._routed_images.pop(input_id, ())
                self._finalize_streaming_assistant()
                text = self._transformed_inputs.pop(input_id, str(event["content"]))
                self._chat_log.add_user(text + (f"\n[Images: {len(images)}]" if images else ""))
                self._editor.add_to_history(text)
        elif kind == "inputs_settled":
            texts = []
            for raw in event.get("inputs", ()):
                self._transformed_inputs.pop(str(raw["id"]), None)
                item = self._routed_inputs.pop(str(raw["id"]), None)
                if item is not None:
                    images = self._routed_images.pop(item.id, ())
                    self._composer.restore(images)
                    texts.append(str(raw["content"]))
            if texts:
                self._restore_editor_messages(texts)
                self._chat_log.add_system("Unconsumed runtime inputs restored; not automatically replayed")
        self._update_message_queue()
        self._tui.request_render()

    def _update_message_queue(self) -> None:
        """Bounded previews include local and acknowledged runtime queues."""
        entries = self._queue_entries()
        self._footer.set_queue_count(len(entries))
        if not entries:
            self._queue_text.set_text("")
            self._layout.queue = None
            return
        previews = []
        for item in entries[:2]:
            preview = sanitize_terminal_text(item.content).replace("\n", " ↵ ")
            if len(preview) > 120:
                preview = preview[:120] + "…"
            previews.append(
                f"  {item.id} [{item.delivery} · {item.state}{self._queued_image_label(item.id)}] {preview}"
            )
        text = f"Queued messages ({len(entries)}):\n" + "\n".join(previews)
        if len(entries) > 2:
            text += f"\n  … {len(entries) - 2} more queued"
        self._queue_text.set_text(self._theme.system(text))
        self._layout.queue = self._message_queue_container

    def _start_turn(self, message: str, *, echo: bool = True, images: tuple[Attachment, ...] = (),
                    input_origin: str = 'user') -> None:
        """Start immediately or enqueue behind the active worker."""
        if self._exit_requested:
            return
        if input_origin != 'user' and (self._worker_active or self._child_image_starting
                or self._command is not None and self._command.mutating):
            self._chat_log.add_system('Extension input owner changed; not copied into the user draft, no replay')
            self._tui.request_render()
            return
        if self._child_image_starting:
            self._composer.restore(images)
            self._preserve_input(message, 'Child image admission still settling; parent draft preserved')
            return
        if self._command is not None and self._command.mutating:
            self._composer.restore(images)
            self._preserve_input(message, f"wait for /{self._command.name}; message kept in draft")
            return
        with self._queue_lock:
            full = self._worker_active and len(self._pending_messages) >= 50
        if full:
            self._composer.restore(images)
            self._preserve_input(message, "Local queue is full (50); message kept in draft")
            return
        events = self._session_events
        if not self._worker_active:
            self._worker_generation += 1
            self._active_stamp = _TurnStamp(self._worker_generation, self._cancel_epoch)
        self._worker_active = True
        self._cancel_requested = False
        self._set_busy(True)
        try:
            events.enqueue(message, images, input_origin, echo=echo)
        except Exception as exc:
            self._composer.restore(images)
            self._preserve_input(message, f"Input not admitted: {exc}")
            self._worker_active = False
            self._set_busy(False)
        self._tui.request_render()

    def _preserve_input(self, value: str, notice: str) -> None:
        draft = self._editor.get_text()
        if draft != value:
            self._editor.set_text("\n\n".join(text for text in (value, draft) if text.strip()))
        self._chat_log.add_system(notice)
        self._tui.request_render()

    def _handle_slash_command(self, cmd: str, args: str) -> None:
        if self._exit_requested:
            return
        if cmd == 'plan':
            self._chat_log.add_system('Planning mode UI was removed; use /readonly on|off|status')
            self._tui.request_render()
            return
        if cmd == 'readonly':
            if args.strip() not in {'', 'status', 'on', 'off'}:
                self._chat_log.add_system('Usage: /readonly on|off|status')
                return
            if (self._permission_prompt is not None or self._permission_waiters or self._tui.has_overlay()
                    or self._extension_dialog_panel is not None or self._extension_dialog_queue):
                self._chat_log.add_system('Another protected owner has input; access control not admitted')
                return
        if cmd == 'login':
            from xdog.agent.extensions.provider_oauth_host import login_arguments
            try:
                cmd, args = 'extensions', 'auth ' + login_arguments(args)
            except ValueError as exc:
                self._chat_log.add_system(str(exc))
                self._tui.request_render()
                return
        if cmd == "compact" and args.strip() == "cancel":
            if self._command is not None and self._command.name == "compact":
                self._cancel_command()
            else:
                self._chat_log.add_system("No compaction command is active")
                self._tui.request_render()
            return
        if cmd == 'draft':
            self._parent_draft_command(args)
            return
        if cmd == "tools":
            self._open_tools(args)
            return
        if cmd == "artifact":
            self._load_command_artifact(args)
            return

        """Serialize session commands; mutations require an idle agent."""
        if self._exit_requested:
            return
        if cmd == 'agents' and args.strip().partition(' ')[0] == 'queue':
            self._chat_log.add_system('Child queue controls were removed; /agents opens read-only inspection')
            self._tui.request_render()
            return
        if cmd == 'agents' and args.strip().partition(' ')[0] == 'saved':
            self._chat_log.add_system('Child draft disk management was removed; '
                                      '/agents drafts recovers this session only')
            self._tui.request_render()
            return
        if cmd == 'agents' and args.strip() == 'drafts':
            self._open_children()
            if self._child_panel is not None:
                self._child_panel.open_drafts()
                self._tui.request_render()
            return
        if cmd == "agents" and args.strip() == "presets":
            self._open_children()
            if self._child_panel is not None:
                self._child_panel.open_presets()
            return
        if cmd == "agents" and args.strip() in {"", "list", "browse"}:
            self._open_children()
            return
        if cmd == "agents" and args.strip().startswith("inspect "):
            self._open_children(args.strip().partition(" ")[2])
            return
        if cmd == "mcp" and args.strip().startswith("authorize "):
            self._open_mcp()
            if self._mcp_panel is not None:
                self._mcp_panel.authorize_server(args.strip().partition(" ")[2])
            return
        if cmd == "mcp" and args.strip() in {"", "browse"}:
            self._open_mcp()
            return
        if cmd in {"copy", "export", "share"}:
            self._output_command(cmd, args)
            return
        if cmd in {"transcript", "fullscreen"}:
            self._transcript_command("fullscreen" if cmd == "fullscreen" else args)
            return
        if cmd in ("quit", "exit"):
            self._request_exit()
            return
        if cmd in {"attach", "attachments", "paste"}:
            self._composer.command(cmd, args, self._session.working_dir)
            return
        if cmd in {"sessions", "tree"}:
            self._open_navigation("sessions" if cmd == "sessions" else f"tree|{self._session.session_id}|")
            return
        if cmd == "queue":
            self._queue_command(args)
            return
        if cmd in {"steer", "follow-up"}:
            self._send_runtime_input("steer" if cmd == "steer" else "follow_up", args)
            return
        if cmd == "keys":
            self._show_keybindings()
            return
        if cmd == "editor":
            self._open_external_editor()
            return
        if cmd == 'help':
            from xdog.tui.command_help import command_help
            try:
                text = command_help(self._editor._command_provider(), args)
            except (ValueError, TypeError) as exc:
                text = f'Help unavailable: {exc}'
            self._chat_log.add_system(text)
            self._tui.request_render()
            return
        if cmd == "details":
            if args.strip() == "close":
                self._close_details()
            else:
                self._open_details()
            return
        if cmd == "status":
            self._update_footer()
            self._chat_log.add_system(self._footer.describe())
            self._tui.request_render()
            return

        value = f"/{cmd}" + (f" {args}" if args else "")
        if self._command is not None:
            self._preserve_input(value, f"wait for /{self._command.name}; command kept in draft")
            return
        mutating = not _command_is_read_only(cmd, args)
        with self._queue_lock:
            worker_active = self._worker_active
        if mutating and (worker_active or self._is_busy):
            self._preserve_input(value, "session changes require an idle agent; command kept in draft")
            return

        self._command_sequence += 1
        command = _CommandState(self._command_sequence, cmd, mutating)
        self._command = command
        if mutating:
            with self._queue_lock:
                self._cancel_epoch += 1
                self._active_stamp = _TurnStamp(self._worker_generation, self._cancel_epoch)
            self._run_status = self._run_status.start(time.monotonic()).advance(RunPhase.COMMAND)
            self._set_status_busy(f"running /{cmd} • 0s")
            self._update_hints()
            self._tui.request_render()
        self._command_thread = threading.Thread(target=self._run_command, args=(command, args), daemon=True)
        self._command_thread.start()

    def _run_command(self, command: _CommandState, args: str) -> None:
        session_id = self._session.session_id
        captured = self._session
        context = self._mcp_context_key()
        previous_navigation_guard = captured._navigation_alive
        def navigation_guard() -> bool:
            return (not self._exit_requested and self._session is captured and self._command is not None
                    and self._command.id == command.id and not self._command.cancel_requested)
        captured._navigation_alive = navigation_guard
        async def catalog(extension: str, request: dict[str, Any]) -> dict[str, Any] | None:
            from dataclasses import asdict

            from xdog.agent.async_work import run_owned
            from xdog.agent.extensions.catalog import page
            def alive() -> bool:
                return (not self._exit_requested and self._session is captured and self._command is not None
                        and self._command.id == command.id and not self._command.cancel_requested
                        and command.name.lower().startswith("ext-" + extension.lower() + "-")
                        and context == self._mcp_context_key() and not self._worker_active)
            if not alive():
                return None
            scope = ("sessions" if request["scope"] == "sessions" else
                     "tree|" + (request["session_id"] or session_id) + "|" + request["branch_id"])
            entries = await run_owned(self._navigation_entries, scope)
            result = await run_owned(page, (asdict(entry) for entry in entries), request, session_id)
            return result if alive() else None
        captured.extensions.catalog_handler = catalog
        self._bind_extension_dialogs()
        self._session.extensions.publish = lambda event: self._event_queue.put(
            {**event, "extension_session": session_id})
        async def owned_command() -> dict[str, Any]:
            with self._queue_lock:
                current = self._command
                if self._exit_requested or current is None or current.id != command.id or current.cancel_requested:
                    raise asyncio.CancelledError
                self._command_loop_ref = asyncio.get_running_loop()
                self._command_task = asyncio.current_task()
            if command.mutating:
                command_stamp = self._active_stamp
                captured.permissions.set_request_handler(
                    lambda request: self._put_turn_event('permission_request', stamp=command_stamp, request=request))
            try:
                changes_history = command.mutating and command.name in {
                    "clear", "compact", "branch", "goto", "resume", "new",
                }
                before = self._session.messages if changes_history else []
                result = await execute_command(command.name, args, self._session)
                changed = changes_history and (
                    command.name in {"clear", "resume", "new", "goto", "branch"} or before != self._session.messages
                )
                return {
                    "output": result.output, "exit": result.exit_requested,
                    "prompt": result.prompt, "history_changed": changed,
                    "history_snapshot": (await capture_history(captured)
                                         if changed and captured.runtime is not None else None),
                    "rich_output": result.rich_output, "owner_session": session_id,
                    "extension_snapshot": (captured.extensions.snapshot()
                                           if command.name == "extensions" or command.name.startswith("ext-")
                                           else None),
                }
            finally:
                with self._queue_lock:
                    self._command_loop_ref = None
                    self._command_task = None

        try:
            runtime = getattr(captured, 'runtime', None)
            inbox_read = command.name == 'inbox' and not command.mutating
            payload = (runtime.controller.control(owned_command, name=command.name).result()
                       if runtime is not None and not inbox_read else asyncio.run(owned_command()))
        except asyncio.CancelledError:
            notice = ("; an admitted commit may have completed — inspect /transcript before retrying"
                      if command.name == "compact" else
                      "; an admitted name save may have completed — inspect /name before retrying"
                      if command.name == "name" else "")
            payload = {"output": f"Cancelled /{command.name}" + notice, "cancelled": True}
        except Exception as exc:
            payload = {"output": f"Error running /{command.name}: {exc}", "failed": True}
        if (command.name == 'extensions' or command.name.startswith('ext-')) and (
                captured is self._session and captured.session_id == session_id and not captured.extensions._closed):
            payload.update(extension_snapshot=captured.extensions.snapshot(), owner_session=session_id)
        if captured._navigation_alive is navigation_guard:
            captured._navigation_alive = previous_navigation_guard
        if not self._exit_requested:
            self._event_queue.put({"type": "command_result", "command_id": command.id, **payload})

    def _cancel_command(self) -> None:
        command = self._command
        if command is None or command.cancel_requested:
            return
        self._command = replace(command, cancel_requested=True)
        with self._queue_lock:
            loop, task = self._command_loop_ref, self._command_task
        if loop is not None and task is not None:
            try:
                loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:
                pass
        self._run_status = self._run_status.advance(RunPhase.CANCELLING)
        self._set_status_text(f"cancelling /{command.name}...")
        self._tui.request_render()

    def _handle_ctrl_c(self) -> None:
        """Ctrl+C: clear input → warn → exit on double-press."""
        now = time.monotonic()
        has_input = len(self._editor.get_text().strip()) > 0

        if has_input:
            self._editor.set_text("")
            self._set_status_text("input cleared; press ctrl+c again to exit")
            self._last_ctrl_c_at = now
            self._tui.request_render()
            return

        if self._is_busy:
            self._cancel_active_work()
            self._last_ctrl_c_at = now
            return

        if now - self._last_ctrl_c_at <= 1.0:
            self._request_exit()
        else:
            self._set_status_text("press ctrl+c again to exit")
            self._last_ctrl_c_at = now
            self._tui.request_render()

    def _cancel_active_work(self) -> None:
        """Cancel active work and restore queued messages to the editor."""
        if self._command is not None and self._command.mutating:
            self._cancel_command()
            return
        with self._queue_lock:
            if self._cancel_requested:
                return
            self._cancel_requested = True
            self._cancel_epoch += 1
            self._active_stamp = _TurnStamp(
                self._worker_generation,
                self._cancel_epoch,
            )
            queued = list(self._pending_messages)
            self._pending_messages.clear()
            self._pending_ids.clear()
            recovered_images = tuple(image for group in self._local_images.values() for image in group)
            self._local_images.clear()

        self._composer.restore(recovered_images)
        queued_text = "\n\n".join(message for message, _echo in queued)
        draft = self._editor.get_text()
        restored = "\n\n".join(text for text in (queued_text, draft) if text.strip())
        if restored:
            self._editor.set_text(restored)

        for component in self._tool_components.values():
            component.set_canceled()
        self._tool_components.clear()

        self._session_events.cancel_local()
        self._session.cancel(preserve_identified=True)
        self._permission_waiters.clear()
        self._permission_child = None
        self._permission_container.clear()
        self._layout.permission = None
        self._permission_prompt = None
        self._run_status = self._run_status.advance(RunPhase.CANCELLING)
        self._tui.set_focus(self._editor)
        self._update_message_queue()
        self._chat_log.drop_assistant(self._stream_id)
        self._set_status_text("cancelling active turn...")
        self._hints.set_text("Cancelling · draft preserved · waiting for worker to stop")
        notice = "cancelling active turn"
        if queued:
            notice += f"; restored {len(queued)} queued message(s) to editor"
        self._chat_log.add_system(notice)
        self._tui.request_render()

    def _handle_escape(self) -> None:
        """Escape cancels the active model turn or running tool task."""
        if self._attachment_panel is not None:
            self._close_attachment_preview()
            return
        if self._navigation_panel is not None:
            self._close_navigation()
            return
        if self._details_open:
            self._close_details()
            return
        if self._is_busy:
            self._cancel_active_work()

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
        """Stop once, cancel owned async work, and fail closed on approvals."""
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
        if hasattr(self, "_argument_feed"):
            self._argument_feed.close()
        self._saved_worker.close()
        if self._child_panel is not None:
            self._close_children()
        self._artifact_loader.close()
        self._file_completions.close()
        self._composer.close()
        self._session.children.extensions.detach(self._child_ui_lease)
        self._session.extension_dialogs.close()
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
        with self._queue_lock:
            self._cancel_requested = True
            self._cancel_epoch += 1
            self._active_stamp = _TurnStamp(self._worker_generation, self._cancel_epoch)
            self._pending_messages.clear()
            self._pending_ids.clear()
            owners = (
                (self._worker_loop_ref, self._worker_task),
                (self._command_loop_ref, self._command_task),
            )
        self._tui.stop()
        for loop, task in owners:
            if loop is not None and task is not None:
                try:
                    loop.call_soon_threadsafe(task.cancel)
                except RuntimeError:
                    pass  # Owner already settled and closed its loop.
        try:
            self._session.cancel()
        finally:
            with self._argument_jobs_lock:
                defer = bool(self._argument_jobs)
                self._argument_dispose_pending = defer
            if not defer:
                self._session.dispose()

    # -- Agent execution --

    async def _async_agent_turn(self, message: str, images: tuple[Attachment, ...] = (),
                                *, input_origin: str = 'user') -> None:
        """Submit prepared native input; do not duplicate the session's turn loop."""
        stamp = self._active_stamp
        self._dispatch_stamp = stamp
        self._dispatch_images = images
        self._dispatch_input_origin = input_origin
        self._dispatch_failed_message = None
        try:
            events = getattr(self, '_session_events', None)
            if events is not None:
                result = await events.submit(message, images, input_origin)
                if result.cause is not None:
                    raise result.cause
                if result.status == 'cancelled':
                    raise asyncio.CancelledError
            else:
                # Minimal embedded/native adapters still use the shared SDK owner.
                await self._session.send_message(message, images=self._image_parts(images), input_origin=input_origin)
        finally:
            undelivered = [dict(id=item.id, content=item.content) for item in self._session.agent.drain_inputs()]
            if self._cancel_requested or self._exit_requested:
                self._session.agent.clear_all_queues()
            if undelivered:
                self._event_queue.put({"type": "inputs_settled", "inputs": undelivered})
            self._session.agent.drain_handled_inputs()
            self._dispatch_stamp = None
            self._dispatch_images = ()

    def _emit_agent_ui_event(self, event: Mapping[str, Any]) -> None:
        sink = getattr(self, '_turn_event_sink', None)
        if sink is None:
            self._event_queue.put(event)
        else:
            sink(event)

    def _put_turn_event(self, event_type: str, *, stamp: _TurnStamp | None = None, **payload: Any) -> None:
        dispatch_stamp = getattr(self, "_dispatch_stamp", None)
        stamp = stamp or dispatch_stamp or getattr(self, "_active_stamp", _TurnStamp(0, 0))
        self._emit_agent_ui_event({
            "type": event_type,
            "stamp": stamp,
            **payload,
        })

    def _dispatch_agent_event(self, event: AgentEvent) -> None:
        """Convert agent events to stamped UI update events."""
        if isinstance(event, (InputTransformedEvent, InputRejectedEvent, InputHandledEvent)):
            parts = event.original.content
            original = (parts if isinstance(parts, str) else
                        "\n".join(p.text for p in parts if isinstance(p, TextContent)))
            data: dict[str, Any] = {"type": event.type, "original": original, "source": event.source,
                                   "id": event.input_id, "images": self._dispatch_images,
                                    "input_origin": self._dispatch_input_origin}
            if isinstance(event, InputHandledEvent):
                data["feedback"] = event.feedback
            if isinstance(event, InputTransformedEvent):
                changed = event.message.content
                data["content"] = (changed if isinstance(changed, str) else
                                   "\n".join(p.text for p in changed if isinstance(p, TextContent)))
            self._emit_agent_ui_event(data)
        elif isinstance(event, InputConsumedEvent):
            self._emit_agent_ui_event({"type": "input_consumed", "id": event.input.id, "content": event.input.content})
        elif isinstance(event, MessageStartEvent):
            if isinstance(event.message, AssistantMessage):
                self._put_turn_event("assistant_start")

        elif isinstance(event, (MessageUpdateEvent, MessageEndEvent)):
            # Some providers emit no incremental text event and deliver the
            # complete response only in MessageEndEvent. Handling both keeps
            # ordinary assistant text visible for streaming and non-streaming
            # response paths.
            msg = event.message
            if isinstance(msg, AssistantMessage):
                if isinstance(event, MessageEndEvent) and msg.stop_reason in {"error", "aborted"}:
                    self._dispatch_failed_message = msg
                    text, thinking = _assistant_content(msg)
                    self._put_turn_event(
                        "error",
                        message=msg.error_message or ("Provider canceled" if msg.stop_reason == "aborted"
                                                      else "Provider request failed"),
                        retain_partial=True, partial_text=text, partial_thinking=thinking,
                        canceled=msg.stop_reason == "aborted",
                    )
                    return
                text, thinking = _assistant_content(msg)
                if text or thinking:
                    self._put_turn_event(
                        "text_update",
                        text=text,
                        thinking=thinking,
                    )
                if isinstance(event, MessageEndEvent):
                    self._put_turn_event("assistant_end")

        elif isinstance(event, AgentEndEvent):
            final = event.messages[-1] if event.messages else None
            if (isinstance(final, AssistantMessage) and final.stop_reason in {"error", "aborted"}
                    and getattr(self, "_dispatch_failed_message", None) is not final):
                self._put_turn_event(
                    "error",
                    message=final.error_message or "Provider request failed",
                    canceled=final.stop_reason == "aborted",
                )
            self._dispatch_failed_message = None

        elif isinstance(event, TurnEndEvent):
            # One user prompt can contain several model/tool turns. Refresh the
            # footer after each one so message count and context usage do not
            # remain stale until the entire agent loop finishes.
            self._put_turn_event("turn_footer_update")

        elif isinstance(event, ToolExecutionStartEvent):
            self._put_turn_event(
                "tool_call",
                id=event.tool_call_id,
                name=event.tool_name,
                arguments=event.args,
            )

        elif isinstance(event, ToolExecutionUpdateEvent):
            if event.partial_result is not None:
                update_parts: list[str] = []
                for update_part in event.partial_result.content:
                    if isinstance(update_part, TextContent):
                        update_parts.append(update_part.text)
                if update_parts:
                    self._put_turn_event(
                        "tool_update",
                        id=event.tool_call_id,
                        name=event.tool_name,
                        text="".join(update_parts),
                    )

        elif isinstance(event, ToolExecutionEndEvent):
            result_text = ""
            if event.result is not None:
                result_text = "\n".join(
                    result_part.text
                    for result_part in event.result.content
                    if isinstance(result_part, TextContent)
                )
            is_error = event.is_error or result_text.startswith("Error:")
            self._put_turn_event(
                "tool_result",
                id=event.tool_call_id,
                name=event.tool_name,
                result=result_text,
                content=event.result.content if event.result is not None else (),
                is_error=is_error,
            )

    # -- Agent event subscription (runs in agent thread) --

    def _on_permission_request(self, request: PermissionRequest) -> None:
        self._put_turn_event("permission_request", request=request)

    def _on_agent_event(self, event: AgentEvent) -> None:
        """Handle agent lifecycle events (called from agent thread)."""
        # This is the subscription handler — we don't need to duplicate
        # what _dispatch_agent_event does since we handle events in
        # _async_agent_turn's event stream iteration.
        pass

    # -- Polling / UI update --

    def _bind_child_approvals(self) -> None:
        owner = self._session.children
        owner.extensions.default_ui = (self._child_ui_lease, lambda event: self._event_queue.put(
            {**event, "child_session_owner": owner.token}))
        owner.approver = lambda child, request: self._event_queue.put(
            {"type": "child_permission", "owner_id": owner.token, "child_id": child, "request": request})

    def _poll_permissions(self) -> bool:
        changed = False
        if self._permission_child is not None and self._permission_prompt is not None:
            owner, child_id = self._permission_child
            if (owner is not self._session.children
                    or not owner.has_pending(child_id, self._permission_prompt.request.id)):
                self._permission_prompt = None
                self._permission_child = None
                self._permission_container.clear()
                self._layout.permission = None
                self._restore_auxiliary_focus()
                changed = True
        if (self._permission_child is None and self._permission_prompt is not None
                and self._permission_alive is not None and not self._permission_alive()):
            self._permission_prompt = None
            self._permission_alive = None
            self._permission_container.clear()
            self._layout.permission = None
            self._restore_auxiliary_focus()
            changed = True
        while self._permission_prompt is None and self._permission_waiters:
            request, waiting_owner, child, alive = self._permission_waiters.popleft()
            if waiting_owner is not None:
                if waiting_owner is not self._session.children or not waiting_owner.has_pending(child, request.id):
                    continue
            elif not alive():
                self._session.permissions.resolve(request.id, "deny")
                continue
            self._show_permission_request(request, owner=waiting_owner, child_id=child, alive=alive)
            changed = True
        if changed:
            self._update_hints()
            if self._permission_prompt is None and not self._is_busy:
                self._set_status_text("ready")
        return changed

    def _poll(self) -> None:
        """Poll the event queue and update the UI (called per frame)."""
        self._bind_child_approvals()
        self._poll_parent_drafts()
        if self._finish_deferred_draft_quit():
            return
        draft_state = self._parent_drafts.autosave.durability
        durability = f'parent draft:{draft_state}' if draft_state not in {'off', 'empty'} else ''
        draft_changed = self._footer.set_durability(durability)
        changed = bool(self._event_queue.drain(self._handle_ui_event))
        changed = draft_changed or changed
        changed = self._poll_permissions() or changed
        if self._child_panel is not None:
            if self._child_context != self._child_context_key():
                self._close_children()
            else:
                changed = self._child_publish_roster() or changed
                changed = self._child_panel.poll() or changed
        changed = self._editor.poll_autocomplete() or changed
        if self._saved_scope is not None and self._saved_context != self._mcp_context_key():
            self._saved_worker.invalidate()
            self._saved_request = ''
            self._saved_records = ()
            if self._transcript_panel is not None:
                self._transcript_panel.replace_records(())
                self._transcript_panel.set_source_status('Saved source owner changed · refresh explicitly', busy=True)
            self._saved_context = self._mcp_context_key()
            changed = True
        if isinstance(self._transcript_panel, ToolInventoryPanel) and self._tools_context != self._mcp_context_key():
            self._tools_request = ''
            self._tools_context = self._mcp_context_key()
            self._transcript_panel.invalidate_inventory('Tool inventory owner changed; r reads the current owner')
            changed = True
        if self._transcript_panel is not None:
            changed = self._transcript_panel.poll() or changed
        if self._mcp_panel is not None:
            if self._mcp_owner_context != self._mcp_context_key():
                self._close_mcp()
            else:
                changed = self._mcp_panel.poll() or changed
        changed = self._reviewed_url.poll(self._chat_log.add_system) or changed
        changed = self._outputs.poll() or changed
        self._sync_output_panel()
        if self._extension_dialog_queue or self._extension_dialog_panel is not None:
            self._sync_extension_dialog()
        if self._navigation_panel is not None:
            changed = self._navigation_panel.poll() or changed

        if self._is_busy and not self._awaiting_permission and self._run_status.phase != RunPhase.CANCELLING:
            elapsed = self._format_elapsed()
            label = f"{self._activity_label()} • {elapsed}"
            # Only repaint when the elapsed label changes.
            if label != self._last_busy_label:
                self._set_status_busy(label)
                changed = True

        session_events = getattr(self, "_session_events", None)
        if session_events is not None:
            changed = session_events.poll() or changed
        if changed:
            self._tui.request_render()

    def _show_permission_request(self, request: PermissionRequest, *, owner: CodingChildren | None = None,
                                 child_id: str = "", alive: Callable[[], bool] | None = None) -> None:
        batch_alive = alive
        stamp = self._active_stamp
        alive = alive or (lambda: self._active_stamp == stamp)
        if self._permission_prompt is not None and (owner is not None or self._permission_child is not None):
            self._permission_waiters.append((request, owner, child_id, alive))
            return
        if owner is not None:
            request = replace(request, summary=f"Child {child_id} · independent approval\n" + request.summary)
        self._permission_child = (owner, child_id) if owner is not None else None
        self._leave_child_fullscreen()
        self._leave_transcript_fullscreen()
        """Display a focused approval panel above the input."""
        self._permission_container.clear()
        self._permission_alive = batch_alive

        def _decide(decision: PermissionDecision) -> None:
            if self._permission_prompt is not prompt:
                return
            if owner is not None:
                if owner is not self._session.children:
                    return
                owner.resolve(child_id, request.id, decision)
            else:
                if not alive():
                    return
                self._session.permissions.resolve(request.id, decision)
            self._permission_child = None
            self._permission_container.clear()
            self._layout.permission = None
            self._permission_prompt = None
            self._permission_alive = None
            self._run_status = self._run_status.resume()
            self._update_hints()
            self._restore_auxiliary_focus()
            if self._is_busy:
                self._set_status_busy("resuming...")
            else:
                self._set_status_text("ready")
            self._tui.request_render()

        prompt = PermissionPromptComponent(request, self._theme, _decide)
        self._permission_prompt = prompt
        self._permission_container.add_child(prompt)
        self._layout.permission = prompt
        self._editor.set_focus(False)
        self._tui.set_focus(prompt)
        if owner is None or self._is_busy:
            self._run_status = self._run_status.advance(RunPhase.PERMISSION)
        self._set_status_text("awaiting child tool permission" if owner is not None else "awaiting tool permission")
        self._update_hints()

    def _restore_editor_messages(self, messages: Any) -> None:
        """Restore queued text on the UI thread while preserving the live draft."""
        if not isinstance(messages, (tuple, list)):
            return
        queued = "\n\n".join(str(message) for message in messages if str(message).strip())
        draft = self._editor.get_text()
        restored = "\n\n".join(text for text in (queued, draft) if text.strip())
        if restored:
            self._editor.set_text(restored)

    def _finalize_streaming_assistant(self) -> None:
        """Finish the current model message without crossing tool boundaries."""
        if self._streaming_text or self._streaming_thinking:
            self._chat_log.finalize_assistant(
                self._streaming_text,
                self._stream_id,
                thinking=self._streaming_thinking,
            )
        self._streaming_text = ""
        self._streaming_thinking = ""

    def _handle_ui_event(self, event: Mapping[str, Any]) -> None:
        if self._exit_requested:
            return
        if event.get('type') == 'argument_completion':
            event = thaw_event(event)  # Own mutable row copies before strict feed validation/presentation.
            if event.get('session_token') == id(self._session) and event['request'].owner == self._mcp_context_key():
                if not self._worker_active and self._command is None and not self._session.agent.is_streaming:
                    self._session.extensions.sync_tools(self._session.agent)
                try:
                    self._extension_snapshot = validate_snapshot(event['snapshot'])
                except (ValueError, TypeError):
                    pass
            self._argument_feed.publish(event['request'], event['rows'], event['status'])
            self._editor.poll_autocomplete()
            self._tui.request_render()
            return
        """Handle a UI event from the event queue."""
        event = thaw_event(event)
        event_type = event.get("type")
        if event_type in {'extension_dialog', 'child_extension_dialog'}:
            question = event.get('dialog')
            if not isinstance(question, dict) or not self._extension_stack.accepts(question, []):
                return  # Removed extension controls cannot touch a successor owner or draft.
        if event_type == 'native_admission_failed':
            self._session_events.admission_failed(event['id'], event['error'])
            return
        if event_type == 'native_queue_ack':
            if not event['success']:
                if event['action'] == 'edit':
                    self._preserve_input(event['content'], event['error'])
                else:
                    self._chat_log.add_system(event['error'])
            else:
                self._chat_log.add_system('Queue change applied: ' + event['action'])
            return
        if event_type == 'session_event':
            self._session_events.display(event['event'])
            self._tui.request_render()
            return
        if event_type in {"input_transformed", "input_rejected", "input_handled"}:
            key = str(event.get("id", ""))
            if event_type == "input_handled":
                if key:
                    self._routed_inputs.pop(key, None)
                    images = self._routed_images.pop(key, ())
                    self._composer.restore(images)
                    self._transformed_inputs.pop(key, None)
                elif event.get('input_origin', 'user') == 'user':
                    self._composer.restore(tuple(event.get("images", ())))
                self._chat_log.add_system("Input handled by extension; no model/history admission. "
                                          "User draft ownership unchanged.\n" + str(event.get("feedback", "")))
                self._update_message_queue()
            elif event_type == "input_transformed":
                text = str(event.get("content", ""))
                if key and key in self._routed_inputs:
                    self._transformed_inputs[key] = text
                elif event.get("source") == "prompt":
                    self._chat_log.transform_user_input(str(event.get("original", "")), text,
                                                        len(event.get("images", ())))
                self._chat_log.add_system("Submitted input transformed before model/history admission")
            elif not key and event.get("source") == "prompt":
                if event.get('input_origin', 'user') == 'user':
                    self._restore_editor_messages((str(event.get("original", "")),))
                    self._composer.restore(tuple(event.get("images", ())))
                    self._chat_log.add_system("Input not admitted; original text/images restored, no automatic replay")
                else:
                    self._chat_log.add_system('Extension input not admitted; user draft unchanged, no automatic replay')
            self._tui.request_render()
            return
        if event_type == "child_source_ready":
            if (self._child_panel is not None and event.get("id") == self._child_source_id
                    and event.get("context") == self._child_context == self._child_context_key()):
                self._child_panel.source(str(event["child_id"]), tuple(event.get("records", ())),
                                         str(event.get("error", "")))
                self._tui.request_render()
            return
        if event_type == "child_ui_result":
            if event.get('image_start'):
                self._child_image_starting = False
            self._child_jobs.discard(str(event.get("id", "")))
            if (self._child_panel is not None
                    and event.get("context") == self._child_context == self._child_context_key()
                    and event.get('panel_owner', self._child_panel.owner_token) == self._child_panel.owner_token
                    and ('queue_owner' not in event or event['queue_owner'] == (
                        self._child_panel.queue.owner_token if self._child_panel.queue is not None else ''))):
                self._child_panel.complete(str(event.get("action", "")), str(event.get("error", "")),
                                           event.get('data'), tuple(event.get('images', ())))
                self._child_publish_roster()
                self._tui.request_render()
            return
        if event_type == "child_permission":
            owner = self._session.children
            child, request = event.get("child_id"), event.get("request")
            if (event.get("owner_id") == owner.token and isinstance(child, str)
                    and isinstance(request, PermissionRequest)
                    and owner.has_pending(child, request.id)):
                self._show_permission_request(request, owner=owner, child_id=child)
            return
        if event_type in {'saved_source_ready', 'saved_source_failed'}:
            panel = self._transcript_panel
            if (self._saved_scope is None or panel is None or event.get('id') != self._saved_request
                    or event.get('context') != self._saved_context
                    or self._saved_context != self._mcp_context_key()):
                return
            self._saved_request = ''
            if event_type == 'saved_source_ready':
                self._saved_records = event['records']
                panel.replace_records(self._saved_records)
                panel.set_source_status('Saved snapshot · no activation')
            else:
                panel.set_source_status('Saved source unavailable · no live fallback', busy=True)
            self._tui.request_render()
            return
        if event_type in {'tools_ready', 'tools_failed'}:
            panel = self._transcript_panel
            if (not isinstance(panel, ToolInventoryPanel) or event.get('id') != self._tools_request
                    or event.get('context') != self._tools_context or self._tools_context != self._mcp_context_key()):
                return
            self._tools_request = ''
            if event_type == 'tools_ready':
                panel.publish_inventory(event['records'])
            else:
                panel.invalidate_inventory('Inventory unavailable · r retries explicitly')
            self._tui.request_render()
            return
        if event_type in {"child_extension_dialog", "child_extension_dialog_closed"}:
            if (event.get("child_session_owner") != self._session.children.token
                    or event.get("child_owner") != self._child_ui_lease
                    or event.get("session_id") != self._session.session_id):
                return
            if event_type == "child_extension_dialog":
                item = {**event["dialog"], "extension": event["extension"], "child_id": event["child_id"]}
                if not self._extension_stack.accepts(item, self._extension_dialog_queue):
                    self._session.children.extensions.answer(item["child_id"], item["id"], self._child_ui_lease, None)
                    return
                if not any(dialog["id"] == item["id"] for dialog in self._extension_dialog_queue):
                    self._extension_dialog_queue.append(item)
            self._sync_extension_dialog()
            self._update_hints()
            self._tui.request_render()
            return
        if event_type in {"extension_dialog", "extension_dialog_closed"}:
            if event.get("extension_session") != self._session.session_id:
                return
            if event_type == "extension_dialog":
                dialog = {**event["dialog"], "extension": event["extension"]}
                if not self._extension_stack.accepts(dialog, self._extension_dialog_queue):
                    self._session.extension_dialogs.resolve(dialog["id"], None)
                    return
                if not any(item["id"] == dialog["id"] for item in self._extension_dialog_queue):
                    self._extension_dialog_queue.append(dialog)
            self._sync_extension_dialog()
            self._update_hints()
            self._tui.request_render()
            return
        if event_type in {"extension_notice", "extension_ui"}:
            if event.get("extension_session") != self._session.session_id:
                return
            if event_type == "extension_notice":
                self._chat_log.add_system(str(event.get("text", "")))
            if isinstance(event.get("snapshot"), dict):
                self._extension_snapshot = event["snapshot"]
            self._tui.request_render()
            return
        if event_type == "mcp_view_result":
            if event.get("id") == self._mcp_active:
                self._mcp_publish(event["action"], event.get("data", ()), str(event.get("error", "")))
            return
        if event_type in {"output_prepared", "output_done", "output_failed"}:
            self._outputs.handle(event)
            self._sync_output_panel()
            return
        if event_type == "restore_attachments":
            self._composer.restore(tuple(event.get("images", ())))
            self._chat_log.add_system("Images recovered to draft; review context before resending")
            return
        if event_type == "attachment_result":
            self._composer.handle(event, retained=self._retained_image_bytes())
            return
        if event_type in {"input_ack", "input_consumed", "inputs_settled"}:
            self._handle_input_event(event)
            return
        if event_type in _TURN_EVENT_TYPES:
            stamp = event.get("stamp")
            if stamp is not None and stamp != self._active_stamp:
                return

        if event_type == "assistant_start":
            self._run_status = self._run_status.advance(RunPhase.WAITING)
            # Every model response gets its own chat component. Reusing one
            # component for the whole agent loop caused a post-tool answer to
            # replace the pre-tool reasoning in place, making the latest text
            # appear before the tool result.
            self._finalize_streaming_assistant()
            self._stream_sequence += 1
            self._stream_id = f"assistant-{self._stream_sequence}"

        elif event_type == "text_update":
            text = event.get("text", "")
            thinking = event.get("thinking", "")
            self._run_status = self._run_status.advance(RunPhase.RESPONDING if text else RunPhase.REASONING)
            self._streaming_text = text
            self._streaming_thinking = thinking
            self._chat_log.update_assistant(
                text,
                self._stream_id,
                thinking=thinking,
            )

        elif event_type == "tool_call":
            self._run_status = self._run_status.advance(RunPhase.RUNNING)
            tool_call_id = event.get("id", "")
            name = event.get("name", "")
            arguments = event.get("arguments", {})
            from xdog.coding.modes.interactive.tool_renderers import get_tool_renderer
            renderer = get_tool_renderer(name)
            custom = renderer("call", arguments) if renderer is not None else None
            if custom is not None:
                self._chat_log.add_child(custom)
            started_component = self._chat_log.add_tool(name, arguments)
            if tool_call_id:
                self._tool_components[tool_call_id] = started_component

        elif event_type == "permission_request":
            request = event.get("request")
            if isinstance(request, PermissionRequest):
                self._show_permission_request(request)

        elif event_type == "tool_update":
            # Parallel tool calls must update their own component rather than a
            # single "last tool" pointer.
            tool_call_id = event.get("id", "")
            update_component = self._tool_components.get(tool_call_id)
            if update_component is not None:
                update_component.set_streaming(event.get("text", ""))

        elif event_type == "tool_result":
            tool_call_id = event.get("id", "")
            finished_component = self._tool_components.pop(tool_call_id, None)
            self._run_status = self._run_status.advance(
                RunPhase.RUNNING if self._tool_components else RunPhase.WAITING,
            )
            if finished_component is not None:
                content = event.get("content", ())
                if isinstance(content, tuple):
                    finished_component.set_content(content)
                finished_component.set_result(
                    event.get("result", ""),
                    is_error=event.get("is_error", False),
                )

        elif event_type == "assistant_end":
            self._finalize_streaming_assistant()

        elif event_type == "queued_message_started":
            self._active_input_origin = "user"
            self._active_images = event.get("images", ())
            self._set_busy(True)
            if event.get("echo", True):
                self._chat_log.add_user(event.get("message", "") + (
                    f"\n[Images: {event['image_count']}]" if event.get("image_count") else ""
                ))
            self._update_message_queue()

        elif event_type == "restore_draft":
            self._restore_editor_messages(event.get("messages", ()))

        elif event_type == "queue_changed":
            generation = event.get("generation")
            if generation is not None and generation != self._worker_generation:
                return
            self._update_message_queue()

        elif event_type == "turn_footer_update":
            self._update_footer()
            self._update_header()

        elif event_type == "turn_end":
            generation = event.get("generation")
            if generation is not None and generation != self._worker_generation:
                return
            self._active_images = ()
            self._set_busy(False)
            self._finalize_streaming_assistant()
            self._update_footer()
            self._update_header()

        elif event_type == "log_message":
            self._chat_log.add_system(f"{event['level']} {event['logger']}: {event['message']}")

        elif event_type in {"artifact_loaded", "artifact_failed"}:
            self._artifact_result(event)

        elif event_type == "command_result":
            command = self._command
            command_id = event.get("command_id")
            if command_id is not None and (command is None or command_id != command.id):
                return
            if command is not None and command.name == "agents" and self._child_panel is not None:
                self._child_panel.complete("start", str(event.get("output", "")) if event.get("failed") else "")
                self._child_roster = {}
                self._child_publish_roster()
            if command is not None and command.name == "mcp" and self._mcp_panel is not None:
                self._mcp_publish(self._mcp_action,
                                  self._session.mcp.status() if self._session.mcp is not None else (),
                                  str(event.get("output", "MCP command failed")) if event.get("failed") else "")
            if (event.get("owner_session") == self._session.session_id
                    and isinstance(event.get("extension_snapshot"), dict)):
                try:
                    self._extension_snapshot = validate_snapshot(event["extension_snapshot"])
                except ValueError:
                    self._chat_log.add_system("Invalid extension registration snapshot ignored")
            self._command = None
            if command is not None and command.mutating:
                self._set_busy(False)
            if event.get("history_changed"):
                self._extension_snapshot = {}
                self._close_navigation()
                self._file_completions.close()
                self._file_completions = WorkspaceFileCompletionProvider(self._session.working_dir)
                self._editor.set_autocomplete_provider(self._file_completions)
                if command is not None and command.name in {"resume", "new", "branch", "goto"}:
                    self._editor.reset_history()
                self._chat_log.clear_all()
                self._streaming_text = ""
                self._streaming_thinking = ""
                self._replay_history(event.get('history_snapshot'))
            rich = event.get("rich_output")
            if isinstance(rich, ExtensionCommandResult):
                if event.get("owner_session") == self._session.session_id:
                    self._chat_log.add_child(ExtensionCommand(rich.command, rich.result, rich.text(),
                                                                owner=self._mcp_context_key()))
                else:
                    self._chat_log.add_system("Extension command owner changed; output artifact discarded")
            else:
                output = event.get("output", "")
                if output:
                    self._chat_log.add_system(output)
            if event.get("exit"):
                self._request_exit()
                return
            prompt = "" if isinstance(rich, ExtensionCommandResult) else event.get("prompt", "")
            if prompt:
                if command is not None and command.cancel_requested:
                    self._preserve_input(prompt, "command already completed; follow-up kept in draft")
                else:
                    self._start_turn(prompt, echo=False)
            self._update_footer()
            self._update_header()

        elif event_type == "error":
            generation = event.get("generation")
            if generation is not None and generation != self._worker_generation:
                return
            self._restore_editor_messages(event.get("restore", ()))
            if self._active_images:
                if self._active_input_origin == 'user':
                    self._composer.restore(self._active_images)
                    self._chat_log.add_system(
                        "Images kept for retry; input may already be in context, inspect before resending")
                else:
                    self._chat_log.add_system(
                        'Extension images not copied into your draft; inspect before re-requesting')
                self._active_images = ()
            message = event.get("message", "Unknown error")
            self._set_busy(False)
            if event.get("retain_partial") is True:
                self._streaming_text = str(event.get("partial_text", ""))
                self._streaming_thinking = str(event.get("partial_thinking", ""))
                self._finalize_streaming_assistant()
                if not event.get("partial_text") and not event.get("partial_thinking"):
                    self._chat_log.drop_assistant(self._stream_id)
            else:
                self._chat_log.drop_assistant(self._stream_id)
            self._chat_log.add_system(
                f"Canceled: {message}" if event.get("canceled") is True else self._theme.error(f"Error: {message}")
            )
            self._streaming_text = ""
            self._streaming_thinking = ""
            self._update_footer()


def _assistant_content(message: AssistantMessage) -> tuple[str, str]:
    """Extract visible response and reasoning text from an assistant message."""
    text: list[str] = []
    thinking: list[str] = []
    for part in message.content:
        if isinstance(part, TextContent):
            text.append(part.text)
        elif isinstance(part, ThinkingContent) and not part.redacted:
            thinking.append(part.thinking)
    return "".join(text), "".join(thinking)


def run_interactive_mode(
    session: AgentSession,
    *,
    verbose: bool = False,
) -> None:
    """Run the interactive TUI mode (blocking).

    This is the main entry point for the full interactive experience.
    """
    mode = InteractiveMode(session, verbose=verbose)
    mode.run()
