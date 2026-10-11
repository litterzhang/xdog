"""Bounded remote parent recovery, followed by exact review and local composer CAS.

No application/agent imports. Feed on the client's owner loop; finish (image decode)
must run off the terminal/event loop. Receiving bytes is never adoption or submission.
"""
from __future__ import annotations

import base64
import hashlib
import json
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, BinaryIO

from xdog.tui.attachments import MAX_IMAGE_BYTES, MAX_RETAINED_BYTES, attachment
from xdog.tui.child_drafts import ChildDraft, draft_receipt
from xdog.tui.components.mcp_sampling_panel import McpSamplingPanel, _json


def composer_receipt(draft: ChildDraft, revision: int) -> dict[str, Any]:
    if type(revision) is not int or revision < 0:
        raise ValueError('Invalid composer revision')
    digest = hashlib.sha256(json.dumps(draft_receipt(draft.text, draft.images),
                                      sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return {'revision': revision, 'empty': not draft.text and not draft.images, 'sha256': digest}


@dataclass
class _Transfer:
    scope: dict[str, Any]
    composer: dict[str, Any]
    snapshot_id: str
    snapshot_sha256: str
    deadline: float
    body: dict[str, Any] | None = None
    files: list[BinaryIO] = field(default_factory=list)
    sizes: list[int] = field(default_factory=list)
    indices: list[int] = field(default_factory=list)
    image_index: int = 0
    ended: bool = False
    canceled: threading.Event = field(default_factory=threading.Event)

    def close(self) -> None:
        for stream in self.files:
            stream.close()


@dataclass(frozen=True)
class ParentRecovery:
    scope_json: str
    review_json: str
    draft: ChildDraft
    deadline: float
    _consumed: bool = field(default=False, init=False, repr=False)

    def panel(self, current_scope: Callable[[], dict[str, Any]], current_composer: Callable[[], dict[str, Any]],
              adopt: Callable[[ChildDraft, dict[str, Any]], None], cancel: Callable[[], None]) -> McpSamplingPanel:
        body = json.loads(self.review_json)
        scope = json.loads(self.scope_json)
        def confirm(_response: dict[str, Any]) -> None:
            if time.monotonic() >= self.deadline:
                raise ValueError('Parent recovery review expired')
            if self._consumed:
                raise ValueError('Parent recovery already consumed')
            current = current_composer()
            if (_json(current_scope()) != _json(scope)
                    or _json(current) != _json(body['composer'])
                    or current.get('empty') is not True):
                raise ValueError('Parent recovery owner/composer changed; no adoption')
            object.__setattr__(self, '_consumed', True)  # No retry if local adoption effects are uncertain.
            adopt(self.draft, body['composer'])  # Host must atomically recheck receipt before changing its composer.
        event = {'type': 'sampling_review', 'phase': 'output', 'id': '0' * 32, 'receipt': '1' * 32,
                 'body': body, 'sha256': hashlib.sha256(_json(body).encode()).hexdigest(),
                 'notice': 'Review saved parent snapshot; copy into unchanged EMPTY composer only. Never submit.'}
        def revoke() -> None:
            object.__setattr__(self, '_consumed', True)
            cancel()
        return McpSamplingPanel(event, (), confirm, revoke)


class ParentDraftReceiver:
    def __init__(self, remaining_bytes: Callable[[], int] = lambda: MAX_RETAINED_BYTES) -> None:
        self.remaining_bytes = remaining_bytes
        self.pending: dict[str, _Transfer] = {}
        self.decoding: dict[str, _Transfer] = {}
        self._lock = threading.RLock()
        self.used: set[str] = set()
        self.closed = False

    def expect(self, scope: dict[str, Any], composer: dict[str, Any], snapshot_id: str,
               snapshot_sha256: str) -> None:
        with self._lock:
            required = {'request_id', 'session_id', 'session_revision', 'parent_draft_owner', 'parent_draft_generation'}
            if (not required <= scope.keys() or type(scope['parent_draft_generation']) is not int
                    or scope['parent_draft_generation'] < 0
                    or any(not isinstance(value, str) or not 0 < len(value.encode()) <= 128
                           for key, value in scope.items() if key != 'parent_draft_generation')
                    or type(composer.get('revision')) is not int or composer['revision'] < 0
                    or set(composer) != {'revision', 'empty', 'sha256'} or composer.get('empty') is not True
                    or composer['sha256'] != composer_receipt(ChildDraft(), composer['revision'])['sha256']
                    or not isinstance(snapshot_id, str) or len(snapshot_id) != 32
                    or any(c not in '0123456789abcdef' for c in snapshot_id)
                    or not isinstance(snapshot_sha256, str) or len(snapshot_sha256) != 64
                    or any(c not in '0123456789abcdef' for c in snapshot_sha256)):
                raise ValueError('Invalid original parent recovery expectation')
            self.poll()
            key = scope['request_id']
            if self.closed or key in self.used or len(self.used) >= 256 or len(self.pending) + len(self.decoding) >= 4:
                raise ValueError('Parent receiver closed/reused/capacity exhausted')
            self.used.add(key)
            self.pending[key] = _Transfer(dict(scope), dict(composer), snapshot_id, snapshot_sha256,
                                          time.monotonic() + 30)

    def feed(self, event: dict[str, Any]) -> None:
        with self._lock:
            key = event.get('request_id')
            transfer = self.pending.get(key) if isinstance(key, str) else None
            if transfer is None or self.closed:
                return  # Unsolicited/late data never opens a receiver.
            try:
                if time.monotonic() >= transfer.deadline:
                    raise ValueError('Parent transfer expired')
                if any(type(event.get(name)) is not type(value) or event.get(name) != value
                       for name, value in transfer.scope.items()):
                    raise ValueError('Parent recovery scope changed')
                kind = event.get('type')
                allowed = {'type', *transfer.scope}
                allowed |= {'review'} if kind == 'parent_draft_begin' else {
                    'image_index', 'index', 'data'} if kind == 'parent_draft_chunk' else set()
                if set(event) - allowed or transfer.ended:
                    raise ValueError('Unknown/late parent recovery fields')
                if kind == 'parent_draft_begin':
                    if transfer.body is not None:
                        raise ValueError('Duplicate parent recovery begin')
                    body = json.loads(_json(event['review']))
                    if (set(body) != {'operation', 'composer', 'snapshot_id', 'snapshot_sha256', 'label',
                                      'content', 'images', 'notice'} or body['operation'] != 'recover'
                            or _json(body['composer']) != _json(transfer.composer)
                            or body['snapshot_id'] != transfer.snapshot_id
                            or body['snapshot_sha256'] != transfer.snapshot_sha256
                            or not isinstance(body['content'], str) or len(body['content'].encode()) > 65536
                            or not isinstance(body['label'], str) or len(body['label'].encode()) > 4096
                            or not isinstance(body['notice'], str) or len(body['notice'].encode()) > 8192
                            or not isinstance(body['images'], list) or len(body['images']) > 4):
                        raise ValueError('Invalid parent recovery review')
                    for row in body['images']:
                        if (not isinstance(row, dict) or set(row) != {'name', 'mime_type', 'sha256', 'bytes'}
                                or not isinstance(row['name'], str) or len(row['name'].encode()) > 1024
                                or row['mime_type'] not in {'image/png', 'image/jpeg', 'image/webp'}
                                or type(row['bytes']) is not int or not 0 < row['bytes'] <= MAX_IMAGE_BYTES
                                or not isinstance(row['sha256'], str) or len(row['sha256']) != 64
                                or any(c not in '0123456789abcdef' for c in row['sha256'])):
                            raise ValueError('Invalid parent image manifest')
                    reserved = sum(row['bytes'] for other in (*self.pending.values(), *self.decoding.values())
                                   if other.body is not None
                                   for row in other.body['images'])
                    available = self.remaining_bytes()
                    if (type(available) is not int or available < 0
                            or reserved + sum(row['bytes'] for row in body['images'])
                            > min(MAX_RETAINED_BYTES, available)):
                        raise ValueError('Parent binary capacity exceeded')
                    transfer.body = body
                    transfer.files = [tempfile.TemporaryFile() for _ in body['images']]
                    transfer.sizes = [0] * len(transfer.files)
                    transfer.indices = [0] * len(transfer.files)
                elif kind == 'parent_draft_chunk':
                    if transfer.body is None:
                        raise ValueError('Chunk before begin')
                    image, index, data = event.get('image_index'), event.get('index'), event.get('data')
                    if (type(image) is not int or type(index) is not int or not 0 <= image < len(transfer.files)
                            or index != transfer.indices[image] or image != transfer.image_index
                            or not isinstance(data, str) or len(data) > 87384):
                        raise ValueError('Invalid parent chunk ordering/capacity')
                    decoded = base64.b64decode(data, validate=True)
                    if not decoded or len(decoded) > 65536:
                        raise ValueError('Invalid parent chunk bytes')
                    transfer.sizes[image] += len(decoded)
                    if transfer.sizes[image] > transfer.body['images'][image]['bytes']:
                        raise ValueError('Parent image exceeds manifest')
                    transfer.files[image].write(decoded)
                    transfer.indices[image] += 1
                    if transfer.sizes[image] == transfer.body['images'][image]['bytes']:
                        transfer.image_index += 1
                elif kind == 'parent_draft_end':
                    if transfer.body is None or transfer.image_index != len(transfer.files):
                        raise ValueError('Incomplete parent transfer')
                    transfer.ended = True
                else:
                    raise ValueError('Unknown parent recovery frame')
            except (ValueError, TypeError, KeyError, OSError):
                self.cancel(transfer.scope['request_id'])
                raise ValueError('Parent recovery invalid; no composer adoption') from None

    def finish(self, key: str, result: dict[str, Any]) -> ParentRecovery:
        with self._lock:
            transfer = self.pending.pop(key, None)
            if transfer is None:
                raise ValueError('No original parent transfer')
            self.decoding[key] = transfer
        try:
            if time.monotonic() >= transfer.deadline or not transfer.ended or transfer.body is None:
                raise ValueError('Incomplete parent transfer')
            if (result.get('type') != 'parent_draft_result' or result.get('success') is not True
                    or result.get('action') != 'read'
                    or any(type(result.get(name)) is not type(value) or result.get(name) != value
                           for name, value in transfer.scope.items())
                    or _json(result.get('recovery')) != _json(transfer.body)):
                raise ValueError('Parent recovery lacks matching successful completion receipt')
            available = self.remaining_bytes()
            if (type(available) is not int or available < 0
                    or sum(row['bytes'] for row in transfer.body['images']) > available):
                raise ValueError('Parent binary capacity changed before decode')
            images = []
            for stream, row in zip(transfer.files, transfer.body['images'], strict=True):
                stream.seek(0)
                data = stream.read(MAX_IMAGE_BYTES + 1)
                if len(data) != row['bytes'] or hashlib.sha256(data).hexdigest() != row['sha256']:
                    raise ValueError('Parent image hash/size mismatch')
                image = attachment(data, row['name'])
                if image.mime_type != row['mime_type']:
                    raise ValueError('Parent image MIME mismatch')
                images.append(image)
            if transfer.canceled.is_set():
                raise ValueError('Parent recovery decode revoked')
            return ParentRecovery(_json(transfer.scope), _json(transfer.body),
                                  ChildDraft(transfer.body['content'], images=tuple(images), uncertain=True),
                                  time.monotonic() + 30)
        finally:
            transfer.close()
            with self._lock:
                self.decoding.pop(key, None)

    def poll(self) -> None:
        with self._lock:
            for key, transfer in tuple(self.pending.items()):
                if time.monotonic() >= transfer.deadline:
                    self.cancel(key)

    def cancel(self, key: str) -> None:
        with self._lock:
            decoding = self.decoding.get(key)
            if decoding is not None:
                decoding.canceled.set()
            transfer = self.pending.pop(key, None)
            if transfer is not None:
                transfer.canceled.set()
                transfer.close()

    def close(self) -> None:
        with self._lock:
            self.closed = True
            for key in (*tuple(self.pending), *tuple(self.decoding)):
                self.cancel(key)
