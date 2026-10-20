"""Validated choices inherited on conversation rotation, not another state owner."""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from xdog.claw.core.persistence.transcript_store import TranscriptStore


def inherited(store: TranscriptStore, identifier: str) -> dict[str, Any]:
    """Snapshot explicit preferences before any destination files are created.

    Current context sidecars remain authoritative. Reasoning retains its model
    binding, including when the user temporarily selected a different model.
    Opaque extension entries and all conversation/execution state stay behind.
    """
    model = store.get_model(identifier)
    tools = store.get_active_tools(identifier)
    skills = store.get_active_skills(identifier)
    store.get_reasoning(identifier, model)  # Validate even an inactive model-bound choice.
    values: dict[str, Any] = {}
    if model:
        values['model'] = {'model': model}
    if store._path(identifier).with_suffix('.tools.json').exists():
        values['tools'] = {'active': list(tools) if tools is not None else None}
    if store._path(identifier).with_suffix('.skills.json').exists():
        values['skills'] = {'active': list(skills)}
    thinking = store._path(identifier).with_suffix('.thinking.json')
    if thinking.exists():
        values['thinking'] = json.loads(thinking.read_text(encoding='utf-8'))
    return values
