"""Bounded one-off/interval/calendar observations, with no harness or task execution."""
from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from xdog.eyes.local_control import JsonValue, object_value
from xdog.eyes.models import (
    AtSchedule,
    CronSchedule,
    IntervalSchedule,
    ObserverDef,
    SchedulerEvidence,
    SchedulerSourceDef,
)

_CRON_NAMES = frozenset("jan feb mar apr may jun jul aug sep oct nov dec mon tue wed thu fri sat sun".split())


def at_timestamp(value: str) -> float:
    stamp = datetime.fromisoformat(value)
    if 'T' not in value or stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("at must be an ISO-8601 datetime with an explicit timezone")
    return stamp.timestamp()


def cron_time(schedule: CronSchedule, after: float, *, previous: bool = False) -> float:
    # Five fields only; exclude croniter's random/hash/year/seconds extensions.
    if (len(schedule.cron.split()) != 5 or not re.fullmatch(r'[\w*/ ,\-]+', schedule.cron, flags=re.ASCII)
            or not set(re.findall('[a-zA-Z]+', schedule.cron.lower())) <= _CRON_NAMES):
        raise ValueError("cron must be a standard five-field expression")
    from croniter import croniter
    zone = ZoneInfo(schedule.timezone)
    start = datetime.fromtimestamp(after, zone)
    # Compare against the original epoch, not rounded datetime microseconds:
    # otherwise a boundary poll can select a future tick or skip the next one.
    minute = start.replace(second=0, microsecond=0)
    candidate = minute.timestamp()
    if (candidate <= after if previous else candidate > after) and croniter.match(schedule.cron, minute):
        return candidate
    iterator = croniter(schedule.cron, start, max_years_between_matches=8)
    value = float(iterator.get_prev(float) if previous else iterator.get_next(float))
    if not math.isfinite(value) or (value > after if previous else value <= after):
        raise ValueError("cron did not produce an ordered finite occurrence")
    return value


def validate_cron(schedule: CronSchedule) -> None:
    cron_time(schedule, datetime(2024, 1, 1, tzinfo=UTC).timestamp())


@dataclass(frozen=True)
class SchedulerSnapshot:
    occurrences: tuple[SchedulerEvidence, ...] = ()
    completed: bool = False


class SchedulerSource:
    """At most one latest occurrence per poll; suspended deadlines are skipped.

    Intervals use monotonic time; cron/at use UTC instants. A wall-clock rollback
    never rearms an already observed calendar occurrence. State lasts only for
    this explicitly started registration unless a host explicitly persists a checkpoint.
    """

    def __init__(self, definition: SchedulerSourceDef, *, wall_now: float, monotonic_now: float) -> None:
        self.definition = definition
        self.completed = False
        self._next_mono = math.inf
        self._next_wall = math.inf
        schedule = definition.schedule
        if isinstance(schedule, AtSchedule):
            self._next_wall = at_timestamp(schedule.at)
            if self._next_wall <= wall_now:
                raise ValueError("One-off at must be in the future when starting a registration")
        elif isinstance(schedule, IntervalSchedule):
            self._next_mono = monotonic_now + schedule.every_seconds
        else:
            self._next_wall = cron_time(schedule, wall_now)

    def export_checkpoint(self, *, wall_now: float, monotonic_now: float) -> dict[str, JsonValue]:
        """Data only; interval UTC is a restart bridge, never the online clock."""
        next_utc = (wall_now + (self._next_mono - monotonic_now)
                    if isinstance(self.definition.schedule, IntervalSchedule) else self._next_wall)
        return object_value({'version': 1, 'binding': self._binding(self.definition),
                             'next_utc': next_utc, 'completed': self.completed})

    @staticmethod
    def _binding(definition: SchedulerSourceDef) -> str:
        return hashlib.sha256(json.dumps(asdict(definition), sort_keys=True, allow_nan=False).encode()).hexdigest()

    @classmethod
    def restore(cls, definition: SchedulerSourceDef, checkpoint: object, *, wall_now: float,
                monotonic_now: float) -> SchedulerSource:
        """Restore a trusted, definition-bound host checkpoint, not a fresh admission.

        Past accepted one-offs remain eligible exactly once. Overdue intervals
        coalesce on their first poll; cron retains its UTC next-occurrence cursor.
        No handler, code, credential or monotonic epoch is loaded from the data.
        """
        keys = {'version', 'binding', 'next_utc', 'completed'}
        data = object_value(checkpoint, allowed=keys, required=keys)
        if type(data['version']) is not int or data['version'] != 1 or data['binding'] != cls._binding(definition):
            raise ValueError('Scheduler checkpoint version or definition binding mismatch')
        deadline, completed = data['next_utc'], data['completed']
        if isinstance(deadline, bool) or not isinstance(deadline, (int, float)) or type(completed) is not bool:
            raise ValueError('Invalid scheduler checkpoint cursor')
        schedule = definition.schedule
        if isinstance(schedule, AtSchedule):
            if deadline != at_timestamp(schedule.at):
                raise ValueError('One-off checkpoint deadline mismatch')
        elif completed:
            raise ValueError('Repeating scheduler cannot be completed')
        if isinstance(schedule, CronSchedule):
            if cron_time(schedule, float(deadline), previous=True) != deadline:
                raise ValueError('Cron checkpoint cursor is not an occurrence')
        source = cls.__new__(cls)
        source.definition, source.completed = definition, completed
        source._next_wall = float(deadline)
        source._next_mono = (monotonic_now + (float(deadline) - wall_now)
                             if isinstance(schedule, IntervalSchedule) else math.inf)
        return source

    def poll(self, *, wall_now: float, monotonic_now: float, suspended: bool = False) -> SchedulerSnapshot:
        if self.completed:
            return SchedulerSnapshot(completed=True)
        schedule = self.definition.schedule
        zone = 'UTC'
        if isinstance(schedule, IntervalSchedule):
            if monotonic_now < self._next_mono:
                return SchedulerSnapshot()
            steps = math.floor((monotonic_now - self._next_mono) / schedule.every_seconds)
            due_mono = self._next_mono + steps * schedule.every_seconds
            due = wall_now - (monotonic_now - due_mono)
            self._next_mono = due_mono + schedule.every_seconds
        else:
            if wall_now < self._next_wall:
                return SchedulerSnapshot()
            if isinstance(schedule, AtSchedule):
                due = self._next_wall
                self.completed = True
            else:
                zone = schedule.timezone
                due = cron_time(schedule, wall_now, previous=True)
                self._next_wall = cron_time(schedule, wall_now)
        if suspended:
            return SchedulerSnapshot(completed=self.completed)
        evidence = SchedulerEvidence(self.definition.id, due, wall_now, uuid.uuid4().hex, zone)
        return SchedulerSnapshot((evidence,), self.completed)


def prepare_schedulers(definition: ObserverDef, *, wall_now: float,
                       monotonic_now: float) -> dict[str, SchedulerSource]:
    """Validate all initial deadlines before allocating any handler connections."""
    return {source.id: SchedulerSource(source, wall_now=wall_now, monotonic_now=monotonic_now)
            for source in definition.sources if isinstance(source, SchedulerSourceDef)}
