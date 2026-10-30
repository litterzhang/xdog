"""Frozen publication images, not arbitrary backend paths or a second outbox."""
from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

MAX_IMAGE = 20 * 1024 * 1024


def _read(path: Path) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_IMAGE:
            raise ValueError('Publication resource must be a bounded regular image')
        value = stream.read(MAX_IMAGE + 1)
    if len(value) > MAX_IMAGE:
        raise ValueError('Publication resource exceeds capacity')
    if not (value.startswith(b'\x89PNG\r\n\x1a\n') or value.startswith(b'\xff\xd8\xff')
            or value.startswith(b'RIFF') and value[8:12] == b'WEBP'):
        raise ValueError('Publication resource is not a supported image')
    return value


def freeze_images(names: list[str], workspace: Path, spool: Path) -> list[dict[str, str]]:
    resources = []
    total = 0
    try:
        root = workspace.resolve(strict=True)
        spool.mkdir(parents=True, exist_ok=True)
        for name in names:
            if '://' in name:
                raise ValueError('Publication images must be workspace files, not URLs')
            source = Path(name)
            source = (source if source.is_absolute() else root / source).resolve(strict=True)
            if not source.is_relative_to(root) or source.suffix.lower() not in {'.png', '.jpg', '.jpeg', '.webp'}:
                raise ValueError('Publication image is outside workspace or has an unsupported extension')
            value = _read(source)
            total += len(value)
            if total > 40 * 1024 * 1024:
                raise ValueError('Publication images exceed aggregate capacity')
            digest = hashlib.sha256(value).hexdigest()
            destination = spool / (digest + source.suffix.lower())
            try:
                fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o400)
            except FileExistsError:
                if _read(destination) != value:
                    raise ValueError('Existing frozen publication resource changed') from None
            else:
                try:
                    with os.fdopen(fd, 'wb') as stream:
                        stream.write(value)
                        stream.flush()
                        os.fsync(stream.fileno())
                except BaseException:
                    destination.unlink(missing_ok=True)
                    raise
            resources.append({'path': str(destination.resolve()), 'sha256': digest})
    except OSError as exc:
        raise ValueError('Publication image could not be frozen from workspace') from exc
    return resources


def verified_paths(resources: list[dict[str, str]], spool: Path) -> list[str]:
    root = spool.resolve(strict=True)
    paths = []
    for item in resources:
        path = Path(item['path'])
        resolved = path.resolve(strict=True)
        if (path != resolved or not resolved.is_relative_to(root)
                or hashlib.sha256(_read(path)).hexdigest() != item['sha256']):
            raise ValueError('Frozen publication resource changed')
        paths.append(str(path))
    return paths
