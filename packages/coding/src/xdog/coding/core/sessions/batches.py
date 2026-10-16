"""Frozen per-input snapshots and a fixed shared turn envelope."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from xdog.agent.session_input import SessionInput, decode_input, input_data
from xdog.ai.types import ImageContent

MAX_BATCH_BYTES = 1024 * 1024


@dataclass(frozen=True)
class TurnInput:
    value: SessionInput
    images: tuple[ImageContent, ...] = ()
    input_origin: str | None = None
    raw_text: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, 'value', decode_input(input_data(self.value)))
        object.__setattr__(self, 'images', tuple(self.images))


def input_size(item: TurnInput) -> int:
    """Count actual member input, not a second copy of the shared batch envelope."""
    data = input_data(item.value)
    if item.raw_text is not None:
        data['instruction'] = item.raw_text
    if item.images:
        # Count inline content too; images have their own provider count/MIME validation.
        data['images'] = [asdict(image) for image in item.images]
    return len(json.dumps(data, ensure_ascii=True, allow_nan=False).encode())


@dataclass(frozen=True)
class TurnBatch:
    id: str
    session_id: str
    branch_id: str
    generation: str
    items: tuple[TurnInput, ...]

    @property
    def request_ids(self) -> tuple[str, ...]:
        return tuple(item.value.id for item in self.items)
