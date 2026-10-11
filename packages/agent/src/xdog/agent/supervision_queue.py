"""Owned child input queue snapshots/optimistic controls, not conversation or tool authority."""
from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import replace
from typing import Any, cast

from xdog.agent.agent import Agent
from xdog.agent.input_queue import Delivery, QueuedInput
from xdog.agent.supervision_images import MAX_CHILD_ENCODED_IMAGES, check_collection
from xdog.ai.types import ImageContent


def request(value: Any) -> dict[str, Any]:
    if (not isinstance(value, dict) or not isinstance(value.get('action'), str)
            or value['action'] not in {'list', 'edit', 'remove', 'move', 'return', 'recover', 'recover_images',
                                        'recover_task', 'remove_task', 'return_prepare',
                                        'return_commit', 'return_cancel'}):
        raise ValueError('Invalid child queue action')
    action = value['action']
    allowed = {'action'} if action == 'list' else {'action', 'id', 'revision'}
    if action in {'return_commit', 'return_cancel'}:
        allowed.add('token')
    if action == 'return_commit':
        allowed.add('receipt')
    if action == 'edit':
        allowed.add('content')
    elif action == 'move':
        allowed.add('direction')
    if set(value) != allowed:
        raise ValueError('Child queue request has missing/unknown fields')
    if action != 'list':
        identifier, revision = value['id'], value['revision']
        if (not isinstance(identifier, str) or not 0 < len(identifier) <= 128
                or not isinstance(revision, str) or len(revision) != 64
                or any(char not in '0123456789abcdef' for char in revision)):
            raise ValueError('Invalid child queue input/revision')
    if action in {'return_commit', 'return_cancel'} and (
            not isinstance(value['token'], str) or len(value['token']) != 32
            or any(char not in '0123456789abcdef' for char in value['token'])):
        raise ValueError('Invalid live image return token')
    if action == 'return_commit':
        receipt = value['receipt']
        if (not isinstance(receipt, dict) or set(receipt) != {'content', 'image_sha256', 'image_mime_types'}
                or not isinstance(receipt['content'], str) or len(receipt['content'].encode()) > 16000
                or not isinstance(receipt['image_sha256'], list) or not 0 < len(receipt['image_sha256']) <= 4
                or any(not isinstance(digest, str) or len(digest) != 64
                       or any(char not in '0123456789abcdef' for char in digest)
                       for digest in receipt['image_sha256'])
                or not isinstance(receipt['image_mime_types'], list)
                or len(receipt['image_mime_types']) != len(receipt['image_sha256'])
                or any(mime not in ('image/png', 'image/jpeg', 'image/webp') for mime in receipt['image_mime_types'])):
            raise ValueError('Invalid verified live image receipt')
    if action == 'edit' and (not isinstance(value['content'], str) or not value['content'].strip()
                             or len(value['content'].encode()) > 16000):
        raise ValueError('Child queue edit requires bounded nonempty text')
    if action == 'move' and (type(value['direction']) is not int or value['direction'] not in {-1, 1}):
        raise ValueError('Child queue movement is one step in its original delivery lane')
    return dict(value)


def snapshot(agent: Agent | None, child: str, status: str,
             recovery: tuple[QueuedInput, ...] = (), *,
             initial: QueuedInput | None = None, initial_uncertain: bool = False,
             recovery_uncertain: bool = False, returned: tuple[QueuedInput, ...] = ()) -> dict[str, Any]:
    rows = agent.pending_inputs() if agent is not None else recovery
    if len(rows) > 32 or any(len(item.content.encode()) > 16000 for item in rows):
        raise ValueError('Child input queue exceeds its display/control capacity; no truncation')
    public = [{'id': item.id, 'content': item.content, 'delivery': item.delivery, 'selected': item.selected,
               'image_count': len(item.images)} for item in rows]
    if recovery_uncertain:
        for row in public:
            row.update(source='crash-snapshot', uncertain=True)
    if len(returned) > 32:
        raise ValueError('Returned backup capacity exceeded')
    for backup in returned:
        if any(item['id'] == backup.id for item in public):
            raise ValueError('Return backup identity conflicts with queued input')
        public.append({'id': backup.id, 'content': backup.content, 'delivery': backup.delivery, 'selected': False,
                       'image_count': len(backup.images), 'source': 'durable-return', 'uncertain': True})
    if initial is not None:
        if any(row['id'] == initial.id for row in public):
            raise ValueError('Initial-task recovery identity conflicts with queued input; no ambiguous selection')
        public.append({'id': initial.id, 'content': initial.content, 'delivery': initial.delivery,
                       'selected': False, 'image_count': len(initial.images),
                       'source': 'initial-task', 'uncertain': initial_uncertain})
    # Opaque process-local image identity fences replacement without hashing large
    # binary data on the agent loop. This lease is not durable across owner restart.
    identity = [[(part.mime_type, len(part.data), id(part)) for part in item.images] for item in rows]
    identity.extend([(part.mime_type, len(part.data), id(part)) for part in row.images] for row in returned)
    if initial is not None:
        identity.append([(part.mime_type, len(part.data), id(part)) for part in initial.images])
    revision = hashlib.sha256(json.dumps([child, status, id(agent), public, identity],
                                         sort_keys=True, ensure_ascii=True).encode()).hexdigest()
    return {'child_id': child, 'revision': revision, 'inputs': public, 'stopped': agent is None}


def change(agent: Agent | None, child: str, status: str, value: dict[str, Any]) -> dict[str, Any]:
    command = request(value)
    before = snapshot(agent, child, status)
    if command['action'] == 'list':
        return before
    if agent is None or status != 'running' or command['revision'] != before['revision']:
        raise ValueError('Child queue changed/stopped; refresh before editing, no replay')
    item = next((row for row in before['inputs'] if row['id'] == command['id']), None)
    if item is None or item['selected']:
        raise ValueError('Child input already selected/consumed; it cannot be edited or returned')
    if command['action'] == 'return' and item['image_count']:
        raise ValueError('Image queue return needs a verified binary receipt; image input cannot be returned lossily')
    action = 'remove' if command['action'] == 'return' else command['action']
    if not agent.change_input(command['id'], action, command.get('content', ''), command.get('direction', 0)):
        raise ValueError('Child input cannot move across delivery lanes or has already changed')
    result = snapshot(agent, child, status)
    if command['action'] == 'return':
        result['returned'] = item
    return result


def planned_change(agent: Agent, child: str, status: str, value: dict[str, Any]) -> tuple[QueuedInput, ...]:
    """Prospective write-ahead rows; never mutate an Agent before persistence."""
    command = request(value)
    before = snapshot(agent, child, status)
    if status != 'running' or command['revision'] != before['revision']:
        raise ValueError('Child queue changed/stopped; refresh before editing, no replay')
    rows = list(agent.pending_inputs())
    index = next((i for i, row in enumerate(rows) if row.id == command['id']), -1)
    if index < 0 or rows[index].selected:
        raise ValueError('Child input already selected/consumed; it cannot be edited or returned')
    row = rows[index]
    if command['action'] == 'return' and row.images:
        raise ValueError('Image input cannot be returned lossily; use the verified two-phase return')
    if command['action'] in {'remove', 'return'}:
        rows.pop(index)
    elif command['action'] == 'edit':
        rows[index] = replace(row, content=command['content'])
    elif command['action'] == 'move':
        target = index + command['direction']
        if (not 0 <= target < len(rows) or rows[target].delivery != row.delivery or rows[target].selected):
            raise ValueError('Child input cannot move across delivery lanes or has already changed')
        rows[index], rows[target] = rows[target], rows[index]
    else:
        raise ValueError('Unsupported write-ahead queue change')
    return tuple(replace(row, selected=False) for row in rows)


MAX_RECOVERY_BYTES = MAX_CHILD_ENCODED_IMAGES + 512 * 1024


def recovery_to_dicts(rows: tuple[QueuedInput, ...]) -> list[dict[str, Any]]:
    check_collection(part for row in rows for part in row.images)
    result = [{'id': row.id, 'content': row.content, 'delivery': row.delivery,
               'images': [{'data': part.data, 'mime_type': part.mime_type} for part in row.images]} for row in rows]
    if len(json.dumps(result, ensure_ascii=False).encode()) > MAX_RECOVERY_BYTES:
        raise ValueError('Child undelivered input persistence capacity exceeded; no truncated recovery')
    return result


def recovery_from_dicts(value: Any) -> tuple[QueuedInput, ...]:
    if not isinstance(value, list) or len(value) > 32:
        raise ValueError('Invalid child undelivered input collection')
    if len(json.dumps(value, ensure_ascii=False).encode()) > MAX_RECOVERY_BYTES:
        raise ValueError('Child undelivered input capacity exceeded')
    result = []
    for row in value:
        if (not isinstance(row, dict) or set(row) != {'id', 'content', 'delivery', 'images'}
                or not isinstance(row['id'], str) or not 0 < len(row['id']) <= 128
                or not isinstance(row['content'], str) or len(row['content'].encode()) > 16000
                or not isinstance(row['delivery'], str) or row['delivery'] not in {'steer', 'follow_up'}
                or not isinstance(row['images'], list) or len(row['images']) > 4):
            raise ValueError('Invalid child undelivered input')
        images = []
        for part in row['images']:
            if (not isinstance(part, dict) or set(part) != {'data', 'mime_type'}
                    or not isinstance(part['data'], str) or not part['data']
                    or not isinstance(part['mime_type'], str)
                    or part['mime_type'] not in {'image/png', 'image/jpeg', 'image/webp'}):
                raise ValueError('Invalid saved child input image')
            base64.b64decode(part['data'], validate=True)
            images.append(ImageContent(data=part['data'], mime_type=part['mime_type']))
        if not row['content'].strip() and not images:
            raise ValueError('Empty child undelivered input')
        result.append(QueuedInput(row['id'], row['content'], cast(Delivery, row['delivery']), images=tuple(images)))
    if len({row.id for row in result}) != len(result):
        raise ValueError('Duplicate child undelivered input identity')
    check_collection(part for row in result for part in row.images)
    return tuple(result)
