"""Removed credential backends fail before I/O; Linux choices never downgrade."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from xdog.agent.extensions.provider_oauth_store import storage_config
from xdog.agent.mcp import McpManager
from xdog.agent.mcp.oauth import OAuthError
from xdog.agent.mcp.oauth_native_store import NativeCredentialStore, storage_backend
from xdog.agent.mcp.oauth_store import CredentialStore


@pytest.mark.parametrize('backend', ['keychain', 'windows-credential'])
def test_removed_backend_rejected_before_factory_or_process_start(tmp_path, monkeypatch, backend):
    def forbidden(*args, **kwargs):
        pytest.fail('Removed backends must not perform credential or process I/O')

    monkeypatch.setattr(NativeCredentialStore, '_run', forbidden)
    monkeypatch.setattr('xdog.agent.mcp.oauth_native_store.subprocess.Popen', forbidden)
    with pytest.raises(OAuthError, match='no fallback'):
        storage_backend(backend)
    with pytest.raises(OAuthError, match='no fallback'):
        McpManager({}, tmp_path, credential_dir=tmp_path / 'private', credential_backend=backend)
    with pytest.raises(ValueError, match='storage'):
        storage_config({'backend': backend, 'namespace': str(tmp_path / 'private')})
    assert not (tmp_path / 'private').exists()


@pytest.mark.parametrize('backend', ['file', 'secret-service'])
def test_explicit_linux_storage_choices_remain_distinct_without_startup(tmp_path, monkeypatch, backend):
    def forbidden(*args, **kwargs):
        pytest.fail('Choosing storage must not start a service or read credentials')

    monkeypatch.setattr('xdog.agent.mcp.oauth_native_store.subprocess.Popen', forbidden)
    manager = McpManager({}, tmp_path, credential_dir=tmp_path / 'private', credential_backend=backend)
    expected = CredentialStore if backend == 'file' else NativeCredentialStore
    assert type(manager.credential_store) is expected
    assert storage_backend(backend) == backend
    assert not (tmp_path / 'private').exists()
    if backend == 'secret-service':
        assert storage_config({'backend': backend, 'namespace': str(tmp_path / 'private')})['backend'] == backend


def test_deleted_native_worker_has_no_source_or_import_surface():
    root = Path(__file__).parents[1] / 'src/xdog/agent/mcp'
    assert not (root / 'native_platform_worker.py').exists()
    assert importlib.util.find_spec('xdog.agent.mcp.native_platform_worker') is None
    assert not hasattr(NativeCredentialStore, '_run_platform')
