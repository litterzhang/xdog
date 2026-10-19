"""Frozen host routing data, never input content, control identity or permission."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from xdog.claw.core.inputs.models import InputScope
from xdog.claw.core.types import UserInput

_FORMAT = "xdog.claw.reply.v1"
_LIMIT = 64 * 1024


def _label(value: object, maximum: int = 256) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value or len(value.encode()) > maximum:
        raise ValueError("Invalid reply routing identity")
    return value


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("Duplicate reply reference field")
        result[name] = value
    return result


@dataclass(frozen=True, init=False)
class ReplyReference:
    scope: InputScope
    binding: str
    channel: str
    recipient: str
    _metadata_json: str = field(repr=False)

    def __init__(self, scope: InputScope, binding: str, original: UserInput) -> None:
        if (
            not isinstance(scope, InputScope)
            or not isinstance(original, UserInput)
            or original.group_id != scope.group_id
        ):
            raise ValueError("Reply reference requires the original matching host scope")
        if not isinstance(original.metadata, dict):
            raise ValueError("Reply context must be a routing object")
        try:
            metadata = json.dumps(original.metadata, sort_keys=True, ensure_ascii=True, allow_nan=False)
        except (ValueError, TypeError, RecursionError) as exc:
            raise ValueError("Reply context must be finite bounded JSON") from exc
        object.__setattr__(self, "scope", scope)
        object.__setattr__(self, "binding", _label(binding))
        object.__setattr__(self, "channel", _label(original.channel))
        object.__setattr__(self, "recipient", _label(original.sender, 1024))
        object.__setattr__(self, "_metadata_json", metadata)
        encode_reply(self)  # Bound the complete serialized reference before publishing it.

    @property
    def original(self) -> UserInput:
        return UserInput(
            group_id=self.scope.group_id,
            sender=self.recipient,
            channel=self.channel,
            metadata=json.loads(self._metadata_json),
        )


def encode_reply(reference: ReplyReference) -> str:
    if not isinstance(reference, ReplyReference):
        raise ValueError("Expected a host-created typed reply reference")
    result = json.dumps(
        {
            "format": _FORMAT,
            "group_id": reference.scope.group_id,
            "workspace": str(reference.scope.workspace),
            "binding": reference.binding,
            "channel": reference.channel,
            "recipient": reference.recipient,
            "metadata": json.loads(reference._metadata_json),
        },
        sort_keys=True,
        ensure_ascii=True,
        allow_nan=False,
    )
    if len(result.encode()) > _LIMIT:
        raise ValueError("Reply reference exceeds bounded routing capacity")
    return result


def decode_reply(payload: str) -> ReplyReference:
    if not isinstance(payload, str) or len(payload.encode()) > _LIMIT:
        raise ValueError("Invalid reply reference capacity")
    data = json.loads(payload, object_pairs_hook=_unique)
    if (
        not isinstance(data, dict)
        or set(data) != {"format", "group_id", "workspace", "binding", "channel", "recipient", "metadata"}
        or data["format"] != _FORMAT
        or not isinstance(data["metadata"], dict)
    ):
        raise ValueError("Unsupported reply reference fields/format; no upgrade is performed")
    scope = InputScope(_label(data["group_id"]), Path(_label(data["workspace"], 4096)))
    return ReplyReference(
        scope,
        _label(data["binding"]),
        UserInput(
            group_id=scope.group_id,
            channel=_label(data["channel"]),
            sender=_label(data["recipient"], 1024),
            metadata=data["metadata"],
        ),
    )
