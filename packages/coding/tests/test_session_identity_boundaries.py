"""Persisted storage identity, not temporary placement, owns a writable session."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from xdog.ai.types import Model
from xdog.coding.core.sdk import CreateSessionOptions, create_agent_session
from xdog.coding.core.session_manager import SessionManager

from packages.coding.tests.test_sdk_session_state import _Provider

FACTORY_CHILD = r'''
import json, sys
from pathlib import Path
from unittest.mock import patch
from xdog.ai.types import Model
from xdog.coding.core.sdk import CreateSessionOptions, create_agent_session
class Provider:
    def models(self):
        return (Model(id='offline/model', context_window=100000),)
    def model(self, name):
        return self.models()[0] if name == 'offline/model' else None
    def stream(self, *_):
        raise AssertionError('No real model')
with patch('xdog.ai.load', lambda: Provider()):
    session = create_agent_session(CreateSessionOptions(working_dir=Path(sys.argv[1]), resume_id=sys.argv[2])).session
    try:
        print(json.dumps({'session_id': session.session_id, 'lease': str(session._lease.directory)}))
    finally:
        session.dispose()
'''


def test_actual_factory_exclusion_survives_different_process_tmpdir(tmp_path, monkeypatch):
    data = tmp_path / 'data'
    monkeypatch.setenv('CODING_DIR', str(data))
    monkeypatch.setattr('xdog.ai.load', lambda: _Provider(Model(id='offline/model', context_window=100_000)))
    owner = create_agent_session(CreateSessionOptions(working_dir=tmp_path)).session
    alternate = tmp_path / 'alternate-tmp'
    alternate.mkdir()
    environment = {**os.environ, 'TMPDIR': str(alternate)}
    try:
        denied = subprocess.run([sys.executable, '-c', FACTORY_CHILD, str(tmp_path), owner.session_id],
                                env=environment, capture_output=True, text=True, timeout=8)
        assert denied.returncode != 0 and 'already owned' in denied.stderr, denied.stdout + denied.stderr
        assert owner._lease.directory.is_relative_to(owner.session_manager._dir.resolve())
        identifier = owner.session_id
        owner.dispose()
        resumed = subprocess.run([sys.executable, '-c', FACTORY_CHILD, str(tmp_path), identifier],
                                 env=environment, capture_output=True, text=True, timeout=8)
        assert resumed.returncode == 0, resumed.stderr
        assert json.loads(resumed.stdout)['session_id'] == identifier
    finally:
        owner.dispose()


def test_discovery_namespace_is_independent_of_tmpdir_and_short_for_deep_store(tmp_path):
    from xdog.coding.core.sessions.ownership import OwnerRegistry, runtime_directory

    sessions = tmp_path / ('deep-' * 20) / ('nested-' * 15) / 'sessions'
    expected = runtime_directory(sessions)
    alternate = tmp_path / 'alternate-tmp'
    alternate.mkdir()
    code = ('from pathlib import Path; import sys; '
            'from xdog.coding.core.sessions.ownership import runtime_directory; '
            'print(runtime_directory(Path(sys.argv[1])))')
    child = subprocess.run([sys.executable, '-c', code, str(sessions)], env={**os.environ, 'TMPDIR': str(alternate)},
                           capture_output=True, text=True, timeout=8)
    assert child.returncode == 0, child.stderr
    assert Path(child.stdout.strip()) == expected
    assert len(str(OwnerRegistry(expected).socket_path('example')).encode()) <= 107


def test_actual_sdk_rejects_embedded_owner_mismatch_before_provider_or_other_owner_write(tmp_path, monkeypatch):
    monkeypatch.setenv('CODING_DIR', str(tmp_path / 'data'))
    provider = _Provider(Model(id='offline/model', context_window=100_000))
    monkeypatch.setattr('xdog.ai.load', lambda: provider)
    first = create_agent_session(CreateSessionOptions(working_dir=tmp_path)).session
    second = create_agent_session(CreateSessionOptions(working_dir=tmp_path)).session
    resumed = None
    try:
        identifier = first.session_id
        path = first.session_manager._session_path(identifier)
        first.dispose()
        value = json.loads(path.read_text())
        value['session_id'] = second.session_id
        path.write_text(json.dumps(value))
        paths = (path, second.session_manager._session_path(second.session_id))
        originals = tuple(file.read_bytes() for file in paths)

        def forbidden():
            pytest.fail('Malformed owner metadata reached provider bootstrap')

        monkeypatch.setattr('xdog.ai.load', forbidden)
        with pytest.raises(ValueError, match='identity|unavailable|invalid'):
            resumed = create_agent_session(CreateSessionOptions(working_dir=tmp_path, resume_id=identifier)).session
        assert tuple(file.read_bytes() for file in paths) == originals
        assert second._lease is not None and second._lease._fd >= 0
    finally:
        if resumed is not None:
            resumed.dispose()
        first.dispose()
        second.dispose()


@pytest.mark.parametrize('root', [[], None, 42, {'session_id': 'other'}])
def test_invalid_snapshot_is_not_absent_and_does_not_break_healthy_listing(tmp_path, root):
    manager = SessionManager(tmp_path)
    bad = manager.create_session(session_id='broken', working_dir=tmp_path)
    good = manager.create_session(session_id='healthy', working_dir=tmp_path)
    path = manager._session_path(bad.session_id)
    path.write_text(json.dumps(root))
    before = path.read_bytes()
    with pytest.raises(ValueError, match='identity|unavailable|invalid'):
        manager.load_session(bad.session_id)
    assert [row.session_id for row in manager.list_sessions()] == [good.session_id]
    assert path.read_bytes() == before
    assert manager.load_session('genuinely-missing') is None


@pytest.mark.parametrize('damage', ['malformed', 'directory', 'invalid-utf8'])
def test_unavailable_existing_snapshot_is_not_an_empty_or_missing_session(tmp_path, damage):
    manager = SessionManager(tmp_path)
    manager.create_session(session_id='existing', working_dir=tmp_path)
    path = manager._session_path('existing')
    if damage == 'directory':
        path.unlink()
        path.mkdir()
    else:
        path.write_bytes(b'{' if damage == 'malformed' else b'\xff')
    with pytest.raises(ValueError, match='unavailable|invalid'):
        manager.load_session('existing')
    assert manager.list_sessions() == []


@pytest.mark.parametrize('field,value', [('created_at', 'yesterday'), ('updated_at', float('nan')),
                                        ('working_dir', []), ('messages', ''), ('settings', [])])
def test_invalid_saved_metadata_cannot_poison_listing_or_become_defaults(tmp_path, field, value):
    manager = SessionManager(tmp_path)
    manager.create_session(session_id='broken', working_dir=tmp_path)
    good = manager.create_session(session_id='healthy', working_dir=tmp_path)
    path = manager._session_path('broken')
    raw = json.loads(path.read_text())
    raw[field] = value
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match='unavailable|invalid'):
        manager.load_session('broken')
    assert [row.session_id for row in manager.list_sessions()] == [good.session_id]
