"""Inert filesystem ceiling snapshots/codecs; no path resolution, IO or authority.

Historical ceilings constrain a fresh host admission. They never grant a tool,
restore a runtime/context or replace checking the actual live parent.
"""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

FilesystemRoots = tuple[Path, ...] | None


def capture_filesystem_roots(context: Mapping[str, Any]) -> FilesystemRoots:
    """Missing/None is unconfined; empty is deny-all. Never resolve symlinks."""
    roots = context.get('fs_confine_to')
    if roots is None:
        return None
    if not isinstance(roots, (list, tuple)):
        raise ValueError('Invalid filesystem execution constraints')
    if any(not isinstance(root, (str, Path)) or not str(root) or '\x00' in str(root) for root in roots):
        raise ValueError('Invalid filesystem execution constraint root')
    # absolute() only pins relative spelling to this process cwd; resolve() and
    # filesystem/symlink validation remain at the real filesystem executor.
    return tuple(Path(root).absolute() for root in roots)


def encode_filesystem_roots(roots: FilesystemRoots) -> dict[str, Any]:
    return {'version': 1, 'roots': None if roots is None else [str(root) for root in roots]}


def decode_filesystem_roots(value: object) -> FilesystemRoots:
    """Strict, versioned finite data. Unknown/malformed is never unconfined."""
    if (not isinstance(value, dict) or set(value) != {'version', 'roots'}
            or type(value['version']) is not int or value['version'] != 1):
        raise ValueError('Invalid historical filesystem constraints version/fields')
    roots = value['roots']
    if roots is None:
        return None
    if (not isinstance(roots, list)
            or any(not isinstance(root, str) or not root or '\x00' in root
                   or not Path(root).is_absolute() or str(Path(root)) != root for root in roots)):
        raise ValueError('Invalid historical filesystem constraint roots')
    return tuple(Path(root) for root in roots)
