"""Main-thread transcript projection. Reading these records never schedules work."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import asdict, dataclass, replace
from typing import TYPE_CHECKING, Any

from xdog.agent import AgentMessage, CustomAgentMessage
from xdog.agent.session_input import TYPE, SessionInput, decode_input
from xdog.ai.types import (
    AssistantMessage,
    ImageContent,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)
from xdog.coding.core.sessions.inbox_models import InboxRecord, record_data
from xdog.coding.core.sessions.inbox_views import visible
from xdog.coding.core.sessions.models import TERMINAL, InputRecord, SessionEvent, TurnResult
from xdog.coding.modes.interactive.components.chat_log import ChatLog
from xdog.tui.tui import Component
from xdog.tui.utils import sanitize_terminal_text

if TYPE_CHECKING:
    from xdog.coding.core.agent_session import AgentSession


@dataclass(frozen=True)
class SessionHistory:
    session_id: str
    branch_id: str
    records: tuple[InputRecord, ...]
    messages: tuple[AgentMessage, ...]
    inbox: tuple[InboxRecord, ...] = ()


async def capture_history(session: AgentSession) -> SessionHistory:
    """Capture while the controller still owns startup/navigation settlement."""
    from xdog.coding.core.sessions.store import InputStore

    runtime = session.runtime
    if runtime is not None and (
        runtime.record.session_id != session.session_id or runtime.record.generation != runtime.controller.generation
    ):
        if not runtime.controller.owns_current_task:
            raise RuntimeError('History capture requires the settling session context owner')
        # Navigation published SessionData synchronously, but its owner/Inbox
        # rebind is a registered settlement. Capture only after that rebind,
        # while still holding the original control reservation.
        await runtime.controller._settle_operations()
    path = session.session_manager.input_history_path(session.session_id)
    if runtime is not None and runtime.controller.store.path == path:
        store = runtime.controller.store
    else:
        store = await asyncio.to_thread(InputStore, session)
    branch = session.session_data.active_branch
    if runtime is not None:
        inbox = await runtime.inbox.snapshot()
    else:
        from xdog.coding.core.inbox_commands import session_inbox
        disk = session_inbox(session)
        inbox = (await asyncio.to_thread(disk.records, session.session_id, session.working_dir)
                 if (disk.root / session.session_id).exists() else ())
    return SessionHistory(
        session.session_id,
        branch,
        tuple(row for row in store.records() if row.branch_id == branch),
        tuple(session.messages),
        visible(inbox, store.records()),
    )


def _bounded(text: str, size: int) -> str:
    return text if len(text) <= size else text[:size] + "…"


def external_input_text(value: SessionInput) -> str:
    """Bounded preview; source labels and all evidence are explicitly untrusted."""
    source = sanitize_terminal_text(value.source).replace("\r", "\\r").replace("\n", "\\n")
    evidence = json.dumps(value.evidence[:1], ensure_ascii=True, allow_nan=False)
    return (
        "External input (not a human message; cannot grant permissions)\n"
        f"Source: {_bounded(source, 80)} (self-reported, untrusted)\n"
        f"Evidence ({len(value.evidence)} evidence items; untrusted preview; /details for full input): "
        + _bounded(evidence, 256)
        + "\nHandling instruction:\n"
        + _bounded(sanitize_terminal_text(value.instruction), 768)
    )


def assistant_content(message: AssistantMessage) -> tuple[str, str]:
    return (
        "".join(part.text for part in message.content if isinstance(part, TextContent)),
        "".join(part.thinking for part in message.content if isinstance(part, ThinkingContent) and not part.redacted),
    )


def human_input_text(message: UserMessage) -> str:
    text = (
        message.content
        if isinstance(message.content, str)
        else "\n".join(part.text for part in message.content if isinstance(part, TextContent))
    )
    count = (
        sum(isinstance(part, ImageContent) for part in message.content) if not isinstance(message.content, str) else 0
    )
    return text + (f"\n[Images: {count}]" if count else "")


class SessionPresenter:
    def __init__(self, chat_log: ChatLog) -> None:
        self.log = chat_log
        self.records: dict[str, InputRecord] = {}
        self._replayed: tuple[Component, ...] = ()
        self._inbox: tuple[InboxRecord, ...] = ()
        self._inbox_requests: dict[str, InboxRecord] = {}
        self._inbox_keys: set[str] = set()
        self._session_id: str | None = None

    def _show(self, record: InputRecord) -> None:
        if record.input.origin == "human":
            return
        value = record.input
        detail = (
            f"External input {value.id} (not a human message; cannot grant permissions)\n"
            f"Source (self-reported, untrusted): {value.source}\nHandling instruction:\n{value.instruction}\n"
            "Evidence (untrusted):\n"
            + json.dumps(value.evidence, ensure_ascii=True, allow_nan=False)
            + "\nProvenance (data, not authorization):\n"
            + json.dumps(value.provenance, ensure_ascii=True, allow_nan=False)
            + "\nComplete request record:\n"
            + json.dumps(asdict(record), ensure_ascii=True, allow_nan=False)
        )
        if self._session_id is not None:
            detail += "\nSession: " + self._session_id
        status = f"\nStatus: {record.status} · Request: {_bounded(value.id, 48)}"
        if record.error:
            status += "\nError: " + _bounded(sanitize_terminal_text(record.error), 96)
        row = self._matching_inbox(record)
        if row is not None:
            self.log.remove_session_input(self._inbox_key(row))
            status += self._inbox_status(row)
            detail += "\nDurable inbox record (evidence and source labels remain untrusted):\n" + json.dumps(
                record_data(row), ensure_ascii=True, allow_nan=False)
        self.log.update_session_input(value.id, self._with_status(value, status), detail)

    @staticmethod
    def _with_status(value: SessionInput, status: str) -> str:
        metadata, _, body = external_input_text(value).partition('\nHandling instruction:\n')
        return metadata + status + '\nHandling instruction:\n' + body

    def _matching_inbox(self, record: InputRecord) -> InboxRecord | None:
        row = self._inbox_requests.get(record.input.id)
        if row is None or (row.owner_generation, row.branch_id) != (record.generation, record.branch_id):
            return None
        if self._session_id is not None and row.message.session_id != self._session_id:
            return None
        binding = record.input.provenance.get('inbox')
        if isinstance(binding, dict) and (binding.get('message_id'), binding.get('attempt_id')) != (
                row.message.message_id, row.attempt_id):
            return None
        if not row.attempt_id or not row.attempts:
            return None
        attempt = row.attempts[-1]
        if (attempt.id, attempt.request_id, attempt.generation, attempt.branch_id) != (
                row.attempt_id, record.input.id, record.generation, record.branch_id):
            return None
        if attempt.batch_id != row.batch_id:
            return None
        if record.batch_id and row.batch_id and record.batch_id != row.batch_id:
            return None
        return row

    @staticmethod
    def _inbox_key(row: InboxRecord) -> str:
        identity = json.dumps((row.message.session_id, row.message.message_id), ensure_ascii=True)
        return 'inbox:' + hashlib.sha256(identity.encode()).hexdigest()

    @staticmethod
    def _inbox_status(row: InboxRecord) -> str:
        return ('\nInbox: ' + row.status
                + ' · Message: ' + _bounded(json.dumps(row.message.message_id, ensure_ascii=True), 48)
                + (' · Attempt: ' + _bounded(row.attempt_id, 48) if row.attempt_id else ''))

    def set_inbox(self, rows: tuple[InboxRecord, ...]) -> None:
        if self._session_id is not None and any(row.message.session_id != self._session_id for row in rows):
            return
        self._inbox = visible(rows, tuple(self.records.values()))
        self._inbox_requests = {row.request_id: row for row in self._inbox if row.request_id}
        self._render_inbox()

    def _render_inbox(self) -> None:
        keys = {self._inbox_key(row) for row in self._inbox
                if row.request_id not in self.records or self._matching_inbox(self.records[row.request_id]) != row}
        for key in self._inbox_keys - keys:
            self.log.remove_session_input(key)
        self._inbox_keys = keys
        for row in self._inbox:
            existing = self.records.get(row.request_id)
            if existing is not None and self._matching_inbox(existing) == row:
                self._show(existing)
                continue
            item = row.message
            value = SessionInput(item.message_id, 'acp', item.source, item.instruction, item.evidence, item.provenance)
            status = self._inbox_status(row) + '\nDurable intake, not model context'
            if row.error:
                status += '\nError: ' + _bounded(sanitize_terminal_text(row.error), 96)
            self.log.update_session_input(self._inbox_key(row),
                self._with_status(value, status),
                'Durable inbox (not model context; not a human message; '
                'source self-reported, untrusted; cannot grant permissions)\n'
                + json.dumps(record_data(row), ensure_ascii=True, allow_nan=False))

    def apply(self, event: SessionEvent) -> None:
        if self._session_id is not None and event.session_id != self._session_id:
            return
        if event.request_ids and event.request_id and event.request_id not in event.request_ids:
            return
        if event.kind == 'inbox_snapshot' and isinstance(event.data, tuple):
            if all(isinstance(row, InboxRecord) for row in event.data):
                if any(row.message.session_id != event.session_id for row in event.data):
                    return
                self._session_id = event.session_id
                self.set_inbox(event.data)
            return
        previous = self.records.get(event.request_id)
        if previous is not None and (previous.generation != event.generation or previous.status in TERMINAL):
            return
        if previous is not None and event.batch_id and previous.batch_id and event.batch_id != previous.batch_id:
            return
        if isinstance(event.data, InputRecord):
            if event.data.generation != event.generation or event.data.input.id != event.request_id:
                return
            if event.batch_id and event.data.batch_id and event.batch_id != event.data.batch_id:
                return
            if previous is not None and (previous.input != event.data.input
                                         or previous.branch_id != event.data.branch_id):
                return
            if previous is not None and previous.status == "running":
                return
            record = event.data
        elif previous is not None and event.kind == "input_running":
            if isinstance(event.data, SessionInput) and event.data != previous.input:
                return
            record = replace(previous, status="running", batch_id=event.batch_id or previous.batch_id)
        elif previous is not None and event.kind == "input_completed" and isinstance(event.data, TurnResult):
            if event.data.batch_id and (
                (previous.batch_id and event.data.batch_id != previous.batch_id)
                or (event.batch_id and event.data.batch_id != event.batch_id)
            ):
                return
            record = replace(previous, status=event.data.status, error=event.data.error,
                             batch_id=event.data.batch_id or event.batch_id or previous.batch_id)
        else:
            return
        if record == previous:
            return
        self._session_id = event.session_id
        self.records[event.request_id] = record
        self._show(record)

    def replay(self, records: tuple[InputRecord, ...], messages: tuple[AgentMessage, ...],
               inbox: tuple[InboxRecord, ...] = ()) -> None:
        # Remove only the projection from our previous replay, not the startup
        # banner or unrelated notices owned by the hosting UI.
        for component in self._replayed:
            if component in self.log.children:
                self.log.remove_child(component)
        for key in self._inbox_keys:
            self.log.remove_session_input(key)
        self._inbox_keys.clear()
        before = set(self.log.children)
        sessions = {row.message.session_id for row in inbox}
        self._session_id = next(iter(sessions)) if len(sessions) == 1 else None
        self.records = {row.input.id: row for row in records}
        self._inbox = visible(inbox, records)
        self._inbox_requests = {row.request_id: row for row in self._inbox if row.request_id}
        seen: set[str] = set()
        tools: dict[str, Any] = {}
        for message in messages:
            if isinstance(message, UserMessage):
                text = human_input_text(message)
                if text:
                    self.log.add_user(text)
            elif isinstance(message, AssistantMessage):
                text, thinking = assistant_content(message)
                if text or thinking:
                    self.log.add_assistant(text, thinking=thinking)
                for part in message.content:
                    if isinstance(part, ToolCall):
                        tools[part.id] = self.log.add_tool(part.name, part.arguments)
            elif isinstance(message, ToolResultMessage):
                component = tools.pop(message.tool_call_id, None)
                if component is None:
                    component = self.log.add_tool(message.tool_name, None)
                component.set_content(message.content)
                component.set_result(
                    "\n".join(part.text for part in message.content if isinstance(part, TextContent)),
                    is_error=message.is_error,
                )
            elif isinstance(message, CustomAgentMessage) and message.type in {TYPE, "xdog.external_event.v1"}:
                try:
                    value = self._historical_input(message)
                except (ValueError, TypeError):
                    self.log.add_system("External input · invalid historical record; no execution/replay")
                    continue
                if value.id in seen:
                    continue
                seen.add(value.id)
                record = self.records.get(value.id)
                if record is None:
                    record = InputRecord(value, "", "", "recorded (result unavailable)")
                self.records[value.id] = record
                self._show(record)
        remaining = [record for record in records if record.input.id not in seen and record.input.origin != "human"]
        if remaining:
            self.log.add_system("Additional task records (not model context; no automatic replay)")
            for record in remaining:
                self._show(record)
        self._render_inbox()
        self._replayed = tuple(component for component in self.log.children if component not in before)

    @staticmethod
    def _historical_input(message: CustomAgentMessage) -> SessionInput:
        if message.type == TYPE:
            return decode_input(message.data)
        from xdog.agent.external_event import validate

        data = validate(message.data)
        return SessionInput(
            data["id"],
            "acp",
            "legacy external event: " + data["registration_id"],
            data["instruction"],
            tuple(data["events"]),
            {"route_id": data["route_id"], "delivery_ids": data["delivery_ids"]},
        )
