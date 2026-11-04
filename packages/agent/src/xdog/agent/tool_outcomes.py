"""Inert tool outcome data. Neither a permission grant nor a retry policy."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

FailureKind = Literal['arguments', 'permission', 'unavailable', 'execution', 'cancelled', 'unknown']
EffectState = Literal['not_started', 'completed', 'unknown']


@dataclass(frozen=True, slots=True)
class ToolFailure:
    kind: FailureKind
    message: str
    effect: EffectState = 'unknown'
    code: str = ''

    def __post_init__(self) -> None:
        if self.kind not in ('arguments', 'permission', 'unavailable', 'execution', 'cancelled', 'unknown'):
            raise ValueError('Invalid tool failure kind')
        if self.effect not in ('not_started', 'completed', 'unknown'):
            raise ValueError('Invalid tool effect state')
        if not isinstance(self.message, str) or not isinstance(self.code, str):
            raise ValueError('Tool failure message/code must be text')

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ToolArtifact:
    """Opaque reference to host-owned output, never a pathname or automatic fetch."""

    id: str
    media_type: str = 'text/plain'
    size_bytes: int | None = None

    def __post_init__(self) -> None:
        if (not isinstance(self.id, str) or not self.id.strip() or len(self.id) > 1024
                or any(ord(char) < 32 for char in self.id)):
            raise ValueError('Artifact requires a bounded opaque identifier')
        if not isinstance(self.media_type, str) or not self.media_type or len(self.media_type) > 128:
            raise ValueError('Invalid artifact media type')
        if self.size_bytes is not None and (type(self.size_bytes) is not int or self.size_bytes < 0):
            raise ValueError('Artifact size must be a non-negative integer or unknown')

    def to_dict(self) -> dict[str, str | int | None]:
        return asdict(self)
