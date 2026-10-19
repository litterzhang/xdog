"""Immutable ordinary-input identity, independent of composer and Agent lanes."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from xdog.tui.attachments import Attachment

TERMINAL = frozenset({'succeeded', 'failed', 'cancelled', 'interrupted'})


def valid_namespace(value: object) -> bool:
    return isinstance(value, str) and len(value) == 32 and all(char in '0123456789abcdef' for char in value)


@dataclass(frozen=True)
class NativeSubmission:
    group_id: str
    namespace: str
    run_id: str
    text: str
    images: tuple[Attachment, ...] = ()
    acknowledged: bool = False
    phase: Literal['awaiting_ack', 'accepted', 'unknown'] = 'awaiting_ack'

    def __post_init__(self) -> None:
        if (not valid_namespace(self.namespace) or not self.group_id or not self.run_id
                or not isinstance(self.images, tuple) or any(not isinstance(item, Attachment) for item in self.images)):
            raise ValueError('Invalid native submission identity or immutable attachments')


def validate_receipt(value: object, submission: NativeSubmission) -> dict[str, str]:
    if (not isinstance(value, dict) or value.get('id') != 'native:' + submission.run_id
            or not all(isinstance(value.get(key), str) for key in
                       ('id', 'status', 'attempt_id', 'session_id', 'response', 'error'))
            or value['status'] not in TERMINAL | {'pending', 'claimed', 'running'}):
        raise ValueError('Native receipt does not match the original input')
    return {key: value[key] for key in ('id', 'status', 'attempt_id', 'session_id', 'response', 'error')}
