"""Bounded one-off/interval/calendar observations, with no harness or task execution."""
from __future__ import annotations

import math
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

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
    this explicitly started registration, not across process restart.
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
