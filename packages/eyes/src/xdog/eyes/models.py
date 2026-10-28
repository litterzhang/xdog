"""Frozen domain definitions and evidence, following flow's model boundary."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypeAlias

from xdog.eyes.connections import ConnectionDef

EventType = Literal["file.created", "file.modified", "file.deleted", "scheduler.due",
                    "record.created", "record.updated", "record.removed", "condition.met", "condition.cleared",
                    "availability.unavailable", "availability.recovered"]
FILE_EVENT_TYPES: tuple[EventType, ...] = ("file.created", "file.modified", "file.deleted")
RECORD_EVENT_TYPES: tuple[EventType, ...] = ("record.created", "record.updated", "record.removed")
CONDITION_EVENT_TYPES: tuple[EventType, ...] = ("condition.met", "condition.cleared")
AVAILABILITY_EVENT_TYPES: tuple[EventType, ...] = ("availability.unavailable", "availability.recovered")
EVENT_TYPES: tuple[EventType, ...] = (*FILE_EVENT_TYPES, "scheduler.due", *RECORD_EVENT_TYPES,
                                    *CONDITION_EVENT_TYPES, *AVAILABILITY_EVENT_TYPES)


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
class AtSchedule:
    at: str


@dataclass(frozen=True)
class IntervalSchedule:
    every_seconds: float


@dataclass(frozen=True)
class CronSchedule:
    cron: str
    timezone: str


Schedule: TypeAlias = AtSchedule | IntervalSchedule | CronSchedule


@dataclass(frozen=True)
class SchedulerSourceDef:
    id: str
    type: Literal["scheduler"]
    schedule: Schedule


@dataclass(frozen=True)
class SchedulerDetectorDef:
    id: str
    type: Literal["scheduler.due"]
    source: str


@dataclass(frozen=True, kw_only=True)
class HttpSourceDef:
    id: str
    type: Literal["http"] = "http"
    url: str
    mode: Literal["status", "json"] = "json"
    pointer: str = ""
    key_pointer: str | None = None
    bearer_env: str | None = None
    poll_interval_seconds: float = 60.0
    timeout_seconds: float = 10.0
    max_bytes: int = 1024 * 1024
    max_records: int = 256


@dataclass(frozen=True, kw_only=True)
class GithubSourceDef:
    id: str
    type: Literal["github"] = "github"
    repository: str
    resource: Literal["issues", "issue_comments", "workflow_runs"]
    api_url: str = "https://api.github.com"
    bearer_env: str | None = None
    poll_interval_seconds: float = 60.0
    timeout_seconds: float = 10.0
    max_bytes: int = 1024 * 1024
    max_records: int = 256
    max_pages: int = 10
    lookback_seconds: float = 86400.0
    overlap_seconds: float = 120.0


RemoteSourceDef: TypeAlias = HttpSourceDef | GithubSourceDef


@dataclass(frozen=True, kw_only=True)
class RecordDetectorDef:
    id: str
    type: Literal["record.change"] = "record.change"
    source: str
    removals: bool = False


@dataclass(frozen=True)
class Predicate:
    pointer: str
    op: Literal["eq", "ne", "gt", "ge", "lt", "le"]
    value: str | int | float | bool | None


@dataclass(frozen=True, kw_only=True)
class ConditionDetectorDef:
    id: str
    type: Literal["value.condition"] = "value.condition"
    source: str
    all: tuple[Predicate, ...]


@dataclass(frozen=True, kw_only=True)
class AvailabilityDetectorDef:
    id: str
    type: Literal["availability"] = "availability"
    source: str
    failures: int = 3
    for_seconds: float = 0.0
    initial_abnormal: bool = True


RemoteDetectorDef: TypeAlias = RecordDetectorDef | ConditionDetectorDef | AvailabilityDetectorDef
SourceDefinition: TypeAlias = SourceDef | SchedulerSourceDef | RemoteSourceDef
DetectorDefinition: TypeAlias = DetectorDef | SchedulerDetectorDef | RemoteDetectorDef


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
    # Target workspace assertion only; never the stdio process startup directory.
    cwd: Path | None = None


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
    sources: tuple[SourceDefinition, ...]
    detectors: tuple[DetectorDefinition, ...]
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
class SchedulerEvidence:
    schedule_id: str
    scheduled_at: float
    observed_at: float
    occurrence_id: str
    timezone: str


@dataclass(frozen=True)
class RecordEvidence:
    key: str
    url: str
    before: str | None
    after: str | None
    observed_at: float


@dataclass(frozen=True)
class AvailabilityEvidence:
    healthy: bool
    failures: int
    status_code: int | None
    reason: str
    observed_at: float


Evidence: TypeAlias = FileEvidence | SchedulerEvidence | RecordEvidence | AvailabilityEvidence


@dataclass(frozen=True)
class Event:
    id: str
    source_id: str
    detector_id: str
    type: EventType
    observed_at: float
    evidence: tuple[Evidence, ...]
