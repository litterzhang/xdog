"""Scope/checksum-bound child image copy receiver, independent of agent/application policy."""
from __future__ import annotations

import base64
import hashlib
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, BinaryIO

from xdog.tui.attachments import Attachment, attachment
from xdog.tui.child_drafts import draft_receipt

MAX_BYTES = 40 * 1024 * 1024


def decode_recovery(value: dict[str, Any]) -> tuple[Attachment, ...]:
    images, receipt = value.get('images'), value.get('receipt')
    if not isinstance(images, list) or not 0 < len(images) <= 4 or not isinstance(receipt, dict):
        raise ValueError('Invalid child image recovery payload/receipt')
    result = []
    for index, row in enumerate(images):
        if (not isinstance(row, dict) or set(row) != {'data', 'mime_type'}
                or not isinstance(row['data'], str) or len(row['data']) > ((10 * 1024 * 1024 + 2) // 3) * 4):
            raise ValueError('Invalid bounded child recovery image')
        item = attachment(base64.b64decode(row['data'], validate=True), f'recovered-child-image-{index + 1}')
        if item.mime_type != row['mime_type']:
            raise ValueError('Recovered child image MIME mismatch')
        result.append(item)
    result_tuple = tuple(result)
    if draft_receipt(receipt.get('content', ''), result_tuple) != receipt:
        raise ValueError('Recovered child image receipt mismatch')
    return result_tuple


@dataclass
class _Transfer:
    scope: dict[str, str]
    manifest: list[dict[str, Any]] = field(default_factory=list)
    receipt: dict[str, Any] = field(default_factory=dict)
    files: list[BinaryIO] = field(default_factory=list)
    hashes: list[Any] = field(default_factory=list)
    sizes: list[int] = field(default_factory=list)
    indices: list[int] = field(default_factory=list)
    image_index: int = 0

    def close(self) -> None:
        for file in self.files:
            file.close()


class ChildRecoveryReceiver:
    def __init__(self, publish: Callable[[dict[str, Any]], None]) -> None:
        self.publish = publish
        self.pending: dict[str, _Transfer] = {}
        self._lock = threading.RLock()
        self._closed = False

    def expect(self, scope: dict[str, str]) -> None:
        if (not {'request_id', 'session_id', 'child_id', 'input_id', 'queue_revision'} <= set(scope)
                or any(not isinstance(value, str) or not value or len(value) > 128 for value in scope.values())):
            raise ValueError('Invalid child recovery request scope')
        with self._lock:
            key = scope['request_id']
            if self._closed or key in self.pending or len(self.pending) >= 4:
                raise ValueError('Child recovery receiver closed/reused/capacity exceeded')
            self.pending[key] = _Transfer(dict(scope))

    def cancel(self, key: str) -> None:
        with self._lock:
            value = self.pending.pop(key, None)
            if value is not None:
                value.close()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            for key in tuple(self.pending):
                self.cancel(key)

    def feed(self, event: dict[str, Any]) -> None:
        with self._lock:
            key = event.get('request_id')
            transfer = self.pending.get(key) if isinstance(key, str) else None
            if transfer is None or self._closed:
                return  # No unsolicited or late transfer adopts a receiver/draft owner.
            try:
                if any(event.get(name) != value for name, value in transfer.scope.items()):
                    raise ValueError('Recovery scope changed')
                kind = event.get('type')
                if kind == 'child_recovery_begin':
                    self._begin(transfer, event)
                elif kind == 'child_recovery_chunk':
                    self._chunk(transfer, event)
                elif kind == 'child_recovery_end':
                    self._end(transfer)
                else:
                    raise ValueError('Unknown recovery frame')
            except (ValueError, TypeError, KeyError, OSError):
                scope = dict(transfer.scope)
                self.cancel(scope['request_id'])
                self.publish({'type': 'child_recovery_failed', **scope,
                              'error': 'Child image recovery invalid/incomplete; durable original retained'})

    def _begin(self, transfer: _Transfer, event: dict[str, Any]) -> None:
        manifest, receipt = event.get('images'), event.get('receipt')
        if (transfer.files or not isinstance(manifest, list) or not 0 < len(manifest) <= 4
                or not isinstance(receipt, dict) or set(receipt) != {'content', 'image_sha256', 'image_mime_types'}
                or not isinstance(receipt['content'], str) or len(receipt['content'].encode()) > 16000):
            raise ValueError('Invalid recovery begin')
        for row in manifest:
            if (not isinstance(row, dict) or set(row) != {'mime_type', 'sha256', 'bytes'}
                    or row['mime_type'] not in {'image/png', 'image/jpeg', 'image/webp'}
                    or type(row['bytes']) is not int or not 0 < row['bytes'] <= 10 * 1024 * 1024
                    or not isinstance(row['sha256'], str) or len(row['sha256']) != 64
                    or any(char not in '0123456789abcdef' for char in row['sha256'])):
                raise ValueError('Invalid recovery manifest')
        if (receipt['image_sha256'] != [row['sha256'] for row in manifest]
                or receipt['image_mime_types'] != [row['mime_type'] for row in manifest]
                or sum(row['bytes'] for value in self.pending.values() for row in value.manifest)
                + sum(row['bytes'] for row in manifest) > MAX_BYTES):
            raise ValueError('Recovery manifest/retained budget mismatch')
        transfer.manifest = [dict(row) for row in manifest]
        transfer.receipt = {'content': receipt['content'], 'image_sha256': list(receipt['image_sha256']),
                            'image_mime_types': list(receipt['image_mime_types'])}
        for _ in manifest:
            transfer.files.append(tempfile.TemporaryFile())
            transfer.hashes.append(hashlib.sha256())
            transfer.sizes.append(0)
            transfer.indices.append(0)

    def _chunk(self, transfer: _Transfer, event: dict[str, Any]) -> None:
        image_index, index, data = event.get('image_index'), event.get('index'), event.get('data')
        if (type(image_index) is not int or not 0 <= image_index < len(transfer.files)
                or type(index) is not int or index != transfer.indices[image_index]
                or not isinstance(data, str) or len(data) > 100000 or image_index < transfer.image_index):
            raise ValueError('Recovery chunk out of order')
        if image_index > transfer.image_index and any(transfer.sizes[old] != transfer.manifest[old]['bytes']
                                                    for old in range(image_index)):
            raise ValueError('Previous recovery image incomplete')
        block = base64.b64decode(data, validate=True)
        if (not 0 < len(block) <= 64 * 1024
                or transfer.sizes[image_index] + len(block) > transfer.manifest[image_index]['bytes']):
            raise ValueError('Recovery chunk exceeds bound')
        transfer.image_index = image_index
        transfer.files[image_index].write(block)
        transfer.hashes[image_index].update(block)
        transfer.sizes[image_index] += len(block)
        transfer.indices[image_index] += 1

    def _end(self, transfer: _Transfer) -> None:
        if not transfer.files:
            raise ValueError('Recovery ended before begin')
        images = []
        for index, meta in enumerate(transfer.manifest):
            if transfer.sizes[index] != meta['bytes'] or transfer.hashes[index].hexdigest() != meta['sha256']:
                raise ValueError('Recovery checksum/size mismatch')
            transfer.files[index].seek(0)
            item = attachment(transfer.files[index].read(), f'recovered-child-image-{index + 1}')
            if item.mime_type != meta['mime_type']:
                raise ValueError('Recovery MIME mismatch')
            images.append(item)
        ready = {'type': 'child_recovery_ready', **transfer.scope, 'images': tuple(images),
                 'receipt': dict(transfer.receipt)}
        if draft_receipt(transfer.receipt['content'], tuple(images)) != transfer.receipt:
            raise ValueError('Recovered bytes do not match receipt')
        self.cancel(transfer.scope['request_id'])
        self.publish(ready)
