"""Private native desktop service, not a mocked keyring or a user vault."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from xdog.agent.mcp import McpManager
from xdog.agent.mcp.oauth import OAuthError
from xdog.agent.mcp.oauth_native_store import NativeCredentialStore


def available():
    if not sys.platform.startswith('linux') or not all(shutil.which(name) for name in
            ('dbus-run-session', 'gnome-keyring-daemon', 'gdbus')) or not Path('/usr/bin/python3').is_file():
        return False
    probe = subprocess.run(['/usr/bin/python3', '-I', '-c',
        "import gi; gi.require_version('Secret', '1'); from gi.repository import Secret"],
        capture_output=True, timeout=3)
    return probe.returncode == 0


@pytest.mark.skipif(not available(), reason='Owned Linux GI/Secret Service test prerequisites unavailable')
def test_actual_native_vault_worker_and_manager_consent_without_prompt_user_service_or_network(tmp_path):
    for name in ('home', 'data', 'runtime', 'control', 'config', 'cache'):
        (tmp_path / name).mkdir(mode=0o700)
    environment = {key: value for key, value in os.environ.items() if key not in {
        'DISPLAY', 'WAYLAND_DISPLAY', 'DBUS_SESSION_BUS_ADDRESS', 'DBUS_STARTER_ADDRESS', 'DBUS_STARTER_BUS_TYPE',
        'PYTHONPATH', 'PYTHONHOME'}}
    environment.update(HOME=str(tmp_path / 'home'), XDG_DATA_HOME=str(tmp_path / 'data'),
        XDG_CONFIG_HOME=str(tmp_path / 'config'), XDG_CACHE_HOME=str(tmp_path / 'cache'),
        XDG_RUNTIME_DIR=str(tmp_path / 'runtime'), NO_AT_BRIDGE='1', GIO_USE_VFS='local',
        XDOG_NATIVE_TEST_ROOT=str(tmp_path))
    script = Path(__file__).parent / 'fixtures/native_store/service.py'
    result = subprocess.run(['dbus-run-session', '--', '/usr/bin/python3', '-I', str(script), sys.executable],
                            env=environment, capture_output=True, text=True, timeout=40)
    assert result.returncode == 0, result.stderr + result.stdout
    records = [json.loads(line) for line in result.stdout.splitlines()]
    assert records[0]['locked_refusal'] and records[0]['review_restore_forget'] and records[0]['both_native_ui']
    assert records[1]['no_prompt_service'] and records[1]['dummy_plaintext_absent']
    assert 'PRIVATE-NATIVE' not in result.stdout + result.stderr


@pytest.mark.parametrize('value', ['unknown', True, None, 1, []])
def test_unknown_backend_is_refusal_not_plaintext_fallback(tmp_path, value):
    with pytest.raises(OAuthError, match='no fallback'):
        McpManager({}, tmp_path, credential_dir=tmp_path / 'private', credential_backend=value)
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
