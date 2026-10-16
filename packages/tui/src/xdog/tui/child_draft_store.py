"""Private explicit child-draft snapshots; storage only, never execution/configuration authority.

Hosts must run these bounded filesystem/codec operations off their UI/event loop
and separately review save/load/discard. Loaded drafts are conservative copies,
not resumable conversations, active queues or restored permission grants.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import stat
import time
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from xdog.tui.attachments import MAX_RETAINED_BYTES, Attachment, attachment
from xdog.tui.child_drafts import ChildDraft, draft_receipt

MAX_DRAFT_BYTES = ((MAX_RETAINED_BYTES + 2) // 3) * 4 + 512 * 1024
MAX_DRAFTS = 32
LOCK_TIMEOUT = 3.0
_KEY = re.compile(r'[a-f0-9]{32}')


class DraftStoreFullError(ValueError):
    """A create-only snapshot was refused at the fixed capacity, before publishing bytes."""


@dataclass(frozen=True)
class SavedChildDraft:
    id: str
    sha256: str
    label: str
    source_scope: str
    source_action: str
    source_slot: str
    draft: ChildDraft


def _plain(value: Any, maximum: int, field: str) -> str:
    if not isinstance(value, str) or len(value.encode('utf-8')) > maximum:
        raise ValueError('Invalid saved child draft ' + field)
    return value


def encode_draft(draft: ChildDraft, *, label: str, scope: str, action: str, slot: str) -> bytes:
    _plain(draft.text, 65536 if action == 'parent' else 16000, 'text')
    _plain(label, 4096, 'label')
    _plain(scope, 1024, 'scope')
    _plain(slot, 512, 'slot')
    if action not in {'start', 'steer', 'follow-up', 'parent'} or draft.access not in {'read-only', 'inherit'}:
        raise ValueError('Invalid saved child draft source action/access')
    if type(draft.uncertain) is not bool or (draft.cursor is not None and (
            type(draft.cursor) is not int or not 0 <= draft.cursor <= len(draft.text))):
        raise ValueError('Invalid saved child draft cursor/uncertainty')
    if (not isinstance(draft.config, dict) or len(json.dumps(draft.config, allow_nan=False).encode()) > 64 * 1024
            or len(draft.images) > 4 or any(not isinstance(item, Attachment) for item in draft.images)
            or sum(len(item.data) for item in draft.images) > MAX_RETAINED_BYTES):
        raise ValueError('Saved child draft exceeds metadata/image capacity')
    for item in draft.images:
        checked = attachment(item.data, item.name)
        if checked.mime_type != item.mime_type:
            raise ValueError('Saved child draft image MIME mismatch')
        _plain(item.name, 1024, 'image name')
    body = {'version': 1, 'label': label, 'scope': scope, 'action': action, 'slot': slot,
            'text': draft.text, 'access': draft.access, 'config': draft.config, 'cursor': draft.cursor,
            'uncertain': draft.uncertain, 'receipt': draft_receipt(draft.text, draft.images),
            'images': [{'name': item.name, 'mime_type': item.mime_type,
                        'data': base64.b64encode(item.data).decode('ascii')} for item in draft.images]}
    encoded = json.dumps(body, ensure_ascii=True, allow_nan=False).encode('utf-8')
    if len(encoded) > MAX_DRAFT_BYTES:
        raise ValueError('Saved child draft exceeds record capacity')
    return encoded


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('Duplicate saved child draft field')
        value[key] = item
    return value


def decode_draft(key: str, data: bytes) -> SavedChildDraft:
    if _KEY.fullmatch(key) is None or len(data) > MAX_DRAFT_BYTES:
        raise ValueError('Invalid saved child draft identity/size')
    value = json.loads(data, object_pairs_hook=_unique,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite draft metadata')))
    if (not isinstance(value, dict) or set(value) != {'version', 'label', 'scope', 'action', 'slot', 'text',
            'access', 'config', 'cursor', 'uncertain', 'receipt', 'images'}
            or type(value['version']) is not int or value['version'] != 1
            or not isinstance(value['images'], list) or len(value['images']) > 4):
        raise ValueError('Invalid saved child draft schema')
    images = []
    for row in value['images']:
        if (not isinstance(row, dict) or set(row) != {'name', 'mime_type', 'data'}
                or not isinstance(row['data'], str) or len(row['data']) > ((10 * 1024 * 1024 + 2) // 3) * 4):
            raise ValueError('Invalid saved child draft image')
        item = attachment(base64.b64decode(row['data'], validate=True), _plain(row['name'], 1024, 'image name'))
        if item.mime_type != row['mime_type']:
            raise ValueError('Saved child draft image MIME mismatch')
        images.append(item)
    original = ChildDraft(value['text'], value['access'], value['config'], tuple(images),
                          value['uncertain'], value['cursor'])
    # Validate the same limits/schema on both sides, including exact byte receipt.
    encode_draft(original, label=value['label'], scope=value['scope'], action=value['action'], slot=value['slot'])
    if draft_receipt(original.text, original.images) != value['receipt']:
        raise ValueError('Saved child draft byte receipt mismatch')
    # Retain provenance, NOT permissions/model/tools/preset or cursor execution state.
    recovered = ChildDraft(original.text, 'read-only', {'inherit_context': False}, original.images, uncertain=True)
    return SavedChildDraft(key, hashlib.sha256(data).hexdigest(), value['label'], value['scope'],
                           value['action'], value['slot'], recovered)


class ChildDraftStore:
    """POSIX pinned-directory private, create-only snapshots with exact discard receipts."""

    def __init__(self, root: Path, *, create_parent: bool = False) -> None:
        self.root = root.expanduser().absolute()
        self.create_parent = create_parent

    def _ensure_application_parent(self) -> None:
        """Explicit host opt-in for first-run local storage, never follow links.

        Existing ancestors are not chmodded. The final application parent must
        be private and owned; callers must supply an application path, not input.
        """
        parent = self.root.parent
        fd = os.open(parent.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for name in parent.parts[1:]:
                try:
                    child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                except FileNotFoundError:
                    try:
                        os.mkdir(name, 0o700, dir_fd=fd)
                        os.fsync(fd)
                    except FileExistsError:
                        pass
                    child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = child
            info = os.fstat(fd)
            if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
                raise ValueError('Draft application parent must be owned and private (0700)')
        finally:
            os.close(fd)

    @contextmanager
    def _directory(self, create: bool = False) -> Iterator[int]:
        if os.name != 'posix':
            raise ValueError('Private child draft storage requires POSIX ownership primitives; no fallback')
        if create and self.create_parent:
            self._ensure_application_parent()
        if create and not self.root.exists():
            # Host supplies an existing private application directory; do not
            # recursively create arbitrary parents or weaken their permissions.
            parent_fd = os.open(self.root.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                try:
                    os.mkdir(self.root.name, mode=0o700, dir_fd=parent_fd)
                except FileExistsError:
                    pass
                os.fsync(parent_fd)
            finally:
                os.close(parent_fd)
        import fcntl  # POSIX-only, after unsupported-platform refusal.
        fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            info = os.fstat(fd)
            if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
                raise ValueError('Child draft storage must be owned and private (0700)')
            # Serialize cooperating snapshot writers/readers so concurrent saves
            # cannot bypass the catalog bound or race an explicitly reviewed delete.
            deadline = time.monotonic() + LOCK_TIMEOUT
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise ValueError('Saved child draft storage busy; lock deadline exceeded, '
                                         'no operation admitted')
                    time.sleep(min(0.01, remaining))
            try:
                yield fd
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    @staticmethod
    def _read(fd: int, key: str) -> bytes:
        if _KEY.fullmatch(key) is None:
            raise ValueError('Invalid saved child draft identity')
        file_fd = os.open(key + '.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(file_fd, 'rb') as source:
            info = os.fstat(source.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink not in {1, 2}
                    or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > MAX_DRAFT_BYTES):
                raise ValueError('Saved child draft file ownership/type/mode/size unsafe')
            data = source.read(MAX_DRAFT_BYTES + 1)
            if len(data) > MAX_DRAFT_BYTES:
                raise ValueError('Saved child draft exceeds read capacity')
            if info.st_nlink == 2:
                # SIGKILL can land after exclusive link publication but before
                # unlinking its private temporary alias. Recognize only this exact
                # same-directory inode pair, never an arbitrary/external hardlink.
                # Loading stays read-only: do not delete even this private alias.
                aliases = []
                for name in os.listdir(fd):
                    if not re.fullmatch(r'\.draft-[a-f0-9]{32}', name):
                        continue
                    candidate = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    if candidate.st_dev == info.st_dev and candidate.st_ino == info.st_ino:
                        aliases.append(name)
                if len(aliases) != 1:
                    raise ValueError('Saved child draft has unsafe hardlink ownership')
                decode_draft(key, data)  # Validate complete typed bytes before accepting an orphan alias.
                current = os.stat(aliases[0], dir_fd=fd, follow_symlinks=False)
                if (current.st_dev != info.st_dev or current.st_ino != info.st_ino
                        or current.st_nlink != 2 or stat.S_IMODE(current.st_mode) != 0o600):
                    raise ValueError('Saved child draft temporary alias changed; no recovery')
                if os.fstat(source.fileno()).st_nlink != 2:
                    raise ValueError('Saved child draft link ownership changed during read')
            return data

    def save(self, data: bytes, *, alive: Callable[[], bool] = lambda: True) -> SavedChildDraft:
        key = uuid.uuid4().hex
        saved = decode_draft(key, data)
        with self._directory(create=True) as fd:
            if sum(_KEY.fullmatch(name.removesuffix('.json')) is not None
                   for name in os.listdir(fd) if name.endswith('.json')) >= MAX_DRAFTS:
                raise DraftStoreFullError('Saved child draft catalog full; explicitly discard old snapshots')
            temporary = '.draft-' + uuid.uuid4().hex
            file_fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
            try:
                with os.fdopen(file_fd, 'wb') as target:
                    target.write(data)
                    target.flush()
                    os.fsync(target.fileno())
                # Validate live host consent immediately before exclusive publication.
                if not alive():
                    raise ValueError('Saved draft save owner revoked before publication; no replay')
                # Exclusive publication: never overwrite existing snapshot/link.
                os.link(temporary, key + '.json', src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
                os.unlink(temporary, dir_fd=fd)
                os.fsync(fd)
            finally:
                try:
                    os.unlink(temporary, dir_fd=fd)
                except FileNotFoundError:
                    pass
        return saved

    def update(self, key: str, sha256: str, data: bytes) -> SavedChildDraft:
        """Replace only an autosave owner's exact previous snapshot, never arbitrary saved data."""
        saved = decode_draft(key, data)
        with self._directory() as fd:
            previous = self._read(fd, key)
            if hashlib.sha256(previous).hexdigest() != sha256:
                raise ValueError('Autosaved draft changed externally; no overwrite or retry')
            temporary = '.draft-' + uuid.uuid4().hex
            file_fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
            try:
                with os.fdopen(file_fd, 'wb') as target:
                    target.write(data)
                    target.flush()
                    os.fsync(target.fileno())
                os.replace(temporary, key + '.json', src_dir_fd=fd, dst_dir_fd=fd)
                os.fsync(fd)
            finally:
                try:
                    os.unlink(temporary, dir_fd=fd)
                except FileNotFoundError:
                    pass
        return saved

    def load(self, key: str) -> SavedChildDraft:
        with self._directory() as fd:
            return decode_draft(key, self._read(fd, key))

    def list(self) -> tuple[SavedChildDraft, ...]:
        if self.root.is_symlink():
            raise ValueError('Child draft storage must not be a symbolic link')
        if not self.root.exists():
            return ()
        with self._directory() as fd:
            names = sorted(name[:-5] for name in os.listdir(fd)
                           if name.endswith('.json') and _KEY.fullmatch(name[:-5]))
            if len(names) > MAX_DRAFTS:
                raise ValueError('Saved child draft catalog exceeds capacity')
            records = []
            size = 0
            for key in names:
                saved = decode_draft(key, self._read(fd, key))
                size += sum(len(image.data) for image in saved.draft.images)
                if size > MAX_RETAINED_BYTES:
                    raise ValueError('Saved catalog images exceed local load capacity; no truncated recovery')
                records.append(saved)
            return tuple(records)

    def catalog(self) -> tuple[dict[str, Any], ...]:
        """Validate one bounded record at a time; retain metadata, never catalog image bodies."""
        if self.root.is_symlink():
            raise ValueError('Child draft storage must not be a symbolic link')
        if not self.root.exists():
            return ()
        with self._directory() as fd:
            names = sorted(name[:-5] for name in os.listdir(fd)
                           if name.endswith('.json') and _KEY.fullmatch(name[:-5]))
            if len(names) > MAX_DRAFTS:
                raise ValueError('Saved child draft catalog exceeds capacity')
            rows = []
            for key in names:
                record = decode_draft(key, self._read(fd, key))
                rows.append({'id': record.id, 'sha256': record.sha256, 'label': record.label,
                             'source_scope': record.source_scope, 'source_action': record.source_action,
                             'image_count': len(record.draft.images),
                             'image_bytes': sum(len(image.data) for image in record.draft.images)})
                del record  # Do not keep previously decoded binary records across iterations.
            return tuple(rows)

    def discard(self, key: str, sha256: str, *, alive: Callable[[], bool] = lambda: True) -> None:
        with self._directory() as fd:
            data = self._read(fd, key)
            if hashlib.sha256(data).hexdigest() != sha256:
                raise ValueError('Saved child draft changed since review; no discard or replay')
            if not alive():
                raise ValueError('Saved draft discard owner revoked before removal; no replay')
            info = os.stat(key + '.json', dir_fd=fd, follow_symlinks=False)
            if info.st_nlink == 2:
                # _read accepted only a validated private publication alias.
                # Delete it ONLY as part of this explicitly reviewed discard;
                # ordinary load/catalog remain non-mutating.
                aliases = [name for name in os.listdir(fd) if re.fullmatch(r'\.draft-[a-f0-9]{32}', name)
                           and (candidate := os.stat(name, dir_fd=fd, follow_symlinks=False)).st_dev == info.st_dev
                           and candidate.st_ino == info.st_ino]
                if len(aliases) != 1:
                    raise ValueError('Saved draft publication alias changed; no discard')
                os.unlink(aliases[0], dir_fd=fd)
                os.fsync(fd)
            os.unlink(key + '.json', dir_fd=fd)
            os.fsync(fd)
