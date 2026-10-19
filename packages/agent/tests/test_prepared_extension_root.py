"""Prepared extension paths reuse the same resolver without owner-loop filesystem work."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from xdog.agent.extensions.location import ExtensionRoot
from xdog.agent.extensions.runtime import ExtensionRuntime


async def test_prepared_root_is_adopted_without_a_second_resolution(tmp_path, monkeypatch):
    real = tmp_path / 'extensions'
    real.mkdir()
    alias = tmp_path / 'alias'
    alias.symlink_to(real, target_is_directory=True)
    prepared = await asyncio.to_thread(ExtensionRoot.read, alias)
    original = Path.resolve
    reads = []

    def observe(path, *args, **kwargs):
        if path == alias:
            reads.append(path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'resolve', observe)
    runtime = ExtensionRuntime((), alias, tmp_path, prepared_root=prepared)
    try:
        assert runtime.root == real and not reads
    finally:
        await runtime.aclose()


@pytest.mark.parametrize('invalid', ['different-request', 'relative-canonical'])
def test_prepared_root_cannot_bind_a_different_location(tmp_path, invalid):
    configured = tmp_path / 'configured'
    prepared = (ExtensionRoot(tmp_path / 'other', tmp_path / 'other') if invalid == 'different-request'
                else ExtensionRoot(configured, Path('relative')))
    with pytest.raises(ValueError, match='another configured location'):
        ExtensionRuntime((), configured, tmp_path, prepared_root=prepared)
