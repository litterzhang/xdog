"""Native idle dispatcher; file/source semantics remain outside the harness."""
from __future__ import annotations

from typing import Any, cast

from xdog.agent.external_event import message
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.external_events.binding import ExternalEventBinding, session_busy
from xdog.coding.core.external_events.messages import serialize
from xdog.tui.utils import sanitize_terminal_text


class NativeExternalEvents:
    def __init__(self, ui: Any) -> None:
        self.ui = ui
        self.binding: ExternalEventBinding | None = None
        self._display: tuple[int, int, str] | None = None

    def start(self) -> None:
        if isinstance(self.ui._session, AgentSession):
            self.binding = self.ui._session.enable_external_events(
                notify=lambda: self.ui._event_queue.put({"type": "external_event_status"}))
            if hasattr(self.ui, "_on_permission_request"):
                self.ui._session.permissions.set_request_handler(self.ui._on_permission_request)

    def human_input(self) -> None:
        if self.binding is not None:
            self.binding.inbox.human_input()

    def poll(self) -> bool:
        if self.binding is None:
            return False
        ui, session = self.ui, self.ui._session
        snapshot = self.binding.inbox.snapshot()
        signature = (cast(int, snapshot["registration_count"]), cast(int, snapshot["pending_count"]),
                     str(snapshot["paused_reason"]))
        changed = signature != self._display
        if changed:
            self._display = signature
            if signature[0] or signature[1]:
                ui._chat_log.add_system(sanitize_terminal_text(
                    f"External events: routes={signature[0]} pending={signature[1]}"
                    + (f" · {signature[2]}" if signature[2] else "")))
        busy = (ui._exit_requested or ui._worker_active or ui._is_busy or ui._command is not None
                or ui._child_image_starting or session_busy(session)
                or any(thread is not None and thread.is_alive() for thread in
                       (ui._worker_thread, ui._command_thread))
                or ui._extension_navigation is not None or ui._extension_navigation_review is not None
                or bool(getattr(ui._terminal_worker, "busy", False)))
        human = bool(ui._pending_messages or ui._routed_inputs or ui._editor.get_text().strip() or ui._composer.items)
        job = self.binding.inbox.take(busy=busy, human_pending=human)
        if job is None:
            return changed
        try:
            serialized = serialize(job)
            ui._chat_log.add_system(sanitize_terminal_text(f"External event · {job.registration_id}/{job.route_id}"))
            ui._start_turn(serialized, echo=False, input_origin="external_event")
        except Exception as exc:
            self.binding.inbox.complete(job.id, status="failed", error=str(exc))
            raise
        return True

    def finish(self, serialized: str, *, status: str, error: str | None = None) -> None:
        if self.binding is not None:
            identifier = message(serialized).data["id"]
            self.binding.inbox.complete(identifier, status=status, error=error)
