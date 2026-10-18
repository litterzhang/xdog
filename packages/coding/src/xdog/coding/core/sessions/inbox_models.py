"""Durable inbox data, independent of ACP, model construction and executable queues."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal, cast

from xdog.agent.session_input import JsonValue, decode_input
from xdog.coding.core.sessions.ownership import identifier

InboxState = Literal['pending', 'claimed', 'running', 'succeeded', 'failed', 'cancelled', 'interrupted']
STATES = frozenset({'pending', 'claimed', 'running', 'succeeded', 'failed', 'cancelled', 'interrupted'})
LEGACY_STATES = (STATES - {'pending', 'claimed'}) | {'pending_review', 'approved'}
RESOLVED = frozenset({'succeeded', 'failed', 'cancelled'})
MAX_MESSAGE_BYTES = 1024 * 1024
MAX_RECORD_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class InboxMessage:
    session_id: str
    cwd: Path
    message_id: str
    instruction: str
    evidence: tuple[JsonValue, ...]
    source: str
    provenance: dict[str, JsonValue]


@dataclass(frozen=True)
class InboxAttempt:
    id: str
    request_id: str
    generation: str
    branch_id: str
    status: str
    error: str = ''
    batch_id: str = ''


@dataclass(frozen=True)
class InboxRecord:
    message: InboxMessage
    branch_id: str
    status: InboxState
    owner_generation: str = ''
    attempt_id: str = ''
    request_id: str = ''
    error: str = ''
    attempts: tuple[InboxAttempt, ...] = ()
    sequence: int = 0
    batch_id: str = ''
    legacy_order: bool = False


@dataclass(frozen=True)
class EnqueueReceipt:
    duplicate: bool
    record: InboxRecord


def label(value: object, *, empty: bool = False, maximum: int = 256) -> str:
    if not isinstance(value, str) or (not empty and not value.strip()) or '\x00' in value:
        raise ValueError('Expected nonempty inbox text without NUL')
    if len(value.encode('utf-8')) > maximum:
        raise ValueError('Inbox text exceeds capacity')
    return value


def object_fields(value: object, fields: set[str]) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError('Invalid inbox fields')
    return value


def encoded(value: object, maximum: int) -> bytes:
    try:
        result = json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True).encode('utf-8')
    except (TypeError, UnicodeError, RecursionError) as exc:
        raise ValueError('Invalid inbox JSON') from exc
    if len(result) > maximum:
        raise ValueError('Inbox record exceeds capacity')
    return result


def message_data(value: InboxMessage) -> dict[str, object]:
    if not isinstance(value.cwd, Path) or not value.cwd.is_absolute():
        raise ValueError('Inbox workspace must be an absolute path')
    data = asdict(value)
    data['cwd'] = str(value.cwd)
    return data


def message_from_data(value: object) -> InboxMessage:
    data = object_fields(value, {'session_id', 'cwd', 'message_id', 'instruction', 'evidence', 'source', 'provenance'})
    session_id = identifier(cast(str, data['session_id']))
    cwd = Path(label(data['cwd'], maximum=4096))
    if not cwd.is_absolute():
        raise ValueError('Inbox workspace must be absolute')
    checked = decode_input({
        'id': data['message_id'], 'origin': 'acp', 'source': data['source'], 'instruction': data['instruction'],
        'evidence': data['evidence'], 'provenance': data['provenance'],
    }, transport_envelope=False)
    message = InboxMessage(session_id, cwd.resolve(), checked.id, checked.instruction,
                           checked.evidence, checked.source, checked.provenance)
    encoded(message_data(message), MAX_MESSAGE_BYTES)
    return message


def record_data(value: InboxRecord) -> dict[str, object]:
    data = asdict(value)
    data['version'] = 2
    data['message'] = message_data(value.message)
    return data


def record_from_data(value: object) -> InboxRecord:
    if not isinstance(value, dict) or type(value.get('version')) is not int or value['version'] not in {1, 2}:
        raise ValueError('Unsupported inbox record version')
    legacy = value['version'] == 1
    fields = {'version', 'message', 'branch_id', 'status', 'owner_generation',
              'attempt_id', 'request_id', 'error', 'attempts'}
    if not legacy:
        fields |= {'sequence', 'batch_id', 'legacy_order'}
    data = object_fields(value, fields)
    status = label(data['status'])
    if status not in (LEGACY_STATES if legacy else STATES):
        raise ValueError('Invalid inbox state')
    attempts = data['attempts']
    if not isinstance(attempts, list):
        raise ValueError('Invalid inbox attempts')
    sequence, legacy_order = data.get('sequence', 0), data.get('legacy_order', True)
    if not legacy and (type(sequence) is not int or not 0 < sequence < 2**63 or type(legacy_order) is not bool):
        raise ValueError('Invalid inbox sequence/migration metadata')
    checked = []
    for item in attempts:
        a = object_fields(item, {'id', 'request_id', 'generation', 'branch_id', 'status', 'error'}
                          | (set() if legacy else {'batch_id'}))
        allowed = LEGACY_STATES - {'pending_review'} if legacy else STATES - {'pending'}
        if legacy_order:
            allowed |= {'approved'}  # Historical legacy fact; never a current authorization.
        if label(a['status']) not in allowed:
            raise ValueError('Invalid inbox attempt state')
        checked.append(InboxAttempt(label(a['id']), label(a['request_id']), label(a['generation']),
                                    label(a['branch_id'], empty=True), label(a['status']),
                                    label(a['error'], empty=True, maximum=4096),
                                    label(a.get('batch_id', ''), empty=True)))
    record = InboxRecord(message_from_data(data['message']), label(data['branch_id'], empty=True),
                         cast(InboxState, status), label(data['owner_generation'], empty=True),
                         label(data['attempt_id'], empty=True), label(data['request_id'], empty=True),
                         label(data['error'], empty=True, maximum=4096), tuple(checked),
                         cast(int, sequence), label(data.get('batch_id', ''), empty=True), cast(bool, legacy_order))
    if len({a.id for a in checked}) != len(checked) or len({a.request_id for a in checked}) != len(checked):
        raise ValueError('Duplicate inbox attempt identity')
    if bool(record.attempt_id) != bool(record.request_id):
        raise ValueError('Inbox current attempt identity mismatch')
    if record.attempt_id and (not checked or checked[-1].id != record.attempt_id
                             or checked[-1].request_id != record.request_id):
        raise ValueError('Inbox current attempt identity mismatch')
    active = {'approved', 'running'} if legacy else {'claimed', 'running'}
    if status in active and (not record.owner_generation or not record.attempt_id
                              or checked[-1].generation != record.owner_generation
                              or checked[-1].status != status):
        raise ValueError('Inbox claim/running record is not bound to an owner attempt')
    if any(a.branch_id != record.branch_id for a in checked):
        raise ValueError('Inbox attempt branch mismatch')
    if status in RESOLVED | {'interrupted'}:
        unstarted_cancel = not legacy and status == 'cancelled' and not record.attempt_id
        if not unstarted_cancel and (not record.attempt_id or not checked or checked[-1].status != status):
            raise ValueError('Inbox terminal attempt state mismatch')
        if unstarted_cancel and (record.owner_generation or record.request_id or record.batch_id):
            raise ValueError('Unstarted cancellation cannot retain an active execution')
    if status in {'pending', 'pending_review'} and (
        record.owner_generation or record.attempt_id or record.request_id or record.batch_id
    ):
        raise ValueError('Pending message cannot retain execution authority')
    if not legacy and record.attempt_id and record.batch_id != checked[-1].batch_id:
        raise ValueError('Inbox current attempt batch identity mismatch')
    if not legacy and status == 'running' and (
        not record.batch_id or checked[-1].batch_id != record.batch_id
    ):
        raise ValueError('Running inbox message requires its batch identity')
    if legacy:
        from dataclasses import replace

        if status in {'pending_review', 'approved'}:
            record = replace(record, status='pending', owner_generation='', attempt_id='', request_id='')
        elif status == 'running':
            batch_id = 'legacy-' + hashlib.sha256(record.attempt_id.encode()).hexdigest()[:32]
            record = replace(record, batch_id=batch_id,
                             attempts=(*record.attempts[:-1], replace(record.attempts[-1], batch_id=batch_id)))
    return record
