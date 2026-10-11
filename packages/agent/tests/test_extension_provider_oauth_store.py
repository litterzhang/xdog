from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from xdog.agent.extensions.provider_oauth_store import operate, storage_config
from xdog.agent.mcp.oauth_native_store import NativeCredentialStore


def available():
    if not sys.platform.startswith('linux') or not all(shutil.which(name) for name in
            ('dbus-run-session', 'gnome-keyring-daemon', 'gdbus')):
        return False
    result = subprocess.run(['/usr/bin/python3', '-I', '-c',
        "import gi; gi.require_version('Secret', '1'); from gi.repository import Secret"],
        capture_output=True, timeout=3)
    return result.returncode == 0


@pytest.mark.skipif(not available(), reason='Owned Linux GI/Secret Service prerequisites unavailable')
def test_actual_provider_private_worker_native_vault_restart_refresh_source_namespace_and_locked_refusal(tmp_path):
    for name in ('home', 'data', 'runtime', 'control', 'config', 'cache'):
        (tmp_path / name).mkdir(mode=0o700)
    environment = {key: value for key, value in os.environ.items() if key not in {
        'DISPLAY', 'WAYLAND_DISPLAY', 'DBUS_SESSION_BUS_ADDRESS', 'DBUS_STARTER_ADDRESS', 'DBUS_STARTER_BUS_TYPE',
        'PYTHONPATH', 'PYTHONHOME'}}
    environment.update(HOME=str(tmp_path / 'home'), XDG_DATA_HOME=str(tmp_path / 'data'),
        XDG_CONFIG_HOME=str(tmp_path / 'config'), XDG_CACHE_HOME=str(tmp_path / 'cache'),
        XDG_RUNTIME_DIR=str(tmp_path / 'runtime'), NO_AT_BRIDGE='1', GIO_USE_VFS='local',
        XDOG_NATIVE_TEST_ROOT=str(tmp_path),
        XDOG_PROVIDER_OAUTH_FIXTURE=str(Path(__file__).parent / 'fixtures/extensions/provideroauth'))
    script = Path(__file__).parent / 'fixtures/provider_oauth_native/service.py'
    result = subprocess.run(['dbus-run-session', '--', '/usr/bin/python3', '-I', str(script), sys.executable],
                            env=environment, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr + result.stdout
    records = [json.loads(line) for line in result.stdout.splitlines()]
    assert records[0]['provider_roundtrip'] and records[0]['private_restart_refresh']
    assert records[0]['source_namespace_binding'] and records[0]['locked_refused']
    assert records[1]['no_prompt_service'] and records[1]['dummy_plaintext_absent']
    assert 'PRIVATE-OAUTH-' not in result.stdout + result.stderr


@pytest.mark.parametrize('value', [None, {}, {'backend': 'file', 'namespace': '/tmp/test'},
    {'backend': 'secret-service', 'namespace': 'relative'}, {'backend': 'secret-service', 'namespace': '/tmp/test',
     'token': 'PRIVATE-OAUTH-SECRET'}])
def test_plaintext_or_malformed_storage_configuration_is_rejected(value):
    with pytest.raises(ValueError):
        storage_config(value)


async def test_native_storage_cancel_settles_owned_helper_thread_without_masking_cancellation(tmp_path, monkeypatch):
    entered, exited = threading.Event(), threading.Event()

    def run(self, action, resource, alive, payload=''):
        assert 'PRIVATE-OAUTH' not in resource
        entered.set()
        try:
            while alive():
                time.sleep(.005)
            raise ValueError('private helper canceled')
        finally:
            exited.set()

    monkeypatch.setattr(NativeCredentialStore, '_run', run)
    params = {'storage': {'backend': 'secret-service', 'namespace': str(tmp_path / 'not-created')},
              'binding': 'a' * 64}
    task = asyncio.create_task(operate('save', params,
        {'access': 'PRIVATE-OAUTH-ACCESS', 'refresh': 'PRIVATE-OAUTH-REFRESH', 'expires': 2**40}, lambda: True))
    async with asyncio.timeout(3):
        while not entered.is_set():
            await asyncio.sleep(.005)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert exited.is_set() and not (tmp_path / 'not-created').exists()


@pytest.mark.parametrize('payload', [
    '{"version":1,"version":1,"binding":"' + 'a' * 64 + '","credentials":{}}',
    json.dumps({'version': True, 'binding': 'a' * 64, 'credentials': {}}),
    json.dumps({'version': 1, 'binding': 'b' * 64, 'credentials': {}}),
    json.dumps({'version': 1, 'binding': 'a' * 64,
                'credentials': {'access': 'PRIVATE', 'refresh': '', 'expires': True}}),
])
async def test_native_record_binding_duplicate_keys_and_expiration_do_not_get_coerced(tmp_path, monkeypatch, payload):
    monkeypatch.setattr(NativeCredentialStore, '_run', lambda *args: {'payload': payload})
    params = {'storage': {'backend': 'secret-service', 'namespace': str(tmp_path / 'not-created')},
              'binding': 'a' * 64}
    with pytest.raises(ValueError):
        await operate('restore', params, None, lambda: True)
    assert not (tmp_path / 'not-created').exists()
