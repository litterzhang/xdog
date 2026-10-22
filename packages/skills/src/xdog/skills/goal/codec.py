"""Finite data-only formats. Saved text never selects code, tools or permissions."""
from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from .models import Goal, Task


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate Goal JSON key')
        result[key] = value
    return result


def json_text(value: object, *, maximum: int | None = 262144, ensure_ascii: bool = True) -> str:
    result = json.dumps(value, sort_keys=True, ensure_ascii=ensure_ascii, allow_nan=False)
    if maximum is not None and len(result.encode()) > maximum:
        raise ValueError('Goal data exceeds response/file capacity; no data was dropped')
    return result


def read_json(value: str) -> dict[str, Any]:
    result = json.loads(value, object_pairs_hook=_unique,
                        parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Non-finite JSON')))
    if not isinstance(result, dict):
        raise ValueError('Expected a Goal JSON object')
    return result


def encode_goal(goal: Goal) -> str:
    return json_text({'format': 'xdog.goal.v2', **asdict(goal)})


def decode_goal(payload: str) -> Goal:
    data = read_json(payload)
    # Historical wait(host) is retained verbatim, never normalized to active.
    # Legacy v2 records remain immutable on disk; absence means not recorded.
    fields = set(Goal.__dataclass_fields__)
    question_fields = {'question', 'impact', 'resume_when'}
    if data.pop('format', None) != 'xdog.goal.v2' or set(data) not in (fields, fields - question_fields):
        raise ValueError('Unsupported Goal record')
    if not isinstance(data['criteria'], list):
        raise ValueError('Invalid Goal criteria')
    data['criteria'] = tuple(data['criteria'])
    return Goal(**data)


def encode_task(task: Task) -> str:
    return json_text({'format': 'xdog.goal_task.v2', **asdict(task)}) + '\n'


def decode_task(payload: str) -> Task:
    data = read_json(payload)
    if data.pop('format', None) != 'xdog.goal_task.v2' or set(data) != set(Task.__dataclass_fields__):
        raise ValueError('Unsupported task file; re-read the original before editing')
    for key in ('depends_on', 'evidence'):
        if not isinstance(data[key], list):
            raise ValueError('Invalid task collection')
        data[key] = tuple(data[key])
    return Task(**data)
