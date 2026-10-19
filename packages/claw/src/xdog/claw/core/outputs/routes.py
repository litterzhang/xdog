"""Trusted immutable reply routing, separate from canonical input and permissions."""
from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from xdog.agent.session_input import JsonValue
from xdog.claw.core.inputs.models import InputEnvelope, ReplyTarget
from xdog.claw.core.outputs.store import OutputRecord
from xdog.claw.core.outputs.targets import ReplyReference
from xdog.claw.core.types import UserInput

Policy = Literal["ready", "pending", "cancelled"]
ResolveReply = Callable[[InputEnvelope], Awaitable[ReplyReference | None]]
ReadPolicy = Callable[[OutputRecord], Awaitable[Policy]]


def source_name(value: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,31}", value) is None:
        raise ValueError("Output source IDs must be 1-32 ASCII letters/digits/underscore")
    return value


@dataclass(frozen=True)
class OutputRoute:
    source: str
    reference: ReplyReference | None

    def __post_init__(self) -> None:
        source_name(self.source)
        if self.reference is not None and not isinstance(self.reference, ReplyReference):
            raise ValueError("Output route requires an actual host reply reference")


def apply_route(envelope: InputEnvelope, route: OutputRoute) -> InputEnvelope:
    if route.reference is None:
        return envelope  # Absence of a destination never means group broadcast.
    reference = route.reference
    if (envelope.context.get("kind") != "external" or envelope.human_authorized
            or envelope.message.origin == "human" or reference.scope != envelope.scope):
        raise ValueError("Automatic output route cannot change input authority or scope")
    original = reference.original
    # Keep the existing bounded context budget: do not duplicate scope or JSON-
    # encode another complete envelope inside it. Metadata here is host-captured
    # original channel routing, never selected by canonical payload/provenance.
    context: dict[str, JsonValue] = {"kind": "external", "channel": reference.channel,
        "sender": reference.recipient, "metadata": original.metadata, "output": [route.source, reference.binding]}
    return InputEnvelope(envelope.scope, envelope.message, ReplyTarget(reference.channel, reference.recipient),
                         envelope.images, human_authorized=False, admission_key=envelope.admission_key, context=context)


def saved_route(envelope: InputEnvelope) -> OutputRoute | None:
    context = envelope.context
    if "output" not in context:
        return None
    value = context["output"]
    metadata = context.get("metadata")
    if (not isinstance(value, list) or len(value) != 2 or not all(isinstance(item, str) for item in value)
            or context.get("kind") != "external" or envelope.human_authorized or envelope.message.origin == "human"
            or not isinstance(metadata, dict) or envelope.reply is None
            or (context.get("channel"), context.get("sender")) != (envelope.reply.channel, envelope.reply.recipient)):
        raise ValueError("Invalid saved host output route")
    source, binding = value
    assert isinstance(source, str) and isinstance(binding, str)
    original = UserInput(group_id=envelope.scope.group_id, channel=envelope.reply.channel,
                         sender=envelope.reply.recipient, metadata=metadata)
    return OutputRoute(source, ReplyReference(envelope.scope, binding, original))
