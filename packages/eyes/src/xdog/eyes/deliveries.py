"""Immutable routed event identity, independent of any handler protocol."""
from dataclasses import dataclass

from xdog.eyes.models import Event


@dataclass(frozen=True)
class Delivery:
    id: str
    registration_id: str
    route_id: str
    events: tuple[Event, ...]
