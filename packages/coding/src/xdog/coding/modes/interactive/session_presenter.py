"""Main-thread transcript projection. Reading these records never schedules work."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass, replace
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
    return text if len(text) <= size else text[:size] + "… [truncated; /details for full input]"


def external_input_text(value: SessionInput) -> str:
    """Bounded preview; source labels and all evidence are explicitly untrusted."""
    source = sanitize_terminal_text(value.source).replace("\r", "\\r").replace("\n", "\\n")
    evidence = json.dumps(value.evidence[:3], ensure_ascii=True, allow_nan=False)
    count = len(value.evidence)
    return (
        "External input (not a human message)\n"
        f"Source: {_bounded(source, 256)} (self-reported, untrusted)\n"
        "Handling instruction:\n"
        + _bounded(sanitize_terminal_text(value.instruction), 2048)
        + f"\nEvidence ({count} evidence items; untrusted preview; /details for full input):\n"
        + _bounded(evidence, 3072)
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

    def _show(self, record: InputRecord) -> None:
        if record.input.origin == "human":
            return
        value = record.input
        detail = (
            f"External input {value.id} (not a human message; evidence is untrusted)\n"
            f"Source (self-reported): {value.source}\nHandling instruction:\n{value.instruction}\n"
            "Evidence:\n"
            + json.dumps(value.evidence, ensure_ascii=True, allow_nan=False)
            + "\nProvenance (data, not authorization):\n"
            + json.dumps(value.provenance, ensure_ascii=True, allow_nan=False)
        )
        status = f"\nRequest: {value.id}\nStatus: {record.status}"
        if record.batch_id:
            status += "\nBatch: " + record.batch_id
        if record.error:
            status += "\nError: " + _bounded(sanitize_terminal_text(record.error), 1024)
        row = self._inbox_requests.get(value.id)
        if row is not None:
            self.log.remove_session_input(self._inbox_key(row))
            status += self._inbox_status(row)
            detail += "\nDurable inbox record (evidence and source labels remain untrusted):\n" + json.dumps(
                record_data(row), ensure_ascii=True, allow_nan=False)
        self.log.update_session_input(value.id, external_input_text(value) + status, detail + status)

    @staticmethod
    def _inbox_key(row: InboxRecord) -> str:
        return 'inbox:' + hashlib.sha256(row.message.message_id.encode()).hexdigest()

    @staticmethod
    def _inbox_status(row: InboxRecord) -> str:
        return ('\nInbox message: ' + json.dumps(row.message.message_id, ensure_ascii=True)
                + '\nInbox status: ' + row.status
                + '\nBranch: ' + json.dumps(row.branch_id, ensure_ascii=True)
                + ('\nAttempt: ' + row.attempt_id if row.attempt_id else '')
                + ('\nRequest: ' + row.request_id if row.request_id else ''))

    def set_inbox(self, rows: tuple[InboxRecord, ...]) -> None:
        self._inbox = visible(rows, tuple(self.records.values()))
        self._inbox_requests = {row.request_id: row for row in self._inbox if row.request_id}
        self._render_inbox()

    def _render_inbox(self) -> None:
        keys = {self._inbox_key(row) for row in self._inbox if row.request_id not in self.records}
        for key in self._inbox_keys - keys:
            self.log.remove_session_input(key)
        self._inbox_keys = keys
        for row in self._inbox:
            existing = self.records.get(row.request_id)
            if existing is not None:
                self._show(existing)
                continue
            item = row.message
            value = SessionInput(item.message_id, 'acp', item.source, item.instruction, item.evidence, item.provenance)
            status = self._inbox_status(row)
            if row.error:
                status += '\nError: ' + _bounded(sanitize_terminal_text(row.error), 1024)
            self.log.update_session_input(self._inbox_key(row),
                'Session inbox (durable intake, not model context)\n' + external_input_text(value) + status,
                'Durable inbox (not a human message; untrusted evidence)\n'
                + json.dumps(record_data(row), ensure_ascii=True, allow_nan=False))

    def apply(self, event: SessionEvent) -> None:
        if event.kind == 'inbox_snapshot' and isinstance(event.data, tuple):
            if all(isinstance(row, InboxRecord) for row in event.data):
                self.set_inbox(event.data)
            return
        previous = self.records.get(event.request_id)
        if previous is not None and (previous.generation != event.generation or previous.status in TERMINAL):
            return
        if isinstance(event.data, InputRecord):
            if event.data.generation != event.generation or event.data.input.id != event.request_id:
                return
            if previous is not None and previous.status == "running":
                return
            record = event.data
        elif previous is not None and event.kind == "input_running":
            record = replace(previous, status="running", batch_id=event.batch_id or previous.batch_id)
        elif previous is not None and event.kind == "input_completed" and isinstance(event.data, TurnResult):
            record = replace(previous, status=event.data.status, error=event.data.error,
                             batch_id=event.data.batch_id or event.batch_id or previous.batch_id)
        else:
            return
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
