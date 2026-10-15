"""Register trusted processing instructions separately from evidence delivery."""
from __future__ import annotations

import json
from dataclasses import asdict

from xdog.eyes.executor import Delivery
from xdog.eyes.models import ObserverDef
from xdog.eyes.transport.client import HostClient
from xdog.eyes.transport.protocol import JsonValue, ProtocolError, json_value, text


class SessionNotifier:
    def __init__(self, client: HostClient, definition: ObserverDef, registration_id: str) -> None:
        self.client = client
        self.definition = definition
        self.registration_id = registration_id

    async def register(self) -> None:
        handlers = {item.id: item for item in self.definition.handlers}
        try:
            for route in self.definition.routes:
                response = await self.client.request({"action": "register", "body": {
                    "registration_id": self.registration_id, "route_id": route.id,
                    "instruction": handlers[route.handler].instruction,
                }})
                if response["status"] not in {"accepted", "duplicate"}:
                    raise ProtocolError("Host rejected notification registration")
        except BaseException:
            # Include an attempted route whose accepted ACK could have been lost.
            await self.unregister()
            raise

    async def unregister(self) -> None:
        await self.client.request({"action": "unregister", "body": {"registration_id": self.registration_id}})

    async def deliver(self, delivery: Delivery) -> str:
        if (delivery.registration_id != self.registration_id
                or delivery.route_id not in {route.id for route in self.definition.routes}):
            raise ProtocolError("Delivery does not belong to this registration")
        events: list[JsonValue] = [
            json_value(json.loads(json.dumps(asdict(event), allow_nan=False))) for event in delivery.events
        ]
        response = await self.client.request({"action": "deliver", "body": {
            "registration_id": self.registration_id, "route_id": delivery.route_id,
            "delivery_id": delivery.id, "events": events,
        }})
        return text(response["status"])
