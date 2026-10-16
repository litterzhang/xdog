"""Frozen domain definitions and evidence, following flow's model boundary."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from xdog.eyes.connections import ConnectionDef

EventType = Literal["file.created", "file.modified", "file.deleted"]
EVENT_TYPES: tuple[EventType, ...] = ("file.created", "file.modified", "file.deleted")


@dataclass(frozen=True)
class SourceDef:
    id: str
    type: Literal["file"]
    root: Path
    include: tuple[str, ...] = ("**",)
    exclude: tuple[str, ...] = ()
    poll_interval_ms: int = 1000
    max_files: int = 10_000
    max_file_bytes: int = 10 * 1024 * 1024


@dataclass(frozen=True)
class DetectorDef:
    id: str
    type: Literal["file.change"]
    source: str
    debounce_ms: int = 500
    max_batch_delay_ms: int = 2000


@dataclass(frozen=True)
class HandlerDef:
    id: str
    type: str


@dataclass(frozen=True)
class AcpHandlerDef(HandlerDef):
    type: Literal["acp"]
    connection: ConnectionDef
    session: str
    instruction: str
    delivery: Literal["prompt", "enqueue"] = "prompt"


@dataclass(frozen=True)
class RouteDef:
    id: str
    detector: str
    handler: str
    event_types: tuple[EventType, ...]


@dataclass(frozen=True)
class ObserverDef:
    version: Literal[2]
    name: str
    sources: tuple[SourceDef, ...]
    detectors: tuple[DetectorDef, ...]
    handlers: tuple[HandlerDef, ...]
    routes: tuple[RouteDef, ...]


@dataclass(frozen=True)
class FileState:
    digest: str
    size: int


@dataclass(frozen=True)
class FileEvidence:
    path: str
    before: FileState | None
    after: FileState | None
    observed_at: float
    root: str = ""


@dataclass(frozen=True)
class Event:
    id: str
    source_id: str
    detector_id: str
    type: EventType
    observed_at: float
    evidence: tuple[FileEvidence, ...]
