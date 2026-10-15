"""Serialize an admitted job for the normal session input boundary."""
import json
from dataclasses import asdict

from xdog.agent.external_event import message
from xdog.coding.core.external_events.inbox import ExternalEventJob


def serialize(job: ExternalEventJob) -> str:
    result = json.dumps(asdict(job), ensure_ascii=True, allow_nan=False)
    message(result)
    return result
