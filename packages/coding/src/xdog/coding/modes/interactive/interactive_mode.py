"""Interactive mode: TUI-based coding agent interface.

Uses tui.TUI as the rendering engine and subscribes to agent.Agent
events for streaming responses, tool execution display, and session
management.

Component tree:
    TUI
    └── root (Container)
        ├── header (Text)
        ├── chat_log (ChatLog)
        ├── status_container (Container)
        ├── footer (FooterComponent)
        ├── editor (CustomEditorComponent)
        ├── message_queue (Container)
        └── permission_prompt (Container)
"""

from __future__ import annotations

import asyncio
import logging
import queue
import random
import threading
import time
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from xdog.agent import (
    AgentEndEvent,
    AgentEvent,
    MessageEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
    TurnEndEvent,
)
from xdog.ai.types import (
    AssistantMessage,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.permissions import PermissionDecision, PermissionRequest
from xdog.coding.core.slash_commands import execute_command, parse_slash_command
from xdog.coding.modes.interactive.components.chat_log import ChatLog
from xdog.coding.modes.interactive.components.custom_editor import CustomEditorComponent
from xdog.coding.modes.interactive.components.footer import FooterComponent
from xdog.coding.modes.interactive.components.permission_prompt import PermissionPromptComponent
from xdog.coding.modes.interactive.components.tool_execution import ToolExecutionComponent
from xdog.coding.modes.interactive.theme import create_default_theme
from xdog.tui.components.bounded_details import BoundedDetails
from xdog.tui.components.inline_layout import CompactText, InlineLayout
from xdog.tui.components.text import Text
from xdog.tui.event_queue import EventQueue, thaw_event
from xdog.tui.tui import TUI, Container
from xdog.tui.utils import sanitize_terminal_text

logger = logging.getLogger(__name__)

# Waiting phrases for the shimmer animation
@dataclass(frozen=True, slots=True)
class _TurnStamp:
    generation: int
    cancel_epoch: int


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
        self._last_ctrl_c_at = 0.0
        self._is_busy = False
        self._busy_started: float | None = None
        self._last_busy_label = ""
        self._streaming_text = ""
        self._streaming_thinking = ""
        self._stream_sequence = 0
        self._stream_id = "assistant-0"
        self._tool_components: dict[str, ToolExecutionComponent] = {}
        self._permission_prompt: PermissionPromptComponent | None = None
        self._awaiting_permission = False
        self._details_expanded = verbose
        self._details_open = verbose
        self._pending_messages: deque[tuple[str, bool]] = deque()
        self._queue_lock = threading.Lock()
        self._worker_active = False
        self._cancel_requested = False
        self._worker_generation = 0
        self._cancel_epoch = 0
        self._active_stamp = _TurnStamp(0, 0)
        self._dispatch_stamp: _TurnStamp | None = None

        # Event queue for thread-safe UI updates from agent events
        self._event_queue = EventQueue()

        # Build component tree
        self._tui = TUI()

        self._header = Text("", 1, 0)
        self._chat_log = ChatLog(self._theme)
        self._chat_log.set_details_expanded(False)
        self._status_container = Container()
        self._footer = FooterComponent(self._theme)
        self._editor = CustomEditorComponent(self._theme)
        self._message_queue_container = Container()
        self._queue_text = CompactText("", 0, 0)
        self._message_queue_container.add_child(self._queue_text)
        self._permission_container = Container()
        self._work_container = Container()
        self._details_panel = BoundedDetails(self._chat_log.detail_records, max_rows=8)
        self._banner_added = False

        # Compatibility handles retained for callers/tests; activity and metadata
        # now occupy one stable footer row.
        self._status_text: Text | None = None
        self._status_loader = None
        self._layout = InlineLayout(
            transcript=self._chat_log,
            work=self._work_container,
            status=self._footer,
            editor=self._editor,
            permission=None,
            details=self._details_panel if self._details_open else None,
            queue=None,
        )
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
        self._unsubscribe = self._session.agent.subscribe(self._on_agent_event)

    def _handle_global_input(self, event: Any) -> dict[str, object] | None:
        """Handle bounded details, suspension, and active-turn cancellation."""
        if self._permission_prompt is not None or self._editor.autocomplete_active:
            return None
        if self._details_open and self._permission_prompt is None:
            if event.matches("escape"):
                self._close_details()
                return {"consume": True}
            if event.matches("left") or event.matches("right") or event.matches("pageup") or event.matches("pagedown"):
                self._details_panel.handle_input(event)
                self._tui.request_render()
                return {"consume": True}
        if event.matches("escape") and self._is_busy:
            self._handle_escape()
            return {"consume": True}
        if event.matches("ctrl+z"):
            if not self._tui.suspend():
                self._set_status_text("suspend is not supported on this platform")
                self._tui.request_render()
            return {"consume": True}
        if event.matches("ctrl+o"):
            self._details_open = not self._details_open
            if self._details_open:
                self._details_panel.show_latest()
            self._layout.details = self._details_panel if self._details_open else None
            self._tui.request_render()
            return {"consume": True}
        return None

    def _close_details(self) -> None:
        self._details_open = False
        self._layout.details = None
        self._tui.set_focus(self._editor)
        self._editor.set_focus(True)
        self._tui.request_render()

    def run(self) -> None:
        """Start the interactive TUI (blocking)."""
        self._update_header()
        self._update_footer()

        model_name = self._session.model or "unknown"
        thinking = self._session.agent.options.thinking or "off"
        self._chat_log.add_system(
            f"session:{self._session.session_id[:8]} | {model_name} | thinking:{thinking}"
        )

        # Replay existing messages
        self._replay_history()

        # Register tick callback for polling events
        self._tui.on_tick(self._poll)

        try:
            self._tui.start()
        finally:
            self._session.permissions.set_request_handler(None)
            self._session.permissions.deny_all()
            if self._unsubscribe is not None:
                self._unsubscribe()

    def _replay_history(self) -> None:
        """Replay messages, including persisted tool calls and their results."""
        tool_components: dict[str, Any] = {}
        for msg in self._session.messages:
            if isinstance(msg, UserMessage):
                text = msg.content if isinstance(msg.content, str) else ""
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

    def _set_busy(self, busy: bool) -> None:
        self._is_busy = busy
        if busy:
            self._busy_started = time.time()
            phrase = random.choice(WAITING_PHRASES)
            self._set_status_busy(f"{phrase}...")
        else:
            self._busy_started = None
            self._last_busy_label = ""
            self._set_status_text("ready")

    def _format_elapsed(self) -> str:
        if self._busy_started is None:
            return "0s"
        total = int(time.time() - self._busy_started)
        if total < 60:
            return f"{total}s"
        m, s = divmod(total, 60)
        return f"{m}m {s}s"

    # -- Input handling --

    def _handle_submit(self, text: str) -> None:
        """Handle editor submission."""
        value = text.strip()
        if not value:
            return

        self._editor.add_to_history(value)

        # Check for slash commands
        parsed = parse_slash_command(value)
        if parsed is not None:
            cmd, args = parsed
            self._handle_slash_command(cmd, args)
            return

        # Regular message
        self._start_turn(value)

    def _update_message_queue(self) -> None:
        """Render messages waiting behind the active turn."""
        with self._queue_lock:
            pending = list(self._pending_messages)
        self._footer.set_queue_count(len(pending))
        if not pending:
            self._queue_text.set_text("")
            self._layout.queue = None
            return
        previews: list[str] = []
        for index, (message, _echo) in enumerate(pending[:2], 1):
            preview = sanitize_terminal_text(message).replace("\n", " ↵ ")
            if len(preview) > 120:
                preview = preview[:120] + "…"
            previews.append(f"  {index}. {preview}")
        text = f"Queued messages ({len(pending)}):\n" + "\n".join(previews)
        if len(pending) > 2:
            text += f"\n  … {len(pending) - 2} more queued"
        self._queue_text.set_text(self._theme.system(text))
        self._layout.queue = self._message_queue_container

    def _start_turn(self, message: str, *, echo: bool = True) -> None:
        """Start immediately or enqueue behind the active worker."""
        with self._queue_lock:
            if self._worker_active:
                self._pending_messages.append((message, echo))
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

        if echo:
            self._chat_log.add_user(message)
        self._set_busy(True)
        self._streaming_text = ""
        self._streaming_thinking = ""

        thread = threading.Thread(
            target=self._run_agent_turn,
            args=(message, generation),
            daemon=True,
        )
        thread.start()

    def _handle_slash_command(self, cmd: str, args: str) -> None:
        """Handle a slash command."""
        if cmd in ("quit", "exit"):
            self._request_exit()
            return

        # Run async commands in a new event loop
        stamp = self._active_stamp

        def _run() -> None:
            result = asyncio.run(execute_command(cmd, args, self._session))
            self._event_queue.put({
                "type": "command_result",
                "output": result.output,
                "exit": result.exit_requested,
                "prompt": result.prompt,
                "stamp": stamp,
            })

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()

    def _handle_ctrl_c(self) -> None:
        """Ctrl+C: clear input → warn → exit on double-press."""
        now = time.time()
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

        queued_text = "\n\n".join(message for message, _echo in queued)
        draft = self._editor.get_text()
        restored = "\n\n".join(text for text in (queued_text, draft) if text.strip())
        if restored:
            self._editor.set_text(restored)

        for component in self._tool_components.values():
            component.set_canceled()
        self._tool_components.clear()

        self._session.cancel()
        self._permission_container.clear()
        self._layout.permission = None
        self._permission_prompt = None
        self._awaiting_permission = False
        self._tui.set_focus(self._editor)
        self._update_message_queue()
        self._chat_log.drop_assistant(self._stream_id)
        self._set_status_text("cancelling active turn...")
        notice = "cancelling active turn"
        if queued:
            notice += f"; restored {len(queued)} queued message(s) to editor"
        self._chat_log.add_system(notice)
        self._tui.request_render()

    def _handle_escape(self) -> None:
        """Escape cancels the active model turn or running tool task."""
        if self._details_open:
            self._close_details()
            return
        if self._is_busy:
            self._cancel_active_work()

    def _request_exit(self) -> None:
        """Request clean exit."""
        if self._exit_requested:
            return
        self._exit_requested = True
        self._session.dispose()
        self._tui.stop()

    # -- Agent execution --

    def _run_agent_turn(self, message: str, generation: int) -> None:
        """Drain the active turn and every message queued behind it."""
        try:
            asyncio.run(self._async_agent_queue(message, generation))
        except (Exception, asyncio.CancelledError) as exc:
            with self._queue_lock:
                self._worker_active = False
                queued = tuple(message for message, _echo in self._pending_messages)
                self._pending_messages.clear()
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

    async def _async_agent_queue(
        self,
        first_message: str,
        generation: int | None = None,
    ) -> None:
        if generation is None:
            generation = self._worker_generation
        message = first_message
        while True:
            await self._async_agent_turn(message)
            self._session._expire_turn_scoped_skills()

            with self._queue_lock:
                if self._cancel_requested:
                    self._cancel_requested = False
                    settling = tuple(text for text, _echo in self._pending_messages)
                    self._pending_messages.clear()
                    next_item = None
                    self._worker_active = False
                elif self._pending_messages:
                    settling = ()
                    next_item = self._pending_messages.popleft()
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
            self._event_queue.put({
                "type": "queued_message_started",
                "message": message,
                "echo": echo,
                "stamp": stamp,
            })

        stamp = _TurnStamp(generation, self._active_stamp.cancel_epoch)
        self._event_queue.put({"type": "queue_changed", "generation": generation, "stamp": stamp})
        self._event_queue.put({"type": "turn_end", "generation": generation, "stamp": stamp})

    async def _async_agent_turn(self, message: str) -> None:
        """Run one agent turn without releasing the queue worker."""
        stamp = self._active_stamp
        self._dispatch_stamp = stamp
        self._session.permissions.set_request_handler(
            lambda request: self._put_turn_event(
                "permission_request",
                stamp=stamp,
                request=request,
            )
        )
        try:
            self._session._rebuild_system_prompt()
            await self._session._maybe_compact()

            event_stream = await self._session.agent.prompt(message)
            async for event in event_stream:
                self._dispatch_agent_event(event)

            self._session._persist()
        finally:
            self._dispatch_stamp = None
            self._session.permissions.set_request_handler(None)

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
        if isinstance(event, MessageStartEvent):
            if isinstance(event.message, AssistantMessage):
                self._put_turn_event("assistant_start")

        elif isinstance(event, (MessageUpdateEvent, MessageEndEvent)):
            # Some providers emit no incremental text event and deliver the
            # complete response only in MessageEndEvent. Handling both keeps
            # ordinary assistant text visible for streaming and non-streaming
            # response paths.
            msg = event.message
            if isinstance(msg, AssistantMessage):
                if isinstance(event, MessageEndEvent) and msg.stop_reason == "error":
                    self._put_turn_event(
                        "error",
                        message=msg.error_message or "Provider request failed",
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
            if isinstance(final, AssistantMessage) and final.stop_reason == "error":
                self._put_turn_event(
                    "error",
                    message=final.error_message or "Provider request failed",
                )

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

    def _poll(self) -> None:
        """Poll the event queue and update the UI (called per frame)."""
        changed = False

        while True:
            try:
                event = self._event_queue.get_nowait()
                self._handle_ui_event(event)
                changed = True
            except queue.Empty:
                break

        if self._is_busy and not self._awaiting_permission and not self._cancel_requested:
            elapsed = self._format_elapsed()
            label = f"thinking... • {elapsed}"
            # Only repaint when the elapsed label changes.
            if label != self._last_busy_label:
                self._set_status_busy(label)
                changed = True

        if changed:
            self._tui.request_render()

    def _show_permission_request(self, request: PermissionRequest) -> None:
        """Display a focused approval panel immediately after the input."""
        self._permission_container.clear()
        stamp = self._active_stamp

        def _decide(decision: PermissionDecision) -> None:
            if self._permission_prompt is not prompt or self._active_stamp != stamp:
                return
            self._session.permissions.resolve(request.id, decision)
            self._permission_container.clear()
            self._layout.permission = None
            self._permission_prompt = None
            self._awaiting_permission = False
            self._tui.set_focus(self._editor)
            self._editor.set_focus(True)
            if self._is_busy:
                self._set_status_busy("resuming...")
            self._tui.request_render()

        prompt = PermissionPromptComponent(request, self._theme, _decide)
        self._permission_prompt = prompt
        self._permission_container.add_child(prompt)
        self._layout.permission = prompt
        self._editor.set_focus(False)
        self._tui.set_focus(prompt)
        self._awaiting_permission = True
        self._set_status_text("awaiting tool permission")

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
        """Handle a UI event from the event queue."""
        event = thaw_event(event)
        event_type = event.get("type")
        if event_type in _TURN_EVENT_TYPES:
            stamp = event.get("stamp")
            if stamp is not None and stamp != self._active_stamp:
                return

        if event_type == "assistant_start":
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
            self._streaming_text = text
            self._streaming_thinking = thinking
            self._chat_log.update_assistant(
                text,
                self._stream_id,
                thinking=thinking,
            )

        elif event_type == "tool_call":
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
            if event.get("echo", True):
                self._chat_log.add_user(event.get("message", ""))
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
            self._set_busy(False)
            self._finalize_streaming_assistant()
            self._update_footer()
            self._update_header()

        elif event_type == "command_result":
            output = event.get("output", "")
            if output:
                self._chat_log.add_system(output)
            if event.get("exit"):
                self._request_exit()
            prompt = event.get("prompt", "")
            if prompt:
                # A skill command: carry its instructions into a real turn.
                self._start_turn(prompt, echo=False)
            self._update_footer()

        elif event_type == "error":
            generation = event.get("generation")
            if generation is not None and generation != self._worker_generation:
                return
            self._restore_editor_messages(event.get("restore", ()))
            message = event.get("message", "Unknown error")
            self._set_busy(False)
            self._chat_log.drop_assistant(self._stream_id)
            self._chat_log.add_system(self._theme.error(f"Error: {message}"))
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
