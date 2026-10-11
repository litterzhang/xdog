"""Identified input snapshots for interactive steering/follow-up controls."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from xdog.ai.types import ImageContent

Delivery = Literal["steer", "follow_up"]


@dataclass(frozen=True, slots=True)
class QueuedInput:
    id: str
    content: str
    delivery: Delivery
    selected: bool = False
    images: tuple[ImageContent, ...] = ()
