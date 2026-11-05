"""Handler-neutral lifecycle and health; no ACP or harness dependencies."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Protocol

from xdog.eyes.deliveries import Delivery
from xdog.eyes.events import DeliveryResult
from xdog.eyes.local_control import JsonValue


@dataclass(frozen=True)
class HandlerSnapshot:
    state: str
    details: dict[str, JsonValue]
    binding: dict[str, JsonValue] = field(kw_only=True)
    retry_safe: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, 'details', json.loads(json.dumps(self.details, allow_nan=False)))
        object.__setattr__(self, 'binding', json.loads(json.dumps(self.binding, allow_nan=False)))
        if type(self.retry_safe) is not bool:
            raise ValueError('Handler retry safety must be explicit')


class HandlerHealthError(ConnectionError):
    def __init__(self, message: str, *, recoverable: bool = False) -> None:
        super().__init__(message)
        self.recoverable = recoverable


class EventHandler(Protocol):
    async def start(self) -> None: ...
    def validate(self, delivery: Delivery) -> None: ...
    async def deliver(self, delivery: Delivery) -> DeliveryResult: ...
    def snapshot(self) -> HandlerSnapshot: ...
    async def check(self) -> None: ...
    async def resume(self) -> bool: ...
    async def close(self) -> None: ...
