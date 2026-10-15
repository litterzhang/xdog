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
from xdog.agent.mcp.completion import McpCompletion, request_arguments
from xdog.agent.mcp.context import McpDocument, McpEntry, resource_completion_document
from xdog.agent.mcp.elicitation_owner import ElicitationReplyInvalid
from xdog.agent.mcp.oauth import validate_launch, validate_review
from xdog.agent.mcp.sampling_context import project as sampling_context
from xdog.agent.mcp.sampling_executor import SamplingExecutor, catalog_current, model_catalog
from xdog.agent.planning import PlanState
from xdog.agent.supervision_config import ChildConfig
from xdog.ai.types import (
    AssistantMessage,
    ImageContent,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
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
from xdog.coding.modes.interactive.theme import create_default_theme
from xdog.tui.attachments import Attachment, AttachmentComposer
from xdog.tui.child_browser import ChildSourceWorker, child_entries
from xdog.tui.child_draft_autosave import ChildDraftAutosave
from xdog.tui.child_draft_io import ChildDraftIO
from xdog.tui.child_draft_store import ChildDraftStore, SavedChildDraft
from xdog.tui.child_drafts import ChildDraft
from xdog.tui.components.attachment_panel import AttachmentPanel
from xdog.tui.components.child_draft_panel import ChildDraftPanel, DraftEntry, draft_entries
from xdog.tui.components.child_panel import ChildPanel
from xdog.tui.components.details_panel import DetailsPanel
from xdog.tui.components.extension_command import ExtensionCommand
from xdog.tui.components.extension_custom import ExtensionCustom, ExtensionCustomPresentation
from xdog.tui.components.extension_dialog import ExtensionDialog
from xdog.tui.components.extension_navigation import ExtensionNavigation
from xdog.tui.components.extension_stack import ExtensionDialogStack
from xdog.tui.components.extension_view import ExtensionView
from xdog.tui.components.inline_layout import CompactText, InlineLayout
from xdog.tui.components.input_panel import _sanitize_editor_text
from xdog.tui.components.mcp_panel import McpPanel
from xdog.tui.components.navigation_panel import NavigationPanel
from xdog.tui.components.output_panel import OutputPanel
from xdog.tui.components.plan_panel import PlanPanel
from xdog.tui.components.review_panel import ReviewPanel
from xdog.tui.components.text import Text
from xdog.tui.components.tool_inventory import ToolInventoryPanel, inventory_records
from xdog.tui.components.transcript_panel import TranscriptPanel
from xdog.tui.editor_actions import EditorActions
from xdog.tui.event_queue import EventQueue, thaw_event
from xdog.tui.extension_shortcuts import ExtensionShortcuts
from xdog.tui.external_editor import edit_draft
from xdog.tui.file_completion import WorkspaceFileCompletionProvider
from xdog.tui.keys import KeyEvent, KeyEventType
from xdog.tui.navigation import NavigationEntry, NavigationWorker
from xdog.tui.parent_drafts import ParentDrafts
from xdog.tui.prompt_keymap import PromptKeymap
from xdog.tui.prompt_terminal import PromptTerminalInterceptor, prompt_key_policy
from xdog.tui.queue_controls import QUEUE_HELP, LocalQueueIds, QueueEntry, parse_queue_command
from xdog.tui.repository_review import RepositoryReview
from xdog.tui.terminal_interception import HeldTerminalInput
from xdog.tui.terminal_logging import capture_terminal_logs
from xdog.tui.terminal_worker import TerminalHookWorker
from xdog.tui.transcript_actions import TranscriptActions
from xdog.tui.transcript_browser import TranscriptRecord
from xdog.tui.transcript_transfer import TransferOptions, parse_transfer
from xdog.tui.tui import TUI, Container
from xdog.tui.url_open import ReviewedUrlOpener
from xdog.tui.utils import sanitize_terminal_text

logger = logging.getLogger(__name__)

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
    rename_target: tuple[str, str] | None = None
    rename_alive: Callable[[], bool] | None = None
    compact_target: str | None = None
    compact_alive: Callable[[], bool] | None = None
    tools_target: tuple[tuple[str, ...] | None, int] | None = None
    tools_alive: Callable[[], bool] | None = None


@dataclass
class _ArgumentJob:
    canceled: threading.Event
    loop: asyncio.AbstractEventLoop | None = None
    task: asyncio.Task[dict[str, Any]] | None = None
    thread: threading.Thread | None = None


def _command_is_read_only(command: str, args: str) -> bool:
    if command == "agents":
        return args.strip().partition(" ")[0] != "start"
    if command == "plan":
        return args.strip() in {"", "show"}
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
        self._context_trigger: tuple[Any, Any, str, str] | None = None
        self._verbose = verbose
        self._theme = create_default_theme()

        # State
        self._exit_requested = False
        self._draft_quit_failures: tuple[int, ...] | None = None
        self._extension_browser: tuple[str, str, str] | None = None
        self._extension_navigation: tuple[str, str, dict[str, Any]] | None = None
        self._extension_navigation_review: tuple[str, str, dict[str, Any]] | None = None
        self._editor_actions = EditorActions(self._command_editor_snapshot, self._command_editor_replace)
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
        self._permission_waiters: deque[tuple[PermissionRequest, CodingChildren | None, str, _TurnStamp]] = deque()
        self._details_expanded = verbose
        self._details_open = verbose
        self._review_panel: ReviewPanel | None = None
        self._review_backend: RepositoryReview | None = None
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
        self._plan_panel: PlanPanel | None = None
        self._plan_context = ""
        self._plan_revision = -1
        self._mcp_auth_lease = ""
        self._mcp_watch_lease = ""
        self._mcp_auth_stop = threading.Event()
        self._mcp_auth_review: dict[str, Any] | None = None
        self._sampling_bindings: dict[str, tuple[Any, str, threading.Event]] = {}
        self._sampling_execution_lock = threading.Lock()
        self._elicitation_bindings: dict[str, tuple[Any, str, threading.Event]] = {}
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
        self._child_draft_labels: dict[str, str] = {}
        self._child_draft_io = ChildDraftIO()
        self._child_autosave = ChildDraftAutosave()
        self._parent_drafts = ParentDrafts(
            lambda: self._session.session_manager._dir / 'saved-parent-drafts', self._parent_draft_snapshot,
                                           self._mcp_context_key, lambda text: self._chat_log.add_system(text),
            self._parent_draft_capacity)
        self._saved_child_drafts: dict[str, SavedChildDraft] = {}
        self._local_images: dict[str, tuple[Attachment, ...]] = {}
        self._routed_images: dict[str, tuple[Attachment, ...]] = {}
        self._active_images: tuple[Attachment, ...] = ()
        self._active_input_origin = "user"
        self._dispatch_input_origin = "user"
        self._extension_input_ids: set[str] = set()
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
        from xdog.coding.modes.interactive.after_run_compaction import NativeAfterRunCompaction
        self._after_run_compaction = NativeAfterRunCompaction(self)
        from xdog.coding.modes.interactive.after_run_tools import NativeAfterRunTools
        self._after_run_tools = NativeAfterRunTools(self)
        from xdog.coding.modes.interactive.after_run_name import NativeAfterRunName
        self._after_run_name = NativeAfterRunName(self)
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
        from xdog.coding.modes.interactive.external_events import NativeExternalEvents
        self._external_events = NativeExternalEvents(self)
        from xdog.coding.modes.interactive.message_inputs import NativeMessageInputs
        self._message_inputs = NativeMessageInputs(self)
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
        self._editor._command_provider = lambda: {**original_commands(),
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
        from xdog.tui.editor_write import EditorWriteReviews
        self._editor_write_reviews = EditorWriteReviews()
        self._extension_snapshot: dict[str, Any] = {}
        self._extension_dialog_queue: list[dict[str, Any]] = []
        self._extension_stack = ExtensionDialogStack()
        self._extension_dialog_panel: ExtensionDialog | ExtensionNavigation | ExtensionCustom | None = None
        self._extension_view = ExtensionView(lambda: self._extension_snapshot)
        self._work_container.add_child(self._extension_view)
        self._details_panel = DetailsPanel(self._chat_log.detail_records, max_rows=8)
        self._banner_added = False

        # Compatibility handles retained for callers/tests; activity and metadata
        # now occupy one stable footer row.
        self._status_text: Text | None = None
        self._status_loader = None
        self._hints = CompactText("", 0, 0)
        self._update_hints()
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
        self._custom_presentation = ExtensionCustomPresentation(self._tui, self._layout)
        self._tui.add_child(self._layout)
        self._tui.set_focus(self._editor)
        self._editor.set_focus(True)
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
        self._terminal_worker = TerminalHookWorker(self._event_queue.put)
        self._terminal_input = PromptTerminalInterceptor(
            self._tui, self._terminal_prompt_eligible, self._terminal_prompt_owner,
            self._terminal_available, self._terminal_key_policy,
            self._send_terminal_hook, self._terminal_worker.cancel, self._chat_log.add_system,
            surface=lambda: "child-prompt" if self._terminal_child() else "prompt",
            release_available=self._terminal_release_available)
        if verbose:
            self._open_details()

    def _terminal_child(self) -> str:
        return self._child_panel.terminal_child if self._child_panel is not None else ""

    def _terminal_available(self) -> bool:
        child = self._terminal_child()
        if self._child_panel is not None:
            return self._session.children.extensions.terminal_metadata(self._child_ui_lease).get(
                child, {}).get("active") is True
        return self._session.extensions.terminal_input_available

    def _terminal_release_available(self) -> bool:
        child = self._terminal_child()
        if self._child_panel is not None:
            return 'release' in self._session.children.extensions.terminal_metadata(self._child_ui_lease).get(
                child, {}).get('events', [])
        return 'release' in self._session.extensions.terminal_events

    def _terminal_prompt_eligible(self) -> bool:
        child = self._terminal_child()
        target = self._child_panel if child else self._editor
        return (not self._exit_requested and self._tui._focused is target
                and not self._tui.has_overlay() and not self._details_open and self._command is None
                and self._permission_prompt is None and not self._permission_waiters
                and self._extension_dialog_panel is None and not self._extension_dialog_queue
                and self._outputs.panel is None and (self._child_panel is None or bool(child))
                and self._plan_panel is None and self._mcp_panel is None and self._transcript_panel is None
                and self._attachment_panel is None and self._navigation_panel is None and self._review_panel is None)

    def _terminal_prompt_owner(self) -> str:
        extra = ""
        if self._child_panel is not None:
            child = self._terminal_child()
            generation = self._session.children.extensions.terminal_metadata(self._child_ui_lease).get(
                child, {}).get("generation", 0)
            extra = f":child:{child}:{generation}:{self._child_panel.terminal_lease}:{id(self._child_panel)}"
        return (f'{self._session.session_id}:{id(self._session.extensions)}:{self._output_epoch}:'
                f'{self._cancel_epoch}:{self._command_sequence}:{self._session.extensions.generation}:'
                f'{self._exit_requested}{extra}')

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

    def _send_terminal_hook(self, item: HeldTerminalInput) -> bool:
        extensions = self._session.extensions
        child = self._terminal_child()
        def alive() -> bool:
            return True
        if child:
            metadata = self._session.children.extensions.terminal_metadata(self._child_ui_lease).get(child, {})
            try:
                extensions, alive = self._session.children.extensions.capture_terminal(
                    child, self._child_ui_lease, metadata.get("generation", 0))
            except ValueError:
                return False
        generation = extensions.generation
        async def run(held: HeldTerminalInput) -> dict[str, Any]:
            if extensions.generation != generation or not alive():
                raise ValueError("Terminal worker selection changed before dispatch")
            result = await extensions.terminal_input(held.data, kind=held.kind, surface=held.surface)
            if not alive():
                raise ValueError("Child terminal worker/UI owner changed before delivery")
            return result
        return self._terminal_worker.send(item, run)

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
        action = self._keymap.resolve(event)
        if action == 'remote_save_draft':
            if event.event_type is KeyEventType.PRESS:
                self._chat_log.add_system('Remote snapshot save is a gateway-client action; use local draft save here')
            return {'consume': True}
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
        if action == "terminal_input":
            if event.event_type == KeyEventType.PRESS:
                self._terminal_input.toggle_pause()
                self._terminal_input.recovery_command('list', self._editor.handle_paste)
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
        if self._attachment_panel is not None and action not in {"suspend", "details", "review", "sessions", "tree"}:
            self._attachment_panel.handle_input(event)
            self._tui.request_render()
            return {"consume": True}
        if action in {"bindings", "external_editor", "suspend", "details", "review", "sessions", "tree"}:
            if event.event_type != KeyEventType.PRESS:
                return {"consume": True}
            if action in {"sessions", "tree"}:
                self._open_navigation("sessions" if action == "sessions" else f"tree|{self._session.session_id}|")
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
        if self._editor.focused and self._editor.autocomplete_active and action not in {"suspend", "details", "review"}:
            return None
        if self._navigation_panel is not None and action not in {"suspend", "details"}:
            if action == "interrupt":
                self._close_navigation()
            else:
                self._navigation_panel.handle_input(event)
                self._tui.request_render()
            return {"consume": True}
        if self._review_panel is not None and action not in {"suspend", "details"}:
            if action == "interrupt":
                self._close_review()
            else:
                self._review_panel.handle_input(event)
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
                or self._review_panel is not None or self._details_open or self._child_panel is not None
                or self._plan_panel is not None or self._mcp_panel is not None or self._attachment_panel is not None):
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
        self._session.extensions.custom_handler = self._session.extension_dialogs.ask_custom
        self._session.extensions.custom_refresh_handler = self._session.extension_dialogs.refresh_custom
        self._session.extensions.custom_control_handler = self._session.extension_dialogs.control_custom
        self._session.extensions.command_menu_handler = self._session.extension_dialogs.ask
        self._session.extensions.editor_write_handler = self._request_editor_write
        self._message_inputs.clear()
        self._session.extensions.dialog_handler = self._message_inputs.ask
        self._session.extensions.queued_input_handler = self._request_extension_queued_input
        self._session.extensions.context_message_handler = self._request_extension_context_message
        self._session.extensions.compaction_request_handler = self._after_run_compaction.request
        self._session.extensions.active_tools_request_handler = self._after_run_tools.request
        self._session.extensions.name_request_handler = self._after_run_name.request
        self._session.extensions.dialog_cancel = self._session.extension_dialogs.cancel_extension

    async def _request_extension_context_message(self, extension: str, dialog: dict[str, Any]) -> bool:
        from xdog.agent.extensions.invocation import current_invocation
        session, stamp, command = self._session, self._active_stamp, self._command
        context, lease = self._mcp_context_key(), current_invocation()
        store = getattr(session, 'context_messages', None)
        if store is None or lease is None or lease.child_id or lease.session_id != session.session_id:
            return False
        def alive() -> bool:
            same_command = (command is not None and self._command is command and not command.cancel_requested
                and command.name.lower().startswith('ext-' + extension.lower() + '-'))
            same_turn = (command is None and self._command is None and self._worker_active
                and self._active_stamp == stamp and not self._cancel_requested)
            return (not self._exit_requested and self._session is session and self._mcp_context_key() == context
                and (same_command or same_turn) and not session.permissions.has_pending_requests)
        async def trigger(value: dict[str, Any]) -> None:
            if command is None or not alive():
                raise ValueError('Idle continuation needs the original successful command')
            self._context_trigger = command.id, session, context, value['id']
        return bool(await store.review(extension, dialog, ask=session.extension_dialogs.ask, alive=alive,
                                       trigger=trigger if command is not None else None))

    async def _request_extension_queued_input(self, extension: str, dialog: dict[str, Any]) -> bool:
        from xdog.agent.extensions.invocation import current_invocation
        from xdog.agent.extensions.queued_input import reviewed
        from xdog.ai.utils.image_files import validate_references
        session, stamp, loop = self._session, self._active_stamp, self._worker_loop_ref
        invocation = current_invocation()
        if loop is None or invocation is None or invocation.child_id or invocation.session_id != session.session_id:
            return False
        agent = session.agent
        def alive() -> bool:
            return (not self._exit_requested and not self._cancel_requested and self._worker_active
                and self._active_stamp == stamp and self._session is session and session.agent is agent
                and agent.is_streaming and not session.permissions.has_pending_requests
                and not session.children.supervisor.active)
        async def review(name: str, projected: dict[str, Any]) -> Any:
            if not alive():
                return False
            answer = await session.extension_dialogs.ask(name, projected)
            return answer if alive() else False
        async def apply(identifier: str, content: str, delivery: str, images: tuple[ImageContent, ...]) -> None:
            from typing import cast

            from xdog.agent import Delivery
            from xdog.tui.attachments import attachment
            if not alive():
                return
            if images:
                await asyncio.to_thread(validate_references, images, session.model_info)
            attachments = tuple(attachment(base64.b64decode(image.data), 'extension-input') for image in images)
            if not alive() or len(agent.pending_inputs()) >= 50:
                return
            agent.queue_input(identifier, content, cast(Delivery, delivery), images)
            self._event_queue.put({'type': 'extension_input_queued', 'id': identifier, 'content': content,
                'delivery': delivery, 'images': attachments, 'stamp': stamp, 'extension_session': session.session_id})
        async def owned_apply(identifier: str, content: str, delivery: str,
                              images: tuple[ImageContent, ...]) -> None:
            from xdog.agent.supervision import settle_on
            await settle_on(loop, apply(identifier, content, delivery, images))
        return await reviewed(extension, dialog, ask=review, apply=owned_apply, alive=alive)

    async def _request_editor_write(self, extension: str, dialog: dict[str, Any]) -> Any:
        from xdog.agent.extensions.editor_request import reviewed
        session_id = self._session.session_id
        async def commit(event: dict[str, Any]) -> None:
            self._event_queue.put({**event, 'extension_session': session_id})
        return await reviewed(extension, dialog, ask=self._session.extension_dialogs.ask, commit=commit,
            discard=lambda event: self._event_queue.put({**event, 'extension_session': session_id}))

    def _prepare_editor_write(self, dialog: dict[str, Any]) -> bool:
        identity = dialog.get('editor_write', {})
        session, child = self._session, str(identity.get('child_id', ''))
        if identity.get('session_id') != session.session_id or child != dialog.get('child_id', ''):
            return False
        runtime = session.children.extensions.owners.get(child) if child else session.extensions
        if runtime is None:
            return False
        generation, context = runtime.generation, self._mcp_context_key()
        def base_alive() -> bool:
            return (self._session is session and self._mcp_context_key() == context and not self._exit_requested
                and runtime.generation == generation and not runtime._closed
                and not self._permission_waiters and self._permission_prompt is None
                and not session.permissions.has_pending_requests
                and dialog['extension'] not in runtime._failed | runtime._unloaded)
        if child:
            panel, ui_lease = self._child_panel, self._child_ui_lease
            if panel is None or panel.terminal_child != child:
                return False  # A child without its own selected draft never borrows the parent composer.
            lease = panel.terminal_lease
            write_generation = session.children.extensions.editor_write_generation(ui_lease)
            def alive() -> bool:
                return (base_alive() and self._child_panel is panel and self._child_ui_lease == ui_lease
                    and panel.terminal_lease == lease and not panel._busy
                    and session.children.extensions.owners.get(child) is runtime
                    and session.children.extensions.editor_write_enabled(ui_lease)
                    and session.children.extensions.editor_write_generation(ui_lease) == write_generation
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
            epoch = self._cancel_epoch
            def alive() -> bool:
                return (base_alive() and self._editor is editor and self._composer is composer
                    and self._child_panel is None and self._cancel_epoch == epoch
                    and not composer.reader.pending)
            snapshot = self._command_editor_snapshot
            replace = self._command_editor_replace
        return self._editor_write_reviews.prepare(dialog, snapshot, replace, alive)

    def _dialog_contains(self, dialog: dict[str, Any]) -> bool:
        if dialog.get("child_id"):
            return self._session.children.extensions.contains(dialog["child_id"], dialog["id"], self._child_ui_lease)
        return self._session.extension_dialogs.contains(dialog["id"])

    def _command_editor_snapshot(self) -> tuple[str, int, tuple[str, ...]]:
        return (self._editor.get_text(), self._editor._cursor,
                tuple(item.id for item in self._composer.items.values()))

    def _command_editor_replace(self, value: str) -> bool:
        if len(value.encode()) > 65536 or _sanitize_editor_text(value) != value:
            return False
        self._editor.set_text(value)
        self._tui.request_render()
        return True

    def _queue_extension_navigation(self, item: dict[str, Any], answer: Any, *, accepted: bool = False) -> bool:
        command = self._command
        allowed = (command is not None and not command.cancel_requested and not item.get("child_id")
                   and command.name.lower().startswith("ext-" + str(item.get("extension", "")).lower() + "-")
                   and not self._worker_active and not self._exit_requested)
        owner = str(command.id) if command is not None else ""
        if (answer is not True or not allowed or self._extension_navigation is not None
                or self._extension_navigation_review is not None
                or item.get('operation') in {'compact', 'message'} and self._extension_browser is not None):
            return False
        if item.get('operation') == 'message' and (self._permission_prompt is not None
                or self._permission_waiters or self._session.children.supervisor.active
                or self._child_image_starting):
            return False
        if item.get('operation') == 'select_tools':
            from xdog.agent.extensions.active_tools import names
            try:
                selected = names(item['names'])
                if (item['revision'] != self._session.agent.tool_selection_revision
                        or selected is not None
                        and not set(selected) <= {tool.name for tool in self._session.agent.state.tools}
                        or self._permission_prompt is not None or self._permission_waiters
                        or self._session.children.supervisor.active):
                    return False
            except (ValueError, TypeError, KeyError):
                return False
        if not accepted:
            self._extension_navigation_review = owner, self._mcp_context_key(), dict(item)
            return True
        if item.get('operation') == 'select_tools':
            self._extension_navigation = owner, self._mcp_context_key(), {
                'action': 'select_tools', 'names': selected, 'revision': item['revision']}
            return True
        if item.get('operation') == 'message':
            try:
                selected_message, message_images = self._message_inputs.take(item)
            except ValueError:
                return False
            self._extension_navigation = owner, self._mcp_context_key(), {
                'action': 'message', 'message': selected_message.content, 'images': message_images,
                'image_model': (self._session.model, self._session.model_info)}
            return True
        if item.get('operation') == 'compact':
            self._extension_navigation = owner, self._mcp_context_key(), {
                'action': 'compact', 'instructions': item['instructions']}
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
        if self._command is None or str(self._command.id) != review[0]:
            return
        self._queue_extension_navigation(item, True, accepted=True)

    def _finish_extension_navigation(self, owner: str, success: bool) -> bool:
        self._message_inputs.clear()
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
        action = target["action"]
        if action == 'select_tools':
            try:
                self._session._children_idle()
                if self._permission_prompt is not None or self._permission_waiters:
                    raise ValueError('Tool approval still owns the host')
                selected = None if target['names'] is None else tuple(target['names'])
                self._handle_slash_command('active-tools', '', tools_target=(selected, target['revision']))
            except (ValueError, RuntimeError):
                self._chat_log.add_system('Active tool selection not applied; owner/inventory changed')
        elif action == 'message':
            if (self._worker_active or self._permission_prompt is not None or self._permission_waiters
                    or self._session.children.supervisor.active or self._child_image_starting
                    or self._command is not None or target.get('images')
                    and target['image_model'] != (self._session.model, self._session.model_info)):
                self._chat_log.add_system('Reviewed extension message discarded; host is no longer idle')
            else:
                self._start_turn(target['message'], images=target.get('images', ()), input_origin='extension')
        elif action == 'compact':
            self._handle_slash_command('compact', '', compact_target=target['instructions'])
        elif action == 'rename':
            self._handle_slash_command('name', '', rename_target=(target['name'], target['expected_name']))
        elif action == "new":
            self._handle_slash_command("new", "")
        elif action == "resume":
            self._handle_slash_command("resume", target["session_id"])
        elif action == "branch":
            self._handle_slash_command("branch", target["session_id"] + " " + target["branch_id"])
        elif action == "goto":
            self._handle_slash_command("goto", target["session_id"] + " " + (target["branch_id"] or "-")
                                       + " " + str(target["index"] + 1))
        else:
            self._handle_slash_command("fork", str(target["index"]) if target["index"] >= 0 else "")
        return True

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
            elif item['operation'] == 'browse':
                value = not (self._extension_navigation is not None
                             and self._extension_navigation[2]['action'] in {'compact', 'message'})
                if value:
                    self._extension_browser = owner, self._mcp_context_key(), item["scope"]
        self._answer_extension_dialog(item["id"], value)

    def _finish_extension_browser(self, owner: str, success: bool) -> None:
        queued, self._extension_browser = self._extension_browser, None
        if (queued is not None and queued[:2] == (owner, self._mcp_context_key())
                and success and not self._exit_requested):
            scope = "sessions" if queued[2] == "sessions" else "tree|" + self._command_session_id() + "|"
            self._open_navigation(scope)

    def _command_session_id(self) -> str:
        return self._session.session_id

    def _serve_editor_request(self, item: dict[str, Any]) -> None:
        command = self._command
        allowed = (not item.get("child_id") and command is not None
                   and command.name.lower().startswith("ext-" + str(item.get("extension", "")).lower() + "-")
                   and not command.cancel_requested and not self._worker_active and not self._exit_requested)
        owner = self._mcp_context_key() + ":" + (str(command.id) if command is not None else "")
        value = None
        if allowed:
            try:
                value = self._editor_actions.handle(owner, item)
            except ValueError:
                value = None
        self._answer_extension_dialog(item["id"], value)

    def _sync_extension_dialog(self) -> None:
        self._extension_dialog_queue = [dialog for dialog in self._extension_dialog_queue
            if self._dialog_contains(dialog)]
        panel = self._extension_dialog_panel
        if panel is not None and not self._dialog_contains(panel.dialog):
            self._extension_dialog_panel = None
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
                    and item.get("operation") not in {"navigate", "rename", "select_tools",
                                                       "compact", "message", "propose", "queue_input",
                                                       "context_message"}):
                if (self._permission_prompt is not None or self._outputs.panel is not None
                        or self._custom_presentation.blocked()):
                    return
                if item["kind"] == "session":
                    self._serve_session_request(item)
                else:
                    self._serve_editor_request(item)
                return

            if item.get('operation') == 'propose' and not self._prepare_editor_write(item):
                self._answer_extension_dialog(item['id'], None)
                return
            if item.get('operation') == 'message':
                try:
                    self._message_inputs.take(item, consume=False)
                except ValueError:
                    self._answer_extension_dialog(item['id'], False)
                    return
            if item.get('operation') == 'select_tools' and item.get('after_run') is not True:
                from xdog.agent.extensions.active_tools import names, snapshot
                try:
                    command = self._command
                    current = snapshot(self._session.agent, self._session.session_id)
                    selected_names = names(item['names'])
                    if (item.get('child_id') or command is None or command.cancel_requested or self._worker_active
                            or not command.name.lower().startswith(
                                'ext-' + str(item.get('extension', '')).lower() + '-')
                            or current['revision'] != item['revision']
                            or selected_names is not None and not set(selected_names) <= set(current['configured'])):
                        raise ValueError('Active tool selection owner changed')
                    item = {**item, 'current_tools': current}
                except (ValueError, KeyError, TypeError):
                    self._answer_extension_dialog(item['id'], False)
                    return
            def answer(value: Any, key: str = item["id"]) -> None:
                self._answer_extension_dialog(key, value)
            label = f"Child {item['child_id']} · {item['extension']}" if item.get("child_id") else item["extension"]
            if 'provider_auth_host' in item:
                from xdog.tui.components.extension_provider_auth import ExtensionProviderAuth
                if not self._provider_auth.owns(item):
                    self._answer_extension_dialog(item['id'], None)
                    return
                self._extension_dialog_panel = ExtensionProviderAuth(label, item, answer,
                                                                     open_url=self._provider_auth.open_url)
            elif item.get('operation') == 'context_message':
                from xdog.tui.components.extension_context_message import ExtensionContextMessage
                self._extension_dialog_panel = ExtensionContextMessage(label, item, answer, self._mcp_context_key())
            elif item.get('operation') == 'queue_input':
                from xdog.tui.components.extension_queued_input import ExtensionQueuedInput
                self._extension_dialog_panel = ExtensionQueuedInput(item['extension'], item, answer,
                    self._session.session_id + (f" · child {item['child_id']}" if item.get('child_id') else ''))
            elif item.get('operation') == 'propose':
                from xdog.tui.components.extension_editor_write import ExtensionEditorWrite
                self._extension_dialog_panel = ExtensionEditorWrite(label, item, answer)
            elif item["kind"] == "custom":
                def request(event: dict[str, Any]) -> bool:
                    return self._request_extension_custom(item, event)
                self._extension_dialog_panel = ExtensionCustom(label, item, answer, request)
            elif item.get("operation") in {"navigate", "rename", "select_tools", "compact", "message"}:
                self._extension_dialog_panel = ExtensionNavigation(label, item, answer, self._command_session_id())
            else:
                self._extension_dialog_panel = ExtensionDialog(label, item, answer)
            self._extension_stack.panels[item["id"]] = self._extension_dialog_panel
        if self._permission_prompt is None and self._outputs.panel is None and not self._custom_presentation.blocked():
            if self._extension_dialog_panel is not None:
                self._leave_child_fullscreen()
                self._leave_transcript_fullscreen()
                if self._extension_dialog_panel.dialog.get('overlay') is not None:
                    self._layout.details = None
                    if (isinstance(self._tui._focused, (ExtensionDialog, ExtensionNavigation, ExtensionCustom))
                            and self._tui._focused is not self._extension_dialog_panel):
                        self._restore_auxiliary_focus()
                else:
                    self._layout.details = self._extension_dialog_panel
                    self._tui.set_focus(self._extension_dialog_panel)
            elif (isinstance(self._layout.details, (ExtensionDialog, ExtensionNavigation, ExtensionCustom))
                  or self._custom_presentation.overlay_panel is not None):
                self._layout.details = (self._child_panel or self._plan_panel or self._mcp_panel
                                       or self._transcript_panel) or (
                    self._details_panel if self._details_open else None)
                self._restore_auxiliary_focus()

        custom = self._extension_dialog_panel
        self._custom_presentation.sync(custom if isinstance(custom, ExtensionCustom) else None,
            self._outputs.panel is None and not self._custom_presentation.blocked() and self._permission_prompt is None)

    def _request_extension_custom(self, item: dict[str, Any], event: dict[str, Any]) -> bool:
        panel = self._extension_dialog_panel
        if ((item.get("fullscreen") and (not isinstance(panel, ExtensionCustom) or not panel.fullscreen)
                or item.get("overlay") is not None and (not isinstance(panel, ExtensionCustom) or not panel.overlay))
                and not (event.get("kind") == "resize" and event.get("height") == 0)):
            return False
        if not self._dialog_contains(item):
            return False
        if item.get("child_id"):
            return self._session.children.extensions.request_custom(
                item["child_id"], item["id"], self._child_ui_lease, event)
        return self._session.extension_dialogs.request_custom(item["id"], event)

    def _answer_extension_dialog(self, key: str, value: Any) -> None:
        item = next((item for item in self._extension_dialog_queue if item["id"] == key), None)
        if item is not None and item.get('operation') == 'propose':
            value = self._editor_write_reviews.answer(item, value is True)
        if (item is not None and item.get("operation") in {"navigate", "rename", "select_tools", "compact", "message"}
                and not (item.get("operation") in {"compact", "select_tools"} and item.get("after_run") is True)
                and not item.get("name_after_run")):
            value = self._queue_extension_navigation(item, value)

        if item is not None and item.get("child_id"):
            self._session.children.extensions.answer(item["child_id"], key, self._child_ui_lease, value)
        else:
            self._session.extension_dialogs.resolve(key, value)
        self._extension_dialog_queue = [item for item in self._extension_dialog_queue if item["id"] != key]
        self._extension_dialog_panel = None
        self._sync_extension_dialog()
        self._update_hints()
        self._tui.request_render()

    def _open_plan(self) -> None:
        if self._child_panel is not None:
            self._close_children()
        if self._exit_requested or self._permission_prompt is not None or self._tui.has_overlay():
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
        self._plan_panel = PlanPanel(lambda: {**self._session.plan.state.to_dict(),
                                    "busy": self._worker_active or self._is_busy or self._command is not None},
                                    self._plan_action, self._close_plan)
        self._layout.details = self._plan_panel
        self._restore_auxiliary_focus()
        self._update_hints()
        self._tui.request_render()

    def _plan_action(self, action: str, revision: int) -> None:
        if self._plan_context != self._mcp_context_key():
            self._close_plan()
            return
        self._handle_slash_command("plan", f"{action} {revision}")

    def _close_plan(self) -> None:
        self._plan_panel = None
        self._layout.details = self._outputs.panel or self._mcp_panel or self._transcript_panel
        self._restore_auxiliary_focus()
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
        if self._child_panel is not None:
            self._close_children()
        if self._exit_requested or self._permission_prompt is not None or self._tui.has_overlay():
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
        self._mcp_watch_lease = uuid.uuid4().hex
        self._mcp_auth_lease = uuid.uuid4().hex
        self._mcp_auth_stop = threading.Event()
        self._mcp_auth_review = None
        self._mcp_panel = McpPanel(self._mcp_send, self._close_mcp, self._mcp_insert, self._mcp_export,
                                   self._mcp_auth_copy)
        self._layout.details = self._mcp_panel
        self._tui.set_focus(self._mcp_panel)
        self._update_hints()
        self._tui.request_render()

    def _elicitation_off(self, server: str) -> None:
        binding = self._elicitation_bindings.pop(server, None)
        if binding is None:
            return
        manager, lease, revoked = binding
        revoked.set()  # Synchronous guard before scheduling async cancellation.
        def detach() -> None:
            try:
                asyncio.run(manager.detach_elicitation(server, lease=lease))
            except (Exception, asyncio.CancelledError):
                pass
        threading.Thread(target=detach, daemon=True, name='xdog-elicitation-detach').start()

    async def _elicitation_enable(self, server: str, *, tasks: bool = False) -> dict[str, str]:
        session, manager, panel = self._session, self._session.mcp, self._mcp_panel
        if (manager is None or panel is None or self._worker_active or self._command is not None
                or session.agent.is_streaming or session.children.supervisor.active
                or server in self._elicitation_bindings):
            raise ValueError('Elicitation requires an idle original view and disconnected server')
        lease, revoked = uuid.uuid4().hex, threading.Event()
        context = self._mcp_context_key()
        def alive() -> bool:
            return (not revoked.is_set() and not self._exit_requested and self._session is session
                    and session.mcp is manager and self._mcp_panel is panel and self._mcp_context_key() == context
                    and not session.agent.is_streaming and not session.children.supervisor.active
                    and self._permission_prompt is None and not self._permission_waiters
                    and self._extension_dialog_panel is None and not self._extension_dialog_queue
                    and not session.extension_dialogs.has_pending and not self._tui.has_overlay())
        self._elicitation_bindings[server] = manager, lease, revoked
        try:
            await manager.register_elicitation(server, lease=lease, alive=alive, tasks=tasks,
                publish=lambda event: self._event_queue.put({'type': 'mcp_elicitation_event', 'event': event,
                    'session_token': id(session), 'context': context}))
        except BaseException:
            revoked.set()
            self._elicitation_bindings.pop(server, None)
            raise
        return {'notice': ('Elicitation tasks armed' if tasks else 'Elicitation armed')
                + ' for this view only; connect explicitly; requests/replies need review.'}

    def _sampling_off(self, server: str) -> None:
        binding = self._sampling_bindings.pop(server, None)
        if binding is None:
            return
        manager, lease, revoked = binding
        revoked.set()  # Synchronous guard before scheduling async cancellation.
        def detach() -> None:
            try:
                asyncio.run(manager.detach_sampling(server, lease=lease))
            except (Exception, asyncio.CancelledError):
                pass
        threading.Thread(target=detach, daemon=True, name='xdog-sampling-detach').start()

    async def _sampling_enable(self, server: str, *, tasks: bool = False) -> dict[str, str]:
        session, manager, panel = self._session, self._session.mcp, self._mcp_panel
        if (manager is None or panel is None or self._permission_prompt is not None
                or self._worker_active or self._command is not None or session.agent.is_streaming
                or session.children.supervisor.active or server in self._sampling_bindings):
            raise ValueError('Sampling requires an idle original MCP view and disconnected server')
        lease, revoked = uuid.uuid4().hex, threading.Event()
        context, stream, model = self._mcp_context_key(), session.agent.stream_fn, session.model
        models, profiles = model_catalog(stream, model)
        def alive() -> bool:
            return (not revoked.is_set() and not self._exit_requested and self._session is session
                    and session.mcp is manager and self._mcp_panel is panel and self._mcp_context_key() == context
                    and session.agent.stream_fn is stream and session.model == model
                    and catalog_current(stream, model, models, profiles)
                    and not session.agent.is_streaming and not session.children.supervisor.active
                    and self._permission_prompt is None and not self._permission_waiters
                    and self._extension_dialog_panel is None and not self._extension_dialog_queue
                    and not session.extension_dialogs.has_pending and not self._tui.has_overlay())
        adapter = SamplingExecutor(stream, models, alive)
        async def execute(value: dict[str, Any], selection: dict[str, Any], cancel: asyncio.Event) -> dict[str, Any]:
            if not alive() or not self._sampling_execution_lock.acquire(blocking=False):
                raise ValueError('Sampling executor requires exclusive idle host ownership')
            if session._auth_changing:
                self._sampling_execution_lock.release()
                raise ValueError('Host authorization/policy work still owns execution')
            session._auth_changing = True
            try:
                return await adapter(value, selection, cancel)
            finally:
                session._auth_changing = False
                self._sampling_execution_lock.release()
        async def capture_context(scope: str) -> list[dict[str, Any]]:
            if not alive():
                raise ValueError('Sampling context original host unavailable')
            from xdog.tui.utils import redact_sensitive_text
            history, aliases = session.agent.messages, manager.context_aliases()
            from xdog.agent.supervision_transfer import settled_image_work
            rows = await settled_image_work(lambda: sampling_context(history, aliases, server, scope,
                                                                      redact_sensitive_text))
            if not alive() or session.agent.messages is not history:
                raise ValueError('Sampling context changed during capture; no disclosure')
            return rows
        self._sampling_bindings[server] = manager, lease, revoked
        try:
            await manager.register_sampling(server, lease=lease, execute=execute, alive=alive,
                context=capture_context, tasks=tasks,
                publish=lambda event: self._event_queue.put({'type': 'mcp_sampling_event', 'event': event,
                    'session_token': id(session), 'context': context, 'models': models}))
        except BaseException:
            revoked.set()
            self._sampling_bindings.pop(server, None)
            raise
        return {'state': 'enabled', 'notice': ('Sampling tasks armed' if tasks else 'Sampling armed')
                + ' for this view only; connect explicitly. '
                'Every request and result requires full review; no automatic model or tool execution.'}

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
                    asyncio.run(manager.detach_roots(lease))
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
        for server in tuple(self._elicitation_bindings):
            self._elicitation_off(server)
        for sampling_server in tuple(self._sampling_bindings):
            self._sampling_off(sampling_server)
        self._detach_mcp_auth()
        lease, self._mcp_watch_lease = self._mcp_watch_lease, ""
        manager = self._session.mcp
        if lease and manager is not None:
            def detach_watches() -> None:
                try:
                    asyncio.run(manager.detach_resource_watches(lease))
                except Exception:
                    pass  # Closed view cannot receive late data; owner close also tears down watches.
            threading.Thread(target=detach_watches, daemon=True, name="xdog-mcp-watch-detach").start()
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
            panel._message = error or str(details.get('notice', details.get('state', 'Sampling settled')))
            self._tui.request_render()
            return
        if action.startswith('roots_'):
            try:
                panel.publish_roots(action, data if isinstance(data, dict) else {}, error)
            except ValueError as exc:
                panel.publish_roots(action, {}, str(exc))
            self._tui.request_render()
            return
        if action in {"watch_resource", "unwatch_resource", "watch_catalog", "unwatch_catalog",
                      "watch_notifications", "unwatch_notifications"}:
            panel.publish_watch(data if isinstance(data, dict) else {}, error, receipt=True)
        elif error:
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
            binding = self._elicitation_bindings.get(server)
            owner_session = self._session
            def url_alive() -> bool:
                return (binding is not None and self._elicitation_bindings.get(server) is binding
                        and not binding[2].is_set() and self._session is owner_session
                        and owner_session.mcp is binding[0] and not owner_session.agent.is_streaming
                        and not owner_session.children.supervisor.active and not owner_session._auth_changing
                        and self._permission_prompt is None and not self._permission_waiters
                        and self._extension_dialog_panel is None and not self._extension_dialog_queue
                        and not owner_session.extension_dialogs.has_pending and not self._tui.has_overlay()
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
        if action == 'elicitation_off':
            self._elicitation_off(server)
            if self._mcp_panel is not None:
                self._mcp_panel._busy = False
                self._mcp_panel._message = 'Elicitation revoked; owned cleanup settling, no replay'
            return
        if action == 'elicitation_answer':
            binding = self._elicitation_bindings.get(server)
            if binding is None:
                return
            manager, lease, revoked = binding
            try:
                response = json.loads(name)
            except ValueError:
                self._elicitation_off(server)
                return
            def answer() -> None:
                try:
                    if revoked.is_set():
                        return
                    asyncio.run(manager.answer_elicitation(server, response.pop('id'), lease=lease,
                                                         **response))
                except ElicitationReplyInvalid:
                    pass  # Owner published a fresh review with retained draft; do not revoke registration.
                except (Exception, asyncio.CancelledError):
                    self._event_queue.put({'type': 'mcp_elicitation_failed', 'server': server, 'lease': lease})
            threading.Thread(target=answer, daemon=True, name='xdog-elicitation-answer').start()
            return
        if action == 'sampling_off':
            self._sampling_off(server)
            if self._mcp_panel is not None:
                self._mcp_panel._busy = False
                self._mcp_panel._message = 'Sampling revoked; owned cleanup settling, no replay'
            return
        if action == 'sampling_answer':
            binding = self._sampling_bindings.get(server)
            if binding is None:
                return
            manager, lease, revoked = binding
            try:
                response = json.loads(name)
            except ValueError:
                self._sampling_off(server)
                return
            def answer() -> None:
                try:
                    if revoked.is_set():
                        return
                    asyncio.run(manager.answer_sampling(server, response.pop('id'), lease=lease,
                                                         **response))
                except (Exception, asyncio.CancelledError):
                    self._event_queue.put({'type': 'mcp_sampling_failed', 'server': server, 'lease': lease})
            threading.Thread(target=answer, daemon=True, name='xdog-sampling-answer').start()
            return
        if action == "completion_cancel":
            if self._mcp_cancel is not None:
                self._mcp_cancel.set()
            self._mcp_active, self._mcp_action = "", ""
            return
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
        if action == "resource_completion_preview":
            try:
                self._mcp_publish(action, resource_completion_document(server, name, arguments))
            except (ValueError, RuntimeError) as exc:
                self._mcp_publish(action, error=str(exc))
            return  # Local operator-edited data; no IPC/remote read/model/tool request.
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
        watch_lease, watch_context = self._mcp_watch_lease, self._mcp_owner_context
        def watch_alive() -> bool:
            return (not self._exit_requested and self._mcp_panel is not None
                    and self._session is owner_session and owner_session.mcp is manager
                    and self._mcp_watch_lease == watch_lease and watch_context == self._mcp_context_key())
        async def request() -> Any:
            if action in {'elicitation_on', 'elicitation_tasks_on'}:
                return await self._elicitation_enable(server, tasks=action == 'elicitation_tasks_on')
            if action in {'sampling_on', 'sampling_tasks_on'}:
                return await self._sampling_enable(server, tasks=action == 'sampling_tasks_on')
            if action.startswith('roots_'):
                return await owner_session.manage_mcp_roots(server, action.removeprefix('roots_'),
                    json.loads(name) if action == 'roots_prepare' else None,
                    name if action == 'roots_commit' else '', auth_lease, alive)
            if action == 'watch_notifications':
                return await manager.watch_notifications(server, json.loads(name), lease=watch_lease, alive=watch_alive,
                    on_change=lambda data: self._event_queue.put({"type": "mcp_watch_notice", "lease": watch_lease,
                                                                "context": watch_context, "data": data}))
            if action == 'unwatch_notifications':
                return await manager.unwatch_notifications(server, json.loads(name), lease=watch_lease)
            if action == "watch_catalog":
                return await manager.watch_catalog(server, name, lease=watch_lease, alive=watch_alive,
                    on_change=lambda data: self._event_queue.put({"type": "mcp_watch_notice", "lease": watch_lease,
                                                                "context": watch_context, "data": data}))
            if action == "unwatch_catalog":
                return await manager.unwatch_catalog(server, name, lease=watch_lease)
            if action == "watch_resource":
                return await manager.watch_resource(server, name, lease=watch_lease, alive=watch_alive,
                    on_change=lambda data: self._event_queue.put({"type": "mcp_watch_notice", "lease": watch_lease,
                                                                "context": watch_context, "data": data}))
            if action == "unwatch_resource":
                return await manager.unwatch_resource(server, name, lease=watch_lease)
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
            if action in {"complete_prompt", "complete_template", "complete_resource"}:
                argument, value, context = request_arguments(arguments)
                return await manager.complete(server, action.removeprefix("complete_"), name, argument, value, context)
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
        if self._exit_requested or self._permission_prompt is not None or self._tui.has_overlay():
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
        self._child_context = self._child_context_key()
        self._child_target = target
        self._child_roster = {}
        self._child_source_worker = ChildSourceWorker(self._event_queue.put)
        self._child_draft_labels[self._child_context] = (
            f'{self._session.working_dir} · session {self._session.session_id}')
        self._child_panel = ChildPanel(self._child_send, self._child_read, self._close_children,
                                       self._child_output, self._child_fullscreen,
                                       configure=self._child_resolve_config,
                                       configured_start=self._child_configured_start,
                                       image_send=self._child_image_send, root=self._session.working_dir,
                                       drafts=self._child_drafts.setdefault(self._child_context, {}),
                                       retained_bytes=self._child_retained_bytes,
                                       draft_source=self._child_draft_entries,
                                       draft_action=self._child_draft_action)
        self._child_panel.queue_enabled = True
        self._child_panel.queue_image_recovery_enabled = True
        self._child_panel.queue_task_recovery_enabled = True
        self._child_panel.queue_image_return_enabled = True
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
        # Existing parent catalog is replaced; every unrelated active/local owner stays counted.
        return max(0, 40 * 1024 * 1024 - self._retained_image_bytes()
                   + self._parent_saved_bytes() - self._composer.bytes)

    def _parent_draft_snapshot(self) -> ChildDraft:
        return ChildDraft(self._editor.get_text(), images=tuple(self._composer.items.values()),
                          cursor=self._editor._cursor)

    def _parent_draft_command(self, args: str) -> None:
        try:
            if args.strip() == 'save':
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
        return ChildDraftStore(self._session.session_manager._dir / 'saved-child-drafts')

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
                    or self._child_context != self._child_context_key()):
                self._chat_log.add_system('Saved catalog view changed; explicitly list again')
                return
            rows = result['catalog']
            self._chat_log.add_system('Saved child snapshots (metadata only; no drafts adopted):\n' + '\n'.join(
                f"{row['id']} · {row['image_count']} images · {row['image_bytes']} bytes · "
                + str(row['label']) for row in rows)
                + '\nUse /agents saved SNAPSHOT_ID to load one for full reviewed recovery')
        elif action in {'list', 'load'}:
            if (panel is None or panel.owner_token != result['owner']
                    or self._child_context != self._child_context_key()):
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
        if panel is None or self._exit_requested or self._child_context != self._child_context_key():
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
        options = ["--label", chosen.label, "--instructions", chosen.instructions,
                   "--timeout", str(chosen.timeout), "--calls", str(chosen.max_model_calls),
                   "--tokens", str(chosen.max_tokens)]
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
        return_owner = panel.owner_token + ':' + (queue_panel.owner_token if queue_panel is not None else '')
        def return_alive() -> bool:
            return (not self._exit_requested and self._child_panel is panel and panel.queue is queue_panel
                    and self._child_context_key() == context)
        def work() -> None:
            error = ""
            data: Any = None
            images: tuple[Attachment, ...] = ()
            try:
                data = asyncio.run(owner.supervisor.control(action, child, text,
                    return_owner=return_owner, return_alive=return_alive))
                if 'image_recovery' in data:
                    from xdog.tui.child_recovery import decode_recovery
                    images = decode_recovery(data.pop('image_recovery'))
            except (Exception, asyncio.CancelledError) as exc:
                error = str(exc) or "Child control canceled after settlement"
            self._event_queue.put({"type": "child_ui_result", "id": identifier, "action": action,
                                   "context": context, "error": error, "data": data, 'images': images,
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
        self._parent_drafts.invalidate_read()
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
            self._layout.details = (self._child_panel or self._plan_panel or self._mcp_panel
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
        if self._transcript_panel is not None:
            self._close_transcript()
        if self._permission_prompt is not None or self._tui.has_overlay():
            return
        if self._mcp_panel is not None:
            self._close_mcp()
        if self._plan_panel is not None:
            self._close_plan()
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
        self._restore_auxiliary_focus()
        self._update_hints()
        self._tui.request_render()

    def _open_navigation(self, scope: str = "sessions") -> None:
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
        if self._plan_panel is not None:
            self._close_plan()
        if self._navigation_panel is not None:
            self._close_navigation()
        if self._review_panel is not None:
            self._close_review()
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
        self._layout.details = self._review_panel or (self._details_panel if self._details_open else None)
        if self._permission_prompt is None:
            self._restore_auxiliary_focus()
        self._update_hints()
        self._tui.request_render()

    def _open_review(self) -> None:
        if self._child_panel is not None:
            self._close_children()
        if self._transcript_panel is not None:
            self._close_transcript()
        if self._attachment_panel is not None:
            self._close_attachment_preview()
        if self._exit_requested:
            return
        if self._permission_prompt is not None or self._tui.has_overlay():
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
        self._review_backend = RepositoryReview(self._session.working_dir)
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
        if self._permission_prompt is None:
            self._tui.set_focus(self._details_panel if self._details_open else self._editor)
        self._update_hints()
        self._tui.request_render()

    def _restore_auxiliary_focus(self) -> None:
        self._tui.set_focus(
            self._permission_prompt or self._outputs.panel
            or (self._extension_dialog_panel if self._extension_dialog_panel is not None
                and self._extension_dialog_panel.dialog.get('overlay') is None else None)
            or self._child_panel or self._plan_panel or self._mcp_panel
            or self._transcript_panel
            or self._attachment_panel
            or self._navigation_panel or self._review_panel
            or (self._details_panel if self._details_open else self._editor)
        )

    def _open_details(self) -> None:
        if self._child_panel is not None:
            self._close_children()
        if self._plan_panel is not None:
            self._close_plan()
        if self._mcp_panel is not None:
            self._close_mcp()
        if self._transcript_panel is not None:
            self._close_transcript()
        if self._attachment_panel is not None:
            self._close_attachment_preview()
        if self._navigation_panel is not None:
            self._close_navigation()
        if self._review_panel is not None:
            self._close_review()
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
            external = getattr(self, "_external_events", None)
            if external is not None:
                external.start()
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

    def _replay_history(self) -> None:
        self._output_epoch += 1
        if self._transcript_panel is not None:
            self._close_transcript()
        """Replay messages, including persisted tool calls and their results."""
        tool_components: dict[str, Any] = {}
        for msg in self._session.messages:
            if isinstance(msg, UserMessage):
                text = msg.content if isinstance(msg.content, str) else "\n".join(
                    part.text for part in msg.content if isinstance(part, TextContent)
                )
                image_count = (sum(isinstance(part, ImageContent) for part in msg.content)
                               if not isinstance(msg.content, str) else 0)
                if image_count:
                    text += f"\n[Images: {image_count}]"
                if text:
                    self._chat_log.add_user(text)
                    self._editor.add_to_history(text)
            elif isinstance(msg, AssistantMessage):
                text, thinking = _assistant_content(msg)
                if text or thinking:
                    self._chat_log.add_assistant(text, thinking=thinking)
                for part in msg.content:
                    if isinstance(part, ToolCall):
                        tool_components[part.id] = self._chat_log.add_tool(
                            part.name,
                            part.arguments,
                        )
            elif getattr(msg, "type", None) == "xdog.external_event.v1":
                self._chat_log.add_system("External event · recorded input; not a human message")
            elif isinstance(msg, ToolResultMessage):
                component = tool_components.pop(msg.tool_call_id, None)
                if component is None:
                    component = self._chat_log.add_tool(msg.tool_name, None)
                result_text = "\n".join(
                    part.text for part in msg.content if isinstance(part, TextContent)
                )
                component.set_result(result_text, is_error=msg.is_error)

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
        elif (isinstance(self._extension_dialog_panel, ExtensionCustom) and self._extension_dialog_panel.overlay
              and not self._extension_dialog_panel.focused):
            hint = "Background overlay · parent draft editable · Esc/Ctrl+C cancel view · NOT tool approval"
        elif self._extension_dialog_panel is not None:
            hint = "Extension dialog · Enter answers · Esc cancels · NOT tool approval"
        elif self._child_panel is not None:
            hint = "Children focused · Enter inspect · n new · c cancel · / search · Esc parent draft"
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
        state = getattr(getattr(self._session, "plan", None), "state", None)
        if self._editor.focused and isinstance(state, PlanState) and state.mode != "off":
            hint = ("READ-ONLY plan · " if state.mode == "planning" else "EXECUTING plan · ") + hint
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
        external = getattr(self, "_external_events", None)
        if external is not None:
            external.human_input()

        # Check for slash commands
        parsed = parse_slash_command(value)
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
                self._extension_input_ids.discard(input_id)
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
            self._extension_input_ids.discard(input_id)
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
                    if item.id in self._extension_input_ids:
                        self._extension_input_ids.discard(item.id)
                        self._chat_log.add_system(f'Extension input {item.id} not consumed; '
                            'not copied into the user draft. Inspect before re-requesting.\n' + str(raw['content']))
                    else:
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
        with self._queue_lock:
            if self._worker_active:
                self._pending_messages.append((message, echo))
                keys = self._pending_ids.sync(len(self._pending_messages))
                self._local_images[keys[-1]] = images
                queued = True
            else:
                self._worker_active = True
                self._cancel_requested = False
                self._worker_generation += 1
                generation = self._worker_generation
                self._active_stamp = _TurnStamp(generation, self._cancel_epoch)
                queued = False

        if queued:
            self._update_message_queue()
            self._tui.request_render()
            return

        self._active_images = images
        self._active_input_origin = input_origin
        if echo:
            self._chat_log.add_user(message + (f"\n[Images: {len(images)}]" if images else ""))
        self._set_busy(True)
        self._streaming_text = ""
        self._streaming_thinking = ""

        self._worker_thread = threading.Thread(
            target=self._run_agent_turn,
            args=(message, generation, images),
            kwargs={'input_origin': input_origin} if input_origin != 'user' else {},
            daemon=True,
        )
        self._worker_thread.start()

    def _preserve_input(self, value: str, notice: str) -> None:
        draft = self._editor.get_text()
        if draft != value:
            self._editor.set_text("\n\n".join(text for text in (value, draft) if text.strip()))
        self._chat_log.add_system(notice)
        self._tui.request_render()

    def _handle_slash_command(self, cmd: str, args: str, *,
                              rename_target: tuple[str, str] | None = None, compact_target: str | None = None,
                              rename_alive: Callable[[], bool] | None = None,
                              compact_alive: Callable[[], bool] | None = None,
                              tools_target: tuple[tuple[str, ...] | None, int] | None = None,
                              tools_alive: Callable[[], bool] | None = None) -> None:
        if self._exit_requested:
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
        if cmd == "terminal-input":
            self._terminal_input.recovery_command(args, self._restore_terminal_text)
            return
        if cmd == "artifact":
            self._load_command_artifact(args)
            return

        """Serialize session commands; mutations require an idle agent."""
        if self._exit_requested:
            return
        if cmd == 'agents' and (args.strip() == 'autosave' or args.strip().startswith('autosave ')):
            self._child_autosave_command(args.strip().partition(' ')[2].strip())
            return
        if cmd == 'agents' and (args.strip() == 'saved' or args.strip().startswith('saved ')):
            try:
                self._load_saved_child_drafts(args.strip().partition(' ')[2].strip())
            except ValueError as exc:
                self._chat_log.add_system(str(exc))
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
        if cmd == "plan" and args.strip() in {"", "show"}:
            self._open_plan()
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
        if cmd == "diff":
            if args.strip() == "close":
                self._close_review()
            elif args.strip() in {"", "refresh"}:
                self._open_review()
            else:
                self._chat_log.add_system("Usage: /diff [refresh|close] — read-only repository changes")
                self._tui.request_render()
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
        mutating = tools_target is not None or rename_target is not None or not _command_is_read_only(cmd, args)
        with self._queue_lock:
            worker_active = self._worker_active
        if mutating and (worker_active or self._is_busy):
            self._preserve_input(value, "session changes require an idle agent; command kept in draft")
            return

        self._command_sequence += 1
        command = _CommandState(self._command_sequence, cmd, mutating, rename_target=rename_target,
                                rename_alive=rename_alive,
                                tools_target=tools_target, compact_target=compact_target, compact_alive=compact_alive,
                                tools_alive=tools_alive)
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

            from xdog.agent.extensions.catalog import page
            from xdog.agent.extensions.runtime import _owned_disk
            def alive() -> bool:
                return (not self._exit_requested and self._session is captured and self._command is not None
                        and self._command.id == command.id and not self._command.cancel_requested
                        and command.name.lower().startswith("ext-" + extension.lower() + "-")
                        and context == self._mcp_context_key() and not self._worker_active)
            if not alive():
                return None
            scope = ("sessions" if request["scope"] == "sessions" else
                     "tree|" + (request["session_id"] or session_id) + "|" + request["branch_id"])
            entries = await _owned_disk(self._navigation_entries, scope)
            result = await _owned_disk(page, (asdict(entry) for entry in entries), request, session_id)
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
            try:
                changes_history = command.mutating and command.name in {
                    "clear", "compact", "branch", "goto", "resume", "new",
                }
                before = self._session.messages if changes_history else []
                if command.compact_target is not None:
                    from xdog.coding.core.slash_commands import CommandResult
                    if command.compact_alive is None:
                        await self._session.compact(command.compact_target)
                    else:
                        await self._session.compact(command.compact_target, command.compact_alive)
                    result = CommandResult(output='Extension-requested compaction completed; inspect /transcript')
                elif command.tools_target is not None:
                    from xdog.coding.core.slash_commands import CommandResult
                    names, revision = command.tools_target
                    self._session.select_active_tools(names, expected_revision=revision, alive=command.tools_alive)
                    result = CommandResult(output='Active tool selection applied; permissions unchanged')
                elif command.rename_target is not None:
                    from xdog.coding.core.slash_commands import CommandResult
                    name, expected = command.rename_target
                    selected = self._session.rename_session(name, expected_name=expected, alive=command.rename_alive)
                    result = CommandResult(output=f"Session name: {selected or '(unnamed)'}")
                else:
                    result = await execute_command(command.name, args, self._session)
                changed = changes_history and (
                    command.name in {"clear", "resume", "new", "goto", "branch"} or before != self._session.messages
                )
                return {
                    "output": result.output, "exit": result.exit_requested,
                    "prompt": result.prompt, "history_changed": changed,
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
            payload = asyncio.run(owned_command())
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
        if self._is_busy:
            self._cancel_active_work()

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
        """Stop once, cancel owned async work, and fail closed on approvals."""
        if self._exit_requested:
            return
        self._poll_child_autosave(flush=True)
        self._parent_drafts.poll(self._show_parent_drafts, flush=True)
        if self._child_draft_io.busy or self._child_autosave.busy or self._parent_drafts.busy:
            if self._draft_quit_failures is None:
                self._draft_quit_failures = self._draft_write_failures()
                self._chat_log.add_system('Saving pending drafts before quitting; storage failure keeps the UI open')
            return
        self._draft_quit_failures = None
        self._exit_requested = True
        if hasattr(self, "_argument_feed"):
            self._argument_feed.close()
        self._custom_presentation.sync(None, False)
        self._terminal_input.close()
        self._saved_worker.close()
        self._editor_actions.invalidate()
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

    def _run_agent_turn(self, message: str, generation: int, images: tuple[Attachment, ...] = (),
                        *, input_origin: str = 'user') -> None:
        """Own the turn's event loop so exit cancels pre-turn work as well."""
        async def owned_turn() -> None:
            with self._queue_lock:
                if self._exit_requested:
                    return
                self._worker_loop_ref = asyncio.get_running_loop()
                self._worker_task = asyncio.current_task()
            try:
                if input_origin == 'user':
                    await self._async_agent_queue(message, generation, images)
                else:
                    await self._async_agent_queue(message, generation, images, input_origin=input_origin)
            finally:
                with self._queue_lock:
                    self._worker_loop_ref = None
                    self._worker_task = None

        producer_success = False
        try:
            asyncio.run(owned_turn())
            producer_success = True
        except (Exception, asyncio.CancelledError) as exc:
            with self._queue_lock:
                self._worker_active = False
                queued = tuple(message for message, _echo in self._pending_messages)
                self._pending_messages.clear()
                self._pending_ids.clear()
                recovered = tuple(item for group in self._local_images.values() for item in group)
                self._local_images.clear()
            if recovered:
                self._event_queue.put({"type": "restore_attachments", "images": recovered})
            stamp = _TurnStamp(generation, self._active_stamp.cancel_epoch)
            self._event_queue.put({
                "type": "queue_changed",
                "generation": generation,
                "stamp": stamp,
            })
            if isinstance(exc, asyncio.CancelledError):
                self._event_queue.put({
                    "type": "restore_draft",
                    "messages": queued,
                    "stamp": stamp,
                })
                self._event_queue.put({
                    "type": "turn_end",
                    "generation": generation,
                    "stamp": stamp,
                })
                return
            self._event_queue.put({
                "type": "error",
                "message": str(exc),
                "generation": generation,
                "restore": queued,
                "stamp": stamp,
            })

        finally:
            self._after_run_compaction.worker_finished(generation, producer_success)

    async def _async_agent_queue(
        self,
        first_message: str,
        generation: int | None = None,
        images: tuple[Attachment, ...] = (),
        *, input_origin: str = 'user',
    ) -> None:
        if generation is None:
            generation = self._worker_generation
        message = first_message
        while not self._exit_requested:
            try:
                if input_origin == 'user':
                    await self._async_agent_turn(message, images)
                else:
                    await self._async_agent_turn(message, images, input_origin=input_origin)
            except (Exception, asyncio.CancelledError) as exc:
                if input_origin == 'external_event':
                    self._external_events.finish(message,
                        status='cancelled' if isinstance(exc, asyncio.CancelledError) else 'failed', error=str(exc))
                # This tuple owns the CURRENT turn, not necessarily the worker's
                # first input. Restore it before the error/settlement receipt.
                if images and input_origin == 'user':
                    self._event_queue.put({"type": "restore_attachments", "images": images})
                raise
            if input_origin == 'external_event':
                error = self._session.agent.state.error
                self._external_events.finish(message, status='failed' if error else 'succeeded', error=error)
            self._session._expire_turn_scoped_skills()

            with self._queue_lock:
                if self._cancel_requested:
                    self._cancel_requested = False
                    settling = tuple(text for text, _echo in self._pending_messages)
                    self._pending_messages.clear()
                    self._pending_ids.clear()
                    recovered = tuple(item for group in self._local_images.values() for item in group)
                    self._local_images.clear()
                    if recovered:
                        self._event_queue.put({"type": "restore_attachments", "images": recovered})
                    next_item = None
                    self._worker_active = False
                elif self._pending_messages:
                    settling = ()
                    keys = self._pending_ids.sync(len(self._pending_messages))
                    images = self._local_images.pop(keys[0], ())
                    next_item = self._pending_messages.popleft()
                    self._pending_ids.pop()
                else:
                    settling = ()
                    next_item = None
                if next_item is None:
                    self._worker_active = False

            stamp = _TurnStamp(generation, self._active_stamp.cancel_epoch)
            if settling:
                self._event_queue.put({
                    "type": "restore_draft",
                    "messages": settling,
                    "stamp": stamp,
                })
            if next_item is None:
                break

            message, echo = next_item
            input_origin = 'user'  # Existing local next-turn queue contains explicit user submissions.
            self._event_queue.put({
                "type": "queued_message_started",
                "message": message,
                "echo": echo,
                "image_count": len(images),
                "images": images,
                "stamp": stamp,
            })

        stamp = _TurnStamp(generation, self._active_stamp.cancel_epoch)
        self._event_queue.put({"type": "queue_changed", "generation": generation, "stamp": stamp})
        self._event_queue.put({"type": "turn_end", "generation": generation, "stamp": stamp})

    async def _async_agent_turn(self, message: str, images: tuple[Attachment, ...] = (),
                                *, input_origin: str = 'user') -> None:
        """Run one agent turn without releasing the queue worker."""
        compaction_producer = self._after_run_compaction.begin()
        compaction_success = False
        stamp = self._active_stamp
        self._dispatch_stamp = stamp
        self._dispatch_images = images
        self._dispatch_input_origin = input_origin
        self._dispatch_failed_message = None
        self._session.permissions.set_request_handler(
            lambda request: self._put_turn_event(
                "permission_request",
                stamp=stamp,
                request=request,
            )
        )
        try:
            from xdog.agent.context_messages import ContextMessages
            context_store = getattr(self._session, 'context_messages', None)
            if not isinstance(context_store, ContextMessages):
                context_store = None  # Older/minimal host adapters have no owned context-message store.
            context_snapshot = (await context_store.capture()
                                if input_origin == 'user' and context_store is not None else None)
            captured_session = self._session.session_id
            self._bind_extension_dialogs()
            self._session.extensions.publish = lambda event: self._event_queue.put(
                {**event, "extension_session": captured_session})
            await self._session.initialize_extensions()
            await self._session.extensions.event("turn_start", {"user_input": message})
            await self._session.initialize_extensions()  # Lifecycle hooks may update provider metadata.
            if images:
                from xdog.ai.utils.image_files import validate_references
                await asyncio.to_thread(validate_references, self._image_parts(images), self._session.model_info)
            self._session._rebuild_system_prompt()
            await self._session._maybe_compact()
            if self._exit_requested:
                return

            self._session.validate_image_inputs(tuple((item.mime_type, len(item.data)) for item in images))
            if input_origin == 'external_event':
                from xdog.agent.external_event import message as external_message
                event_stream = await self._session.agent.prompt(external_message(message))
            elif input_origin == 'custom_context':
                from xdog.agent.core import CustomAgentMessage
                from xdog.agent.extensions.context_message import TYPE
                history = self._session.agent.messages
                if not history or not isinstance(history[-1], CustomAgentMessage) or history[-1].type != TYPE:
                    raise ValueError('Reviewed canonical context tail changed before continuation')
                from xdog.agent.extensions.context_message import images as context_images
                from xdog.agent.supervision_transfer import settled_image_work
                from xdog.ai.utils.image_files import validate_references
                await settled_image_work(lambda: validate_references(
                    context_images((history[-1],)), self._session.model_info))
                event_stream = await self._session.agent.continue_()
            elif context_snapshot is None:
                event_stream = await self._session.agent.prompt(message, images=list(self._image_parts(images)) or None)
            else:
                assert context_store is not None
                event_stream = await self._session.agent.prompt(message, images=list(self._image_parts(images)) or None,
                    initial_prompt_context=lambda: context_store.admit(context_snapshot))
            async for event in event_stream:
                self._dispatch_agent_event(event)
                if isinstance(event, MessageEndEvent):
                    await self._session.extensions.event("message_end", {
                        "role": getattr(event.message, "role", "custom")})
                if isinstance(event, ToolExecutionEndEvent):
                    await self._session.extensions.event("tool_end", {
                        "tool_name": event.tool_name, "is_error": event.is_error})

            self._session._persist()
            await self._session.extensions.event("turn_end")
            compaction_success = not bool(self._session.agent.state.error)
        finally:
            self._after_run_compaction.finish(compaction_producer, compaction_success)
            undelivered = [dict(id=item.id, content=item.content) for item in self._session.agent.drain_inputs()]
            if self._cancel_requested or self._exit_requested:
                self._session.agent.clear_all_queues()
            if undelivered:
                self._event_queue.put({"type": "inputs_settled", "inputs": undelivered})
            self._session.agent.drain_handled_inputs()
            self._dispatch_stamp = None
            self._dispatch_images = ()
            self._session.permissions.set_request_handler(
                self._on_permission_request if getattr(self._session, "external_events", None) is not None else None)

    def _put_turn_event(self, event_type: str, *, stamp: _TurnStamp | None = None, **payload: Any) -> None:
        dispatch_stamp = getattr(self, "_dispatch_stamp", None)
        stamp = stamp or dispatch_stamp or getattr(self, "_active_stamp", _TurnStamp(0, 0))
        self._event_queue.put({
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
            self._event_queue.put(data)
        elif isinstance(event, InputConsumedEvent):
            self._event_queue.put({"type": "input_consumed", "id": event.input.id, "content": event.input.content})
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
        owner.extensions.enable_editor_write(self._child_ui_lease, True)
        owner.extensions.enable_queued_input(self._child_ui_lease, True)
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
        while self._permission_prompt is None and self._permission_waiters:
            request, waiting_owner, child, stamp = self._permission_waiters.popleft()
            if waiting_owner is not None:
                if waiting_owner is not self._session.children or not waiting_owner.has_pending(child, request.id):
                    continue
            elif stamp != self._active_stamp:
                self._session.permissions.resolve(request.id, "deny")
                continue
            self._show_permission_request(request, owner=waiting_owner, child_id=child)
            changed = True
        if changed:
            self._update_hints()
            if self._permission_prompt is None and not self._is_busy:
                self._set_status_text("ready")
        return changed

    def _poll(self) -> None:
        """Poll the event queue and update the UI (called per frame)."""
        self._bind_child_approvals()
        self._editor_write_reviews.prune()
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
        changed = bool(self._event_queue.drain(self._handle_ui_event))
        changed = draft_changed or changed
        changed = self._poll_permissions() or changed
        self._after_run_compaction.poll()
        self._after_run_tools.poll()
        self._after_run_name.poll()
        changed = self._terminal_input.poll() or changed
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
        if self._plan_panel is not None:
            if self._plan_context != self._mcp_context_key():
                self._close_plan()
            elif self._plan_revision != self._session.plan.state.revision:
                self._plan_revision = self._session.plan.state.revision
                changed = True
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
            if isinstance(self._extension_dialog_panel, ExtensionCustom):
                changed = self._extension_dialog_panel.poll() or changed
        if self._navigation_panel is not None:
            changed = self._navigation_panel.poll() or changed
        if self._review_panel is not None:
            changed = self._review_panel.poll() or changed

        if self._is_busy and not self._awaiting_permission and self._run_status.phase != RunPhase.CANCELLING:
            elapsed = self._format_elapsed()
            label = f"{self._activity_label()} • {elapsed}"
            # Only repaint when the elapsed label changes.
            if label != self._last_busy_label:
                self._set_status_busy(label)
                changed = True

        external = getattr(self, "_external_events", None)
        if external is not None:
            changed = external.poll() or changed
        if changed:
            self._tui.request_render()

    def _show_permission_request(self, request: PermissionRequest, *, owner: CodingChildren | None = None,
                                 child_id: str = "") -> None:
        if self._permission_prompt is not None and (owner is not None or self._permission_child is not None):
            self._permission_waiters.append((request, owner, child_id, self._active_stamp))
            return
        if owner is not None:
            request = replace(request, summary=f"Child {child_id} · independent approval\n" + request.summary)
        self._permission_child = (owner, child_id) if owner is not None else None
        self._leave_child_fullscreen()
        self._leave_transcript_fullscreen()
        self._custom_presentation.sync(None, False)
        """Display a focused approval panel above the input."""
        self._permission_container.clear()
        stamp = self._active_stamp

        def _decide(decision: PermissionDecision) -> None:
            if self._permission_prompt is not prompt:
                return
            if owner is not None:
                if owner is not self._session.children:
                    return
                owner.resolve(child_id, request.id, decision)
            else:
                if self._active_stamp != stamp:
                    return
                self._session.permissions.resolve(request.id, decision)
            self._permission_child = None
            self._permission_container.clear()
            self._layout.permission = None
            self._permission_prompt = None
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
        if event_type == 'external_event_status':
            return  # The idle poll presents a coalesced authoritative snapshot.
        if event_type == 'mcp_elicitation_failed':
            binding = self._elicitation_bindings.get(str(event['server']))
            if binding is not None and binding[1] == event['lease']:
                self._elicitation_off(str(event['server']))
                if self._mcp_panel is not None:
                    active_elicitation_review = self._mcp_panel.elicitation_review
                    if active_elicitation_review is not None:
                        active_elicitation_review.disable('Elicitation answer refused; original owner revoked')
                    self._mcp_panel._busy = False
                    self._mcp_panel._message = 'Elicitation answer refused; no replay, inspect owner'
            return
        if event_type == 'mcp_elicitation_event':
            value = event['event']
            binding = self._elicitation_bindings.get(value['server'])
            if (binding is None or binding[1] != value['lease'] or binding[2].is_set()
                    or event['session_token'] != id(self._session) or event['context'] != self._mcp_context_key()
                    or self._mcp_panel is None):
                return
            if value['type'] in {'elicitation_url_pending', 'elicitation_url_complete'}:
                self._chat_log.add_system(str(value['notice']))
                self._tui.request_render()
                return
            if value['type'] == 'elicitation_closed':
                self._mcp_panel.elicitation_closed(value['id'])
            else:
                try:
                    self._mcp_panel.publish_elicitation(value)
                except ValueError:
                    self._elicitation_off(value['server'])
                    self._mcp_panel._busy = False
                    self._mcp_panel._message = 'Elicitation review unavailable; request canceled, no replay'
            self._tui.request_render()
            return
        if event_type == 'mcp_sampling_failed':
            binding = self._sampling_bindings.get(str(event['server']))
            if binding is not None and binding[1] == event['lease']:
                self._sampling_off(str(event['server']))
                if self._mcp_panel is not None:
                    active_review = self._mcp_panel.sampling_review
                    if active_review is not None:
                        active_review.disable('Sampling answer refused; original owner revoked')
                    self._mcp_panel._busy = False
                    self._mcp_panel._message = 'Sampling answer refused; no replay, inspect owner'
            return
        if event_type == 'mcp_sampling_event':
            value = event['event']
            binding = self._sampling_bindings.get(value['server'])
            if (binding is None or binding[1] != value['lease'] or binding[2].is_set()
                    or event['session_token'] != id(self._session) or event['context'] != self._mcp_context_key()
                    or self._mcp_panel is None):
                return
            if value['type'] == 'sampling_closed':
                self._mcp_panel.sampling_closed(value['id'])
            else:
                try:
                    self._mcp_panel.publish_sampling(value, tuple(event['models']))
                except ValueError:
                    self._sampling_off(value['server'])
                    self._mcp_panel._busy = False
                    self._mcp_panel._message = 'Sampling review unavailable; request canceled, no replay'
            self._tui.request_render()
            return
        if event_type == 'extension_context_admitted':
            if (event.get('extension_session') != self._session.session_id
                    or event.get('session_id') != self._session.session_id):
                return
            from xdog.agent.extensions.context_message import display_text
            value = {key: value for key, value in event.items() if key not in {'type', 'extension_session', 'stamp'}}
            try:
                text = display_text(value)
            except ValueError:
                text = 'Extension context display unavailable; inspect canonical transcript'
            if text:
                self._chat_log.add_system(text)
                self._tui.request_render()
            return
        if event_type == 'extension_input_queued':
            if event.get('extension_session') != self._session.session_id:
                return
            identifier = str(event['id'])
            self._routed_inputs[identifier] = QueueEntry(identifier, str(event['content']),
                str(event['delivery']), 'accepted', str(self._worker_generation))
            self._routed_images[identifier] = tuple(event['images'])
            self._extension_input_ids.add(identifier)
            self._chat_log.add_system('Reviewed extension input queued; not proof of consumption')
            self._update_message_queue()
            self._tui.request_render()
            return
        if event_type == "terminal_input_result":
            self._terminal_input.queue.accept(event['id'], event['result'])
            return
        if event_type in {"input_transformed", "input_rejected", "input_handled"}:
            key = str(event.get("id", ""))
            if event_type == "input_handled":
                if key:
                    self._routed_inputs.pop(key, None)
                    images = self._routed_images.pop(key, ())
                    if key not in self._extension_input_ids:
                        self._composer.restore(images)
                    self._extension_input_ids.discard(key)
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
        if event_type in {'extension_custom_control', 'child_extension_custom_control'}:
            item = next((item for item in self._extension_dialog_queue if item['id'] == event.get('id')), None)
            if item is None or item.get('kind') != 'custom' or not item.get('live_overlay'):
                return
            if event_type.startswith('child_'):
                if (event.get('child_session_owner') != self._session.children.token
                        or event.get('child_owner') != self._child_ui_lease
                        or event.get('session_id') != self._session.session_id
                        or event.get('child_id') != item.get('child_id')):
                    return
            elif event.get('extension_session') != self._session.session_id or item.get('child_id'):
                return
            panel = self._extension_stack.panels.get(item['id'])
            from xdog.tui.components.extension_custom import overlay_state
            previous_overlay = item.get('overlay_state')
            overlay_update = overlay_state(event.get('state'),
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
        if event_type in {"extension_custom_frame", "extension_custom_error",
                          "child_extension_custom_frame", "child_extension_custom_error"}:
            panel = self._extension_dialog_panel
            if not isinstance(panel, ExtensionCustom) or event.get("id") != panel.dialog["id"]:
                return
            if event_type.startswith("child_"):
                if (event.get("child_session_owner") != self._session.children.token
                        or event.get("child_owner") != self._child_ui_lease
                        or event.get("session_id") != self._session.session_id
                        or event.get("child_id") != panel.dialog.get("child_id")):
                    return
            elif event.get("extension_session") != self._session.session_id or panel.dialog.get("child_id"):
                return
            if event_type.endswith("_error"):
                self._chat_log.add_system("Extension custom rendering/input failed; view canceled")
            if self._dialog_contains(panel.dialog) and panel.accept(event):
                self._tui.request_render()
            return
        if event_type in {'extension_editor_write_commit', 'extension_editor_write_discard',
                          'child_extension_editor_write_commit', 'child_extension_editor_write_discard'}:
            if (event.get('extension_session', self._session.session_id) != self._session.session_id
                    or event.get('editor_write', {}).get('session_id') != self._session.session_id
                    or event_type.startswith('child_') and event.get('child_owner') != self._child_ui_lease):
                self._editor_write_reviews.discard(dict(event))
                return
            if event_type.endswith('_commit'):
                applied = self._editor_write_reviews.apply(dict(event))
                self._chat_log.add_system('Reviewed extension draft applied; not submitted' if applied else
                                         'Extension draft proposal not applied; original owner/draft changed')
            else:
                self._editor_write_reviews.discard(dict(event))
            self._tui.request_render()
            return
        if event_type in {'child_extension_input_queued', 'child_extension_input_consumed',
                          'child_extension_input_returned'}:
            if (event.get('child_session_owner') != self._session.children.token
                    or event.get('child_owner') != self._child_ui_lease
                    or event.get('session_id') != self._session.session_id
                    or event.get('queued_input_generation') !=
                    self._session.children.extensions.queued_input_generation(self._child_ui_lease)):
                return
            self._child_publish_roster()
            self._chat_log.add_system('Child extension input ' + event_type.rsplit('_', 1)[-1]
                + '; inspect the original child queue/history, never replayed into the parent')
            self._tui.request_render()
            return
        if event_type in {"child_extension_dialog", "child_extension_dialog_closed"}:
            if (event.get("child_session_owner") != self._session.children.token
                    or event.get("child_owner") != self._child_ui_lease
                    or event.get("session_id") != self._session.session_id):
                return
            if event_type == "child_extension_dialog":
                if (event['dialog'].get('operation') == 'queue_input'
                        and event.get('queued_input_generation') !=
                        self._session.children.extensions.queued_input_generation(self._child_ui_lease)):
                    return
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
        if event_type == 'extension_name_after_run':
            if event.get('session_id') == self._session.session_id:
                self._chat_log.add_system('Reviewed name proposal: ' + str(event.get('status', 'unknown')))
            return
        if event_type == 'extension_compaction_after_run':
            if event.get('session_id') == self._session.session_id:
                status = event.get('status')
                self._chat_log.add_system('Extension compaction: ' + str(status) +
                    ('; waiting for original producer settlement' if status == 'staged' else ''))
            return
        if event_type == "extension_dialog_accepted":
            if event.get("extension_session") == self._session.session_id:
                self._accept_extension_navigation(event)
            return
        if event_type == "extension_dialog_closed":
            review = self._extension_navigation_review
            if review is not None and event.get("id") == review[2]["id"]:
                self._extension_navigation_review = None
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
        if event_type == "mcp_watch_notice":
            if (self._mcp_panel is not None and event.get("lease") == self._mcp_watch_lease
                    and event.get("context") == self._mcp_context_key()):
                self._mcp_panel.publish_watch(event["data"])
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
            if command is not None and command.name == "plan" and not event.get("error"):
                self._open_plan()
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
                if self._review_panel is not None:
                    self._close_review()
                self._file_completions.close()
                self._file_completions = WorkspaceFileCompletionProvider(self._session.working_dir)
                self._editor.set_autocomplete_provider(self._file_completions)
                if command is not None and command.name in {"resume", "new", "branch", "goto"}:
                    self._editor.reset_history()
                self._chat_log.clear_all()
                self._streaming_text = ""
                self._streaming_thinking = ""
                self._replay_history()
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
            finished_owner = str(command.id) if command is not None else ""
            success = not event.get("failed") and not event.get("cancelled")
            if self._finish_extension_navigation(finished_owner, success):
                self._extension_browser = None
            else:
                self._finish_extension_browser(finished_owner, success)
            context_trigger = getattr(self, '_context_trigger', None)
            if context_trigger is not None and command is not None and context_trigger[0] == command.id:
                self._context_trigger = None
                history = self._session.agent.messages
                if (success and not command.cancel_requested and self._session is context_trigger[1]
                        and self._mcp_context_key() == context_trigger[2] and history
                        and getattr(history[-1], 'data', {}).get('id') == context_trigger[3]
                        and not self._worker_active and not self._session.permissions.has_pending_requests):
                    self._start_turn('', echo=False, input_origin='custom_context')

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
