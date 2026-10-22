"""Private native desktop service, not a mocked keyring or a user vault."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from xdog.agent.mcp import McpRegistry
from xdog.agent.mcp.oauth import OAuthError
from xdog.agent.mcp.oauth_native_store import NativeCredentialStore


@pytest.mark.parametrize('value', ['unknown', True, None, 1, []])
def test_unknown_backend_is_refusal_not_plaintext_fallback(tmp_path, value):
    with pytest.raises(OAuthError, match='no fallback'):
        McpRegistry({}, tmp_path, credential_dir=tmp_path / 'private', credential_backend=value)
    assert not (tmp_path / 'private').exists()


def test_native_backend_never_initializes_or_contacts_storage_just_from_constructor(tmp_path):
    store = NativeCredentialStore(tmp_path / 'private')
    assert len(store.namespace) == 64 and not store.directory.exists()
    with pytest.raises(OAuthError, match='owner changed'):
        store.forget('https://resource.test/mcp', lambda: False)
    assert not store.directory.exists()


@pytest.mark.parametrize('phase', ['stalled', 'before-admission', 'after-admission'])
def test_owned_native_helper_cancellation_timeout_and_secret_input_never_use_argv_or_file_fallback(
    tmp_path, monkeypatch, phase,
):
    import time

    import xdog.agent.mcp.oauth_native_store as module

    helper = tmp_path / 'fake.py'
    marker = tmp_path / 'marker'
    if phase == 'stalled':
        body = 'time.sleep(60)'
    elif phase == 'before-admission':
        body = "print('{\"ready\":true}', flush=True); marker.write_text('ready'); time.sleep(60)"
    else:
        body = ("print('{\"ready\":true}', flush=True); sys.stdin.readline(); "
                "marker.write_text('admitted'); time.sleep(60)")
    helper.write_text("import json,sys,time\nfrom pathlib import Path\nmarker=Path(sys.argv[1])\n"
                      "value=json.loads(sys.stdin.readline())\n" + body + '\n')
    popen = module.subprocess.Popen
    owned = []
    def launch(args, **kwargs):
        assert all('PRIVATE-FAKE-TOKEN' not in str(arg) for arg in args)
        child = popen([sys.executable, '-I', str(helper), str(marker)], **kwargs)
        owned.append(child)
        return child
    monkeypatch.setattr(module.subprocess, 'Popen', launch)
    monkeypatch.setattr(module, 'HELPER_TIMEOUT', 0.25)
    store = NativeCredentialStore(tmp_path / 'not-created')
    def alive():
        return phase == 'stalled' or not marker.exists()
    start = time.monotonic()
    with pytest.raises(OAuthError):
        store._run('save', 'https://resource.test/mcp', alive, 'PRIVATE-FAKE-TOKEN')
    assert time.monotonic() - start < 2
    assert all(child.poll() is not None for child in owned)
    assert all(child.stdin.closed and child.stdout.closed for child in owned)
    assert not store.directory.exists()


def test_native_worker_missing_os_gi_refuses_without_service_network_or_plaintext_fallback():
    worker = Path(__file__).parents[1] / 'src/xdog/agent/mcp/native_store_worker.py'
    script = """import builtins,runpy,sys
original=builtins.__import__
def no_gi(name,*args,**kwargs):
    if name=='gi' or name.startswith('gi.'):
        raise ImportError('PRIVATE-DEPENDENCY-DIAGNOSTIC')
    return original(name,*args,**kwargs)
builtins.__import__=no_gi
runpy.run_path(sys.argv[1],run_name='__main__')
"""
    result = subprocess.run([sys.executable, '-I', '-c', script, str(worker)],
                            input=b'', capture_output=True, timeout=3)
    assert result.returncode == 1 and result.stderr == b''
    assert json.loads(result.stdout) == {
        'ok': False, 'error': 'Native storage unavailable/locked/unsafe; no fallback'}
    assert b'PRIVATE-DEPENDENCY-DIAGNOSTIC' not in result.stdout


@pytest.mark.parametrize('changes', [
    {'registration': 'dynamic'}, {'registration': 'metadata'},
    {'token_auth_method': 'client_secret_basic', 'client_secret_env': 'XDOG_SECRET'},
    {'token_auth_method': 'client_secret_post', 'client_secret_env': 'XDOG_SECRET'},
    {'token_auth_method': 'client_secret_jwt', 'client_secret_env': 'XDOG_SECRET'},
    {'token_auth_method': 'private_key_jwt', 'client_secret_env': 'XDOG_PRIVATE_KEY'},
    {'client_private_key_env': ''}, {'registration_endpoint': 'https://issuer.test/register'},
    {'unexpected_credential_field': 'PRIVATE'},
])
def test_native_save_revalidates_mutated_review_snapshot_before_helper_io(tmp_path, monkeypatch, changes):
    from xdog.agent.mcp.oauth_store import capture

    from packages.agent.tests.test_oauth_store import seeded

    saved = capture(seeded())
    saved.review.update(changes)
    store = NativeCredentialStore(tmp_path / 'not-created')

    def forbidden(*args, **kwargs):
        pytest.fail('Stale unsupported snapshot reached native helper I/O')

    monkeypatch.setattr(store, '_run', forbidden)
    with pytest.raises(OAuthError) as error:
        store.save(saved, lambda: True)
    assert 'PRIVATE' not in str(error.value) and not store.directory.exists()


def test_native_save_public_record_keeps_exact_payload_and_explicit_owner_guard(tmp_path, monkeypatch):
    from xdog.agent.mcp.oauth_store import capture, decode_credential

    from packages.agent.tests.test_oauth_store import seeded

    saved = capture(seeded())
    store = NativeCredentialStore(tmp_path / 'not-created')
    calls = []
    def alive():
        return True

    def run(action, resource, guard, payload):
        calls.append((action, resource, guard))
        assert decode_credential(payload.encode(), resource) == saved
        return {'ok': True}

    monkeypatch.setattr(store, '_run', run)
    store.save(saved, alive)
    assert calls == [('save', saved.resource, alive)] and not store.directory.exists()
