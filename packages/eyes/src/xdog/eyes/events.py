"""Source-neutral runtime telemetry; never contains authentication capabilities."""
from dataclasses import dataclass


@dataclass(frozen=True)
class RuntimeEvent:
    kind: str
    registration_id: str
    source_id: str = ""
    route_id: str = ""
    delivery_id: str = ""
    error: str = ""
    event_ids: tuple[str, ...] = ()
