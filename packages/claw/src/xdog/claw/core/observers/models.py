"""Frozen observer data and bounded JSON; no protocol or storage owner."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from xdog.claw.core.outputs.targets import ReplyReference


class ObserverStopped(RuntimeError):
    """A persisted cancellation/revision fence won before a new trigger."""


def encoded(value: object) -> str:
    data = json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False)
    if len(data.encode()) > 1024 * 1024:
        raise ValueError('Managed observer record exceeds one MiB; no data was dropped')
    return data


@dataclass(frozen=True)
class ObserverRecord:
    id: str
    group_id: str
    cwd: Path
    name: str
    definition: dict[str, Any]
    revision: int
    enabled: bool
    recovery: str
    state: dict[str, Any]
    error: str
    inbox_id: str = ""
    owner: str = ""
    notify_only: bool = False
    reports_enabled: bool = True
    reply: ReplyReference | None = None


