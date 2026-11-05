"""Source-neutral runtime telemetry; never contains authentication capabilities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class RuntimeEvent:
    kind: str
    registration_id: str
    source_id: str = ""
    route_id: str = ""
    delivery_id: str = ""
    error: str = ""
    event_ids: tuple[str, ...] = ()
    receipt_status: str = ""
    message_state: str = ""
    retry_on_resume: bool = False


@dataclass(frozen=True)
class DeliveryResult:
    """Retry preserves the original identity; it makes no claim that a send did not happen."""

    status: Literal["queued", "succeeded", "failed", "cancelled", "not-sent", "outcome-unknown", "paused", "retry"]
    error: str = ""
    receipt_status: str = ""
    message_state: str = ""
    retry_on_resume: bool = False
