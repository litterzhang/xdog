"""One pure display worker; business state remains behind a readonly host view."""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from xdog.agent.extensions.api import ExtensionAPI

_STATES = {"active", "waiting", "paused", "completed", "cleared"}


def _plain(value: str) -> str:
    return "".join(char if ord(char) >= 32 and not 127 <= ord(char) <= 159 else " " for char in value)


def _display(value: dict[str, Any]) -> tuple[str, str]:
    if (value.get("format") != "xdog.goal_view.v2" or type(value.get("items")) is not list
            or type(value.get("total")) is not int or value["total"] < len(value["items"])
            or not isinstance(value.get("revision"), str) or re.fullmatch("[a-f0-9]{64}", value["revision"]) is None):
        raise ValueError("Goal view schema unavailable")
    total, rows = value["total"], value["items"]
    if total == 0:
        return "No Goals", "No Goals in this owner"
    summary = f"Goals: {total} • showing {len(rows)}"
    lines = [summary]
    for row in rows:
        if (not isinstance(row, dict) or not isinstance(row.get("id"), str)
                or not isinstance(row.get("title"), str) or row.get("status") not in _STATES
                or not isinstance(row.get("tasks"), dict)):
            raise ValueError("Goal row unavailable")
        counts = row["tasks"]
        if any(type(counts.get(key)) is not int or counts[key] < 0 for key in ("completed", "total")):
            raise ValueError("Goal task counts unavailable")
        marker = " [preview]" if row.get("preview_fields") else ""
        lines.append(f"{_plain(row['id'])} [{row['status']}] {_plain(row['title'])}{marker} "
                     f"• completed tasks {counts['completed']}/{counts['total']}")
    next_offset = value.get("next_offset")
    if next_offset is not None:
        if type(next_offset) is not int or next_offset < 0:
            raise ValueError("Goal page cursor unavailable")
        lines.append(f"Next page: /goals page {next_offset}")
    return summary, "\n".join(lines)


def setup(api: ExtensionAPI) -> None:
    async def refresh(query: dict[str, Any]) -> str:
        try:
            value = await api.read_view("goals", query=query)
            if value is None:
                raise ValueError("Goal view not granted")
            summary, output = _display(value)
        except (ValueError, TypeError, KeyError):
            summary = output = "Goal view unavailable"
        api.status("goals", summary)
        return output

    async def command(args: str, context: dict[str, Any]) -> str:
        parts = args.split()
        if not parts:
            return await refresh({})
        if len(parts) == 2 and parts[0] == "page" and re.fullmatch("[0-9]{1,16}", parts[1]):
            return await refresh({"offset": int(parts[1])})
        if len(parts) == 1 and re.fullmatch("[A-Za-z0-9][A-Za-z0-9_-]{0,127}", parts[0]):
            return await refresh({"goal_id": parts[0]})
        return "Usage: /goals [GOAL_ID | page OFFSET]"

    async def start(context: dict[str, Any]) -> None:
        await refresh({})

    async def changed(context: dict[str, Any]) -> None:
        if context.get("view") == "goals":
            await refresh({})  # A hint is not the snapshot, status or permission.

    api.command("goals", "Read current owner Goals; no task or permission mutations", command, public_name="goals")
    api.on("session_start", start)
    api.on("view_changed", changed)
