"""Pure explicit route matching; handler execution belongs to the runtime."""
from dataclasses import dataclass

from xdog.eyes.models import Event, ObserverDef


@dataclass(frozen=True)
class RoutedEvents:
    route_id: str
    handler_id: str
    events: tuple[Event, ...]


class EventRouter:
    def __init__(self, definition: ObserverDef) -> None:
        self.routes = definition.routes

    def route(self, events: tuple[Event, ...]) -> tuple[RoutedEvents, ...]:
        matches = []
        for route in self.routes:
            selected = tuple(event for event in events
                             if event.detector_id == route.detector and event.type in route.event_types)
            if selected:
                matches.append(RoutedEvents(route.id, route.handler, selected))
        return tuple(matches)
