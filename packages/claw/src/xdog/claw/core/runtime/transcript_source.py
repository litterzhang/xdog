"""Untruncated, redacted source snapshots. No provider calls or binary bodies."""
from __future__ import annotations

import json
import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from xdog.agent import AgentMessage
from xdog.agent.messages import messages_to_dicts
from xdog.claw.core.runtime.display_events import display_arguments, display_result
from xdog.tui.source_spool import SourceSpool, spool_records
from xdog.tui.transcript_browser import TranscriptRecord


@dataclass(frozen=True, slots=True)
class SourceTool:
    id: str
    name: str
    arguments: dict[str, Any]
    output: str = ""
    progress: str = ""


@dataclass(frozen=True, slots=True)
class SourceCapture:
    session_id: str
    load: Callable[[], list[dict[str, Any]]]
    revision: int = 0
    messages: tuple[AgentMessage, ...] | None = None
    channels: tuple[tuple[int, str], ...] = ()
    partial: AgentMessage | None = None
    tools: tuple[SourceTool, ...] = ()
    records_load: Callable[[threading.Event], tuple[TranscriptRecord, ...]] | None = None


def source_records(capture: SourceCapture, canceled: threading.Event) -> tuple[TranscriptRecord, ...]:
    if capture.records_load is not None:
        return capture.records_load(canceled)
    if capture.messages is None:
        entries = capture.load()
    else:
        entries = messages_to_dicts(list(capture.messages))
        for index, channel in capture.channels:
            if index < len(entries):
                entries[index] = {**entries[index], "channel": channel}
    if capture.partial is not None:
        entries.extend(messages_to_dicts([capture.partial]))
    records: list[TranscriptRecord] = []
    calls: dict[str, int] = {}
    for index, entry in enumerate(entries):
        if canceled.is_set():
            raise ValueError("Source request canceled")
        role, content = entry.get("role"), entry.get("content")
        if role == "custom" and entry.get("type") == "xdog.session_input.v1":
            from xdog.claw.core.persistence.transcript_convert import entry_text
            records.append(TranscriptRecord(f"m{index}:external", "user", "external input",
                                            display_result(entry_text(entry), bounded=False)))
            continue
        if entry.get("channel") in {"goal_runner", "scheduler"}:
            continue
        parts = content if isinstance(content, list) else []
        text = content if isinstance(content, str) else "".join(
            str(part.get("text", "")) for part in parts if isinstance(part, dict) and part.get("type") == "text"
        )
        if role == "user" and text.startswith(("Continue working on your active goals:", "[Goal:", "Goal completed:")):
            continue
        if role in {"user", "assistant"}:
            images = sum(isinstance(part, dict) and part.get("type") == "image" for part in parts)
            if images:
                text += f"\n[Images: {images}]"
            pending_text = text
            if role == "user" or not parts:
                if pending_text:
                    records.append(TranscriptRecord(f"m{index}:text", role,
                                                    "input" if role == "user" else "response",
                                                    display_result(pending_text, bounded=False)))
                pending_text = ""
            for number, part in enumerate(parts):
                if not isinstance(part, dict):
                    continue
                if pending_text and part.get("type") != "thinking":
                    records.append(TranscriptRecord(f"m{index}:text", role, "response",
                                                    display_result(pending_text, bounded=False)))
                    pending_text = ""
                if part.get("type") == "thinking" and not part.get("redacted"):
                    body = display_result(str(part.get("thinking", "")), bounded=False)
                    if body:
                        records.append(TranscriptRecord(f"m{index}:p{number}", "reasoning", "reasoning", body))
                elif part.get("type") == "toolCall":
                    args = part.get("arguments", {})
                    args = display_arguments(args if isinstance(args, dict) else {}, bounded=False)
                    body = "Arguments:\n" + json.dumps(args, ensure_ascii=False) + "\n\nOutput:\n"
                    calls[str(part.get("id", ""))] = len(records)
                    records.append(TranscriptRecord(f"m{index}:p{number}", "tool", str(part.get("name", "tool")), body))
        elif role == "toolResult":
            images = sum(isinstance(part, dict) and part.get("type") == "image" for part in parts)
            if images:
                text += f"\n[Images: {images}]"
            call = calls.get(str(entry.get("tool_call_id", "")))
            body = display_result(text, bounded=False)
            if call is not None:
                records[call] = replace(records[call], body=records[call].body + body)
            else:
                records.append(TranscriptRecord(f"m{index}:result", "tool", str(entry.get("tool_name", "tool")), body))
    # Live progress is not necessarily in Agent.messages yet. Stable source IDs
    # associate it with its actual call, not the last reused provider tool ID.
    by_id = {record.id: index for index, record in enumerate(records)}
    for tool in capture.tools:
        if canceled.is_set():
            raise ValueError("Source request canceled")
        if tool.id in by_id:
            index = by_id[tool.id]
            # A complete canonical result takes precedence over older progress.
            if not records[index].body.endswith("\n\nOutput:\n"):
                if tool.progress and tool.progress not in records[index].body:
                    records[index] = replace(records[index], body=records[index].body
                                             + "\n\nLast reported progress:\n"
                                             + display_result(tool.progress, bounded=False))
                continue
        else:
            index = len(records)
            by_id[tool.id] = index
            records.append(TranscriptRecord(tool.id, "tool", tool.name, ""))
        args = display_arguments(tool.arguments, bounded=False)
        records[index] = replace(records[index], body="Arguments:\n" + json.dumps(args, ensure_ascii=False)
                                 + "\n\nOutput:\n" + display_result(tool.output, bounded=False))
    return tuple(records)


__all__ = ['SourceCapture', 'SourceSpool', 'SourceTool', 'prepare_source', 'source_records']


def prepare_source(capture: SourceCapture, canceled: threading.Event) -> SourceSpool:
    return spool_records(source_records(capture, canceled), canceled)
