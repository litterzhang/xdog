"""Presentation-only child summaries and canonical JSON transcript conversion."""
from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from xdog.tui.transcript_browser import TranscriptRecord
from xdog.tui.utils import redact_sensitive_text, sanitize_terminal_text

CHILD_STATES = frozenset({
    "queued", "running", "canceling", "completed", "failed", "canceled", "timed-out", "interrupted"})
_SECRET = re.compile(r"password|passwd|secret|token|authorization|api[_-]?key|credential", re.I)


@dataclass(frozen=True, slots=True)
class ChildEntry:
    id: str
    task: str
    access: str
    status: str
    calls: int
    tokens: int
    revision: int = 0
    settling: bool = False
    error: str = ""
    label: str = ""


def child_entries(value: Mapping[str, Any]) -> tuple[ChildEntry, ...]:
    rows = value.get("children")
    if not isinstance(rows, (list, tuple)) or len(rows) > 16:
        raise ValueError("Invalid child catalog")
    result = []
    ids = set()
    for row in rows:
        if (not isinstance(row, Mapping) or not isinstance(row.get("id"), str)
                or not re.fullmatch(r"[a-f0-9]{12}", row["id"]) or row["id"] in ids
                or not isinstance(row.get("task"), str) or len(row["task"].encode("utf-8")) > 16000
                or not isinstance(row.get("access"), str) or row["access"] not in {"read-only", "inherit"}
                or not isinstance(row.get("status"), str) or row["status"] not in CHILD_STATES
                or type(row.get("settling", False)) is not bool or not isinstance(row.get("error", ""), str)):
            raise ValueError("Invalid child identity/status")
        for key in ("calls", "tokens", "revision"):
            if type(row.get(key, 0)) is not int or row.get(key, 0) < 0:
                raise ValueError("Invalid child counters")
        config = row.get("config", {})
        if not isinstance(config, Mapping) or not isinstance(config.get("label", ""), str):
            raise ValueError("Invalid child configuration metadata")
        label = config.get("label", "")
        if len(label.encode("utf-8")) > 120:
            raise ValueError("Invalid child label")
        ids.add(row["id"])
        result.append(ChildEntry(row["id"], sanitize_terminal_text(row["task"]), row["access"], row["status"],
                                 row.get("calls", 0), row.get("tokens", 0), row.get("revision", 0),
                                 row.get("settling", False), sanitize_terminal_text(row.get("error", "")),
                                 sanitize_terminal_text(label)))
    return tuple(result)


def _safe(value: Any, key: str = "") -> Any:
    if _SECRET.search(key):
        return "<redacted>"
    if isinstance(value, Mapping):
        return {str(k): _safe(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe(v) for v in value]
    if isinstance(value, str):
        return redact_sensitive_text(sanitize_terminal_text(value))
    if value is None or isinstance(value, (bool, int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            return str(value)
        return value
    return str(value)


def message_records(messages: Any, *, partial: Any = None) -> tuple[TranscriptRecord, ...]:
    """Redacted complete typed history; no binary/signature bodies or execution."""
    if not isinstance(messages, (list, tuple)) or len(messages) > 20000:
        raise ValueError("Saved transcript exceeds the message limit")
    records: list[TranscriptRecord] = []
    calls: dict[str, int] = {}
    def append_message(message: Any, index: str) -> None:
        if not isinstance(message, Mapping):
            raise ValueError("Invalid child message")
        role = message.get("role")
        if role == 'custom' and message.get('type') == 'xdog.extension_context.v1':
            data = message.get('data')
            if not isinstance(data, Mapping) or data.get('display') is not True:
                return
            body = data.get('content', [])
            if not isinstance(body, list):
                raise ValueError('Invalid custom context source')
            text = '\n'.join(part['text'] for part in body if isinstance(part, Mapping)
                             and part.get('type') == 'text' and isinstance(part.get('text'), str))
            images = sum(isinstance(part, Mapping) and part.get('type') == 'image' for part in body)
            if images:
                text += f'\n[Images: {images}; binary data omitted]'
            records.append(TranscriptRecord(index + ':custom', 'custom',
                str(_safe(f'Extension context · {data.get("extension", "")} / {data.get("custom_type", "")}')),
                str(_safe(text))))
            return
        if role not in {"user", "assistant", "toolResult"}:
            return  # Custom metadata is not falsely presented as model prose.
        content = message.get("content", [])
        parts = content if isinstance(content, (list, tuple)) else ()
        text = content if isinstance(content, str) else "\n".join(
            str(part.get("text", "")) for part in parts if isinstance(part, Mapping) and part.get("type") == "text")
        images = sum(isinstance(part, Mapping) and part.get("type") == "image" for part in parts)
        if images:
            text += f"\n[Images: {images}; binary data omitted]"
        if role == "toolResult":
            position = calls.get(str(message.get("tool_call_id", "")))
            text = ("[Tool error]\n" if message.get("is_error") else "") + str(_safe(text))
            if position is not None:
                original = records[position]
                records[position] = TranscriptRecord(original.id, original.role, original.title, original.body + text)
            else:
                records.append(TranscriptRecord(index + ":result", "tool", str(_safe(message.get("tool_name", "tool"))),
                                                text))
            return
        if text:
            records.append(TranscriptRecord(index + ":text", role, "input" if role == "user" else "response",
                                            str(_safe(text))))
        for number, part in enumerate(parts):
            if not isinstance(part, Mapping):
                continue
            if part.get("type") == "thinking" and not part.get("redacted"):
                records.append(TranscriptRecord(f"{index}:p{number}", "reasoning", "reasoning",
                                                str(_safe(part.get("thinking", "")))))
            elif part.get("type") == "toolCall":
                calls[str(part.get("id", ""))] = len(records)
                records.append(TranscriptRecord(f"{index}:p{number}", "tool", str(_safe(part.get("name", "tool"))),
                    "Arguments:\n" + json.dumps(_safe(part.get("arguments", {})), ensure_ascii=False)
                    + "\n\nOutput:\n"))
    for number, message in enumerate(messages):
        append_message(message, f"m{number}")
    if partial is not None:
        append_message(partial, "partial")
    return tuple(records)


def child_records(value: Mapping[str, Any]) -> tuple[TranscriptRecord, ...]:
    """No binary bodies, signatures, truncation or rendering on the UI thread."""
    entry = child_entries({"children": [value]})[0]
    messages = value.get("messages", [])
    if not isinstance(messages, (list, tuple)) or len(messages) > 2000:
        raise ValueError("Invalid child transcript")
    records = [TranscriptRecord("child-status", "system", f"Child {entry.id}",
        f"[{entry.status}] {entry.access} · {entry.calls} calls · {entry.tokens} reported tokens\n"
        + str(_safe(entry.task)) + ("\nError: " + str(_safe(entry.error)) if entry.error else ""))]
    feedback = value.get("input_feedback", [])
    if (not isinstance(feedback, (list, tuple)) or len(feedback) > 64
            or any(not isinstance(item, str) for item in feedback)
            or sum(len(item.encode()) for item in feedback) > 65536):
        raise ValueError("Invalid child input-handler feedback")
    records.extend(TranscriptRecord(f"child-input-feedback:{index}", "system", "Extension input feedback",
                                   str(_safe(item))) for index, item in enumerate(feedback))
    records.extend(message_records(messages, partial=value.get("partial")))
    if isinstance(value.get("effective"), Mapping):
        records.append(TranscriptRecord("child-model", "system", "Effective child model/reasoning",
                                        json.dumps(_safe(value["effective"]), ensure_ascii=False, indent=2)))
    if isinstance(value.get("config"), Mapping):
        records.append(TranscriptRecord("child-config", "system", "Child configuration (not tool authority)",
                                        json.dumps(_safe(value["config"]), ensure_ascii=False, indent=2)))
    if type(value.get("live_tools_limited", False)) is not bool:
        raise ValueError("Invalid live progress capacity marker")
    if value.get("live_tools_limited"):
        records.append(TranscriptRecord("child-progress-limit", "system", "Live progress capacity",
            "[Some live tool progress omitted by retention/capacity limits; "
            "canonical final results remain authoritative]"))
    progress = value.get("live_tools", ())
    if not isinstance(progress, (list, tuple)) or len(progress) > 128:
        raise ValueError("Invalid live child progress")
    by_id = {record.id: index for index, record in enumerate(records)}
    for row in progress:
        if (not isinstance(row, Mapping) or not isinstance(row.get("id"), str)
                or not isinstance(row.get("name"), str) or not isinstance(row.get("text"), str)
                or type(row.get("limited", False)) is not bool):
            raise ValueError("Invalid live child progress entry")
        if row["id"] not in by_id:
            raise ValueError("Live progress has no canonical child tool identity")
        index = by_id[row["id"]]
        original = records[index]
        # A canonical complete result wins over delayed/out-of-order progress.
        if original.body.endswith("\n\nOutput:\n"):
            body = original.body + "[Live tool progress; final result pending]\n" + str(_safe(row["text"]))
            if row.get("limited"):
                body += "\n[Live progress clipped by retention budget; final result will be checked separately]"
            records[index] = TranscriptRecord(original.id, original.role, original.title, body)
    return tuple(records)


class ChildSourceWorker:
    """One coalescing conversion owner; stale/closed requests never publish into a new view."""
    def __init__(self, publish: Callable[[dict[str, Any]], None]) -> None:
        import threading
        self.publish = publish
        self._condition = threading.Condition()
        self._pending: tuple[str, str, str, Callable[[], Mapping[str, Any]]] | None = None
        self._closed = False
        self._serial = 0
        self._thread: threading.Thread | None = None

    def request(self, identifier: str, child: str, context: str, load: Callable[[], Mapping[str, Any]]) -> None:
        import threading
        with self._condition:
            if self._closed:
                return
            self._serial += 1
            self._pending = identifier, child, context, load
            if self._thread is None:
                self._thread = threading.Thread(target=self._work, daemon=True, name="xdog-child-source")
                self._thread.start()
            self._condition.notify()

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._pending = None
            self._serial += 1
            self._condition.notify_all()

    def _work(self) -> None:
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._closed or self._pending is not None)
                if self._closed:
                    return
                assert self._pending is not None
                identifier, child, context, load = self._pending
                serial = self._serial
                self._pending = None
            event: dict[str, Any] = {"type": "child_source_ready", "id": identifier,
                                     "child_id": child, "context": context}
            try:
                event["records"] = child_records(load())
            except Exception as exc:
                event["error"] = str(exc)
            with self._condition:
                if not self._closed and serial == self._serial:
                    self.publish(event)
