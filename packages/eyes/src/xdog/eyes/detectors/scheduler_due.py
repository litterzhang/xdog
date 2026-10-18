"""Project scheduler observations into ordinary routed events, without executing work."""
import uuid

from xdog.eyes.models import Event, SchedulerDetectorDef, SchedulerEvidence
from xdog.eyes.sources.scheduler import SchedulerSnapshot


class SchedulerDueDetector:
    def __init__(self, definition: SchedulerDetectorDef) -> None:
        self.definition = definition
        self._pending: tuple[SchedulerEvidence, ...] = ()

    def observe(self, snapshot: SchedulerSnapshot) -> None:
        self._pending += snapshot.occurrences

    def flush(self, *, now: float) -> tuple[Event, ...]:
        pending, self._pending = self._pending, ()
        return tuple(Event(uuid.uuid4().hex, self.definition.source, self.definition.id, 'scheduler.due',
                           evidence.observed_at, (evidence,)) for evidence in pending)
