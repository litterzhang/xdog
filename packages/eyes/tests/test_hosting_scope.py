"""Scope shape is bounded stdlib data, independently of a live host registry."""
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from xdog.eyes.hosting.journal import ObserverJournal
from xdog.eyes.hosting.models import validate_scope

from packages.eyes.tests.test_hosting import Host


def test_scope_shape_accepts_exact_byte_limits_without_filesystem_io():
    validate_scope('界' * 85 + 'a', Path('/' + '界' * 1365))
    validate_scope('a' * 256, Path('/' + 'a' * 4095))


@pytest.mark.parametrize('owner,workspace', [
    ('', Path('/valid')), (' \t', Path('/valid')), ('a\x00b', Path('/valid')),
    ('界' * 86, Path('/valid')), ('valid', Path('relative')), ('valid', Path('/a/../b')),
    ('valid', Path('/a\x00b')), ('valid', Path('/' + '界' * 1366)), ('valid', '/not-a-Path'),
])
async def test_create_rejects_bad_scope_shape_before_host_io(tmp_path, monkeypatch, owner, workspace):
    host = Host(tmp_path)
    journal = await ObserverJournal.open(tmp_path / 'journal.sqlite3', host)
    namespace = AsyncMock(side_effect=AssertionError('Scope shape must fail before host IO'))
    monkeypatch.setattr(host, 'input_namespace', namespace)
    try:
        with pytest.raises(ValueError, match='observer owner/workspace scope'):
            await journal.create(owner, workspace, 'invalid-scope', {}, reply=None)
        namespace.assert_not_called()
        assert await journal.list() == ()
    finally:
        await journal.close()
