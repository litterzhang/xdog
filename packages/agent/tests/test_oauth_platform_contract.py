"""Native backend implementation contracts on Linux, not macOS/Windows native proof."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from xdog.agent.mcp import McpManager
from xdog.agent.mcp.native_platform_worker import CHUNK, ChunkedCredential
from xdog.agent.mcp.oauth import OAuthError
from xdog.agent.mcp.oauth_native_store import NativeCredentialStore, storage_backend


class Vault:
    prefix = 'XDOG/owned-contract'
    def __init__(self):
        self.rows = {}
        self.failure = None
        self.calls = []

    def get(self, key):
        return self.rows.get(key)

    def put(self, key, value):
        self.calls.append(('put', key))
        assert len(value) <= 2560
        if self.failure == 'chunk' and key != self.prefix:
            raise OSError('owned failure')
        self.rows[key] = value
        if self.failure == 'manifest' and key == self.prefix:
            raise OSError('uncertain manifest write')

    def delete(self, key):
        self.calls.append(('delete', key))
        self.rows.pop(key, None)


@pytest.mark.parametrize('size', [1, CHUNK, CHUNK + 1, 64 * 1024])
def test_windows_chunk_manifest_roundtrip_replace_forget_preserves_full_record(size):
    vault = Vault()
    store = ChunkedCredential(vault)
    body = bytes(range(256)) * (size // 256) + bytes(range(size % 256))
    assert store.read() is None
    store.save(body)
    assert store.read() == body
    old = set(vault.rows)
    store.save(b'UPDATED')
    assert store.read() == b'UPDATED'
    assert not any(key in vault.rows for key in old - {vault.prefix})
    store.forget()
    assert store.read() is None and not vault.rows


@pytest.mark.parametrize('fault', ['missing', 'digest', 'manifest', 'chunk-failure', 'uncertain-manifest'])
def test_windows_staged_failures_never_restore_partial_or_destroy_admitted_generation(fault):
    vault = Vault()
    store = ChunkedCredential(vault)
    store.save(b'OLD')
    if fault in {'chunk-failure', 'uncertain-manifest'}:
        vault.failure = 'chunk' if fault == 'chunk-failure' else 'manifest'
        with pytest.raises(OSError):
            store.save(b'NEW')
        assert store.read() == (b'OLD' if fault == 'chunk-failure' else b'NEW')
        return
    manifest = store.manifest()
    key = store.chunk_key(manifest['generation'], 0)
    if fault == 'missing':
        del vault.rows[key]
    elif fault == 'digest':
        vault.rows[key] = b'BAD'
    else:
        vault.rows[vault.prefix] = b'{"version":true}'
    with pytest.raises(ValueError):
        store.read()


@pytest.mark.parametrize('backend,platform', [('keychain', 'darwin'), ('windows-credential', 'win32')])
def test_explicit_platform_selection_namespace_and_public_notice_without_storage_start(tmp_path, monkeypatch,
                                                                                      backend, platform):
    import xdog.agent.mcp.oauth_native_store as module
    monkeypatch.setattr(module, 'sys', SimpleNamespace(platform=platform, executable=sys.executable))
    assert storage_backend(backend) == backend
    manager = McpManager({}, tmp_path, credential_dir=tmp_path / 'not-created', credential_backend=backend)
    store = manager.credential_store
    assert isinstance(store, NativeCredentialStore) and store.backend == backend
    assert not store.directory.exists()
    with pytest.raises(OAuthError, match='owner changed'):
        store.forget('https://resource.test/mcp', lambda: False)
    assert not store.directory.exists()


@pytest.mark.parametrize('backend', ['keychain', 'windows-credential'])
def test_noncurrent_platform_selection_refuses_not_file_fallback(tmp_path, backend):
    if sys.platform != 'linux':
        pytest.skip('Current Linux negative contract')
    with pytest.raises(OAuthError, match='no plaintext fallback'):
        McpManager({}, tmp_path, credential_dir=tmp_path / 'private', credential_backend=backend)
    assert not (tmp_path / 'private').exists()


@pytest.mark.parametrize('backend,platform', [('keychain', 'darwin')])
@pytest.mark.parametrize('phase', ['success', 'stall', 'pre-admission-cancel', 'post-admission-cancel', 'oversize'])
def test_actual_owned_helper_pipe_protocol_lifecycle_no_secret_argv_or_plaintext(tmp_path, monkeypatch,
                                                                              backend, platform, phase):
    import xdog.agent.mcp.oauth_native_store as module
    monkeypatch.setattr(module, 'sys', SimpleNamespace(platform=platform, executable=sys.executable))
    script = tmp_path / 'owned.py'
    marker = tmp_path / 'receipt'
    script.write_text('''import sys,json,time
from pathlib import Path
request=json.loads(sys.stdin.readline())
phase=sys.argv[2]; marker=Path(sys.argv[1])
if phase=='stall': time.sleep(30)
if phase=='oversize':
    print('x'*300000,flush=True);time.sleep(30)
print('{"ready":true}',flush=True)
if phase=='pre-admission-cancel':
    marker.write_text('ready');time.sleep(30)
commit=sys.stdin.readline()
assert commit=='{"commit":true}\\n'
if phase=='post-admission-cancel':
    marker.write_text('admitted');time.sleep(30)
assert request['payload']=='PRIVATE-PIPE-TOKEN'
print('{"ok":true}',flush=True)
''')
    launch = module.subprocess.Popen
    owned = []
    def popen(args, **kwargs):
        assert 'PRIVATE-PIPE-TOKEN' not in str(args) and not kwargs.get('shell')
        child = launch([sys.executable, '-I', str(script), str(marker), phase], **kwargs)
        owned.append(child)
        return child
    monkeypatch.setattr(module.subprocess, 'Popen', popen)
    monkeypatch.setattr(module, 'HELPER_TIMEOUT', 0.4)
    store = NativeCredentialStore(tmp_path / 'not-created', backend=backend)
    def alive():
        return not marker.exists()
    if phase == 'success':
        assert store._run('save', 'https://resource.test/mcp', alive, 'PRIVATE-PIPE-TOKEN') == {'ok': True}
    else:
        with pytest.raises(OAuthError) as error:
            store._run('save', 'https://resource.test/mcp', alive, 'PRIVATE-PIPE-TOKEN')
        assert 'PRIVATE-PIPE-TOKEN' not in str(error.value)
    assert all(child.poll() is not None and child.stdin.closed and child.stdout.closed for child in owned)
    assert not store.directory.exists()


def test_isolated_native_worker_linux_refusal_is_secret_free_not_native_pass():
    if sys.platform != 'linux':
        pytest.skip('Current Linux unsupported-worker contract')
    worker = Path(__file__).parents[1] / 'src/xdog/agent/mcp/native_platform_worker.py'
    attrs = {'xdg:schema': 'org.xdog.McpCredentials', 'xdog.namespace': 'a' * 64, 'xdog.resource': 'b' * 64}
    result = subprocess.run([sys.executable, '-I', str(worker)], input=json.dumps({
        'action': 'save', 'attributes': attrs, 'payload': 'PRIVATE-TOKEN'}).encode() + b'\n',
        capture_output=True, timeout=3)
    assert result.returncode == 1 and result.stderr == b''
    assert json.loads(result.stdout)['ok'] is False
    assert b'PRIVATE' not in result.stdout


@pytest.mark.parametrize('backend,platform', [('keychain', 'darwin'), ('windows-credential', 'win32')])
async def test_manager_platform_review_restart_restore_forget_and_foreign_lease(
    tmp_path, monkeypatch, backend, platform,
):
    import time

    import xdog.agent.mcp.oauth_native_store as module
    from xdog.agent.mcp.oauth import OAuthConfig, OAuthOwner, OAuthReview
    monkeypatch.setattr(module, 'sys', SimpleNamespace(platform=platform, executable=sys.executable))
    rows, operations = {}, []
    def run(store, action, resource, alive, payload=''):
        assert alive()
        key = store.namespace, resource
        operations.append(action)
        if action == 'save':
            rows[key] = payload
            return {'ok': True}
        if action == 'forget':
            rows.pop(key, None)
            return {'ok': True}
        if key not in rows:
            raise OAuthError('Native saved credential unavailable')
        return {'ok': True, 'payload': rows[key]}
    monkeypatch.setattr(NativeCredentialStore, '_run', run)
    resource = 'https://resource.test/mcp'
    config = {'peer': {'transport': 'http', 'url': resource,
                      'oauth': {'issuer': 'https://issuer.test', 'client_id': 'client', 'scopes': ['read']}}}
    manager = McpManager(config, tmp_path, credential_dir=tmp_path / 'private', credential_backend=backend)
    fresh = None
    try:
        owner = OAuthOwner(resource, OAuthConfig.parse(config['peer']['oauth']))
        owner._token, owner._refresh = 'PRIVATE-ACCESS', 'PRIVATE-REFRESH'
        owner._expires = time.monotonic() + 1000
        owner._grant = OAuthReview('a' * 32, 'https://issuer.test', resource, 'client', ('read',),
                                  'https://issuer.test/authorize', 'https://issuer.test/token')
        manager._oauth['peer'] = owner
        with pytest.raises(Exception):
            await manager.manage_auth('peer', 'credentials_save', 'unreviewed', lease='one')
        assert not operations
        review = await manager.manage_auth('peer', 'credentials_save_prepare', lease='one')
        assert review['storage_backend'] == backend and 'NATIVE' in review['storage_notice']
        assert 'PRIVATE-' not in json.dumps(review) and not operations
        with pytest.raises(Exception):
            await manager.manage_auth('peer', 'credentials_save', review['review_id'], lease='foreign')
        assert not operations
        review = await manager.manage_auth('peer', 'credentials_save_prepare', lease='one')
        await manager.manage_auth('peer', 'credentials_save', review['review_id'], lease='one')
        fresh = McpManager(config, tmp_path, credential_dir=tmp_path / 'private', credential_backend=backend)
        assert not fresh._oauth and not fresh._clients
        restored = await fresh.manage_auth('peer', 'credentials_restore_prepare', lease='two')
        assert fresh._oauth['peer'].credential() is None
        assert 'PRIVATE-' not in json.dumps(restored)
        await fresh.manage_auth('peer', 'credentials_restore', restored['review_id'], lease='two')
        assert fresh._oauth['peer'].credential() == 'PRIVATE-ACCESS' and not fresh._clients
        forgotten = await fresh.manage_auth('peer', 'credentials_forget_prepare', lease='two')
        await fresh.manage_auth('peer', 'credentials_forget', forgotten['review_id'], lease='two')
        assert not rows and not (tmp_path / 'private').exists()
        assert fresh._oauth['peer'].credential() == 'PRIVATE-ACCESS'  # Saved erase is not live revoke.
    finally:
        await manager.aclose()
        if fresh is not None:
            await fresh.aclose()


def test_windows_ctypes_native_calls_structure_bounds_free_and_mutex_contract(monkeypatch):
    import ctypes

    from xdog.agent.mcp.native_platform_worker import WindowsVault
    calls = []
    class Function:
        def __init__(self, name, callback):
            self.name, self.callback = name, callback
        def __call__(self, *args):
            calls.append(self.name)
            return self.callback(*args)
    api = SimpleNamespace()
    kept = []
    def read(key, kind, flags, output):
        assert key == vault.prefix and kind == 1 and flags == 0
        body = b'OWNED BLOB'
        buffer = (ctypes.c_ubyte * len(body)).from_buffer_copy(body)
        row = vault.credential(Type=1, TargetName=key, CredentialBlobSize=len(body),
                               CredentialBlob=buffer, Persist=2)
        kept.extend([row, buffer])
        ctypes.cast(output, ctypes.POINTER(ctypes.POINTER(vault.credential)))[0] = ctypes.pointer(row)
        return 1
    def write(pointer, flags):
        row = ctypes.cast(pointer, ctypes.POINTER(vault.credential)).contents
        assert row.Type == 1 and row.Persist == 2 and row.TargetName == vault.prefix and flags == 0
        assert ctypes.string_at(row.CredentialBlob, row.CredentialBlobSize) == b'NEW BLOB'
        return 1
    api.CredReadW = Function('read', read)
    api.CredWriteW = Function('write', write)
    api.CredDeleteW = Function('delete', lambda *args: 1)
    api.CredFree = Function('free', lambda *args: None)
    kernel = SimpleNamespace(CreateMutexW=Function('mutex', lambda *args: 123),
        WaitForSingleObject=Function('wait', lambda *args: 0),
        ReleaseMutex=Function('release', lambda *args: 1), CloseHandle=Function('close', lambda *args: 1))
    def win_dll(name, **kwargs):
        assert kwargs == {'use_last_error': True, 'winmode': 0x800}
        return api if name == 'advapi32.dll' else kernel
    monkeypatch.setattr(ctypes, 'WinDLL', win_dll, raising=False)
    vault = WindowsVault('a' * 64 + '/' + 'b' * 64)
    try:
        assert vault.credential.Flags.size == 4 and vault.credential.Type.size == 4
        assert vault.credential.Persist.size == 4
        assert vault.get(vault.prefix) == b'OWNED BLOB' and 'free' in calls
        vault.put(vault.prefix, b'NEW BLOB')
        with pytest.raises(ValueError, match='capacity'):
            vault.put(vault.prefix, bytes(2561))
        vault.delete(vault.prefix)
    finally:
        vault.close()
    assert calls[-2:] == ['release', 'close']


def test_mac_native_query_no_ui_type_bounds_release_and_delete_contract():
    import ctypes

    from xdog.agent.mcp.native_platform_worker import MacVault
    # Actual production methods through owned ABI functions; no native library
    # load occurs on Linux, no claim that Apple's native execution passed.
    vault = object.__new__(MacVault)
    vault.binding, vault.refs = 'owned-binding', []
    values, calls = {}, []
    def constant(name, **kwargs):
        return name
    def data(body, **kwargs):
        return body
    def dictionary(pairs):
        values[len(values) + 1] = pairs
        return len(values)
    vault.constant, vault.data, vault.dictionary = constant, data, dictionary
    body = ctypes.create_string_buffer(b'OWNED KEYCHAIN')
    def match(query, output):
        assert values[query]['kSecUseAuthenticationUI'] == 'kSecUseAuthenticationUIFail'
        assert values[query]['kSecReturnData'] == 'kCFBooleanTrue'
        ctypes.cast(output, ctypes.POINTER(ctypes.c_void_p))[0] = 77
        calls.append('read')
        return 0
    def update(query, changes):
        assert values[query]['kSecUseAuthenticationUI'] == 'kSecUseAuthenticationUIFail'
        assert values[changes]['kSecValueData'] == b'NEW'
        calls.append('update')
        return -25300
    def add(query, output):
        assert values[query]['kSecUseAuthenticationUI'] == 'kSecUseAuthenticationUIFail'
        assert values[query]['kSecValueData'] == b'NEW'
        calls.append('add')
        return 0
    vault.sec = SimpleNamespace(SecItemCopyMatching=match, SecItemUpdate=update, SecItemAdd=add,
                               SecItemDelete=lambda query: -25300)
    vault.cf = SimpleNamespace(CFGetTypeID=lambda value: 1, CFDataGetTypeID=lambda: 1,
        CFDataGetLength=lambda value: len(b'OWNED KEYCHAIN'),
        CFDataGetBytePtr=lambda value: ctypes.addressof(body), CFRelease=lambda value: calls.append('release'))
    assert vault.read() == b'OWNED KEYCHAIN' and calls == ['read', 'release']
    vault.save(b'NEW')
    assert calls[-2:] == ['update', 'add']
    vault.forget()
    vault.sec.SecItemDelete = lambda query: -25308  # Interaction denied, not not-found success.
    with pytest.raises(ValueError, match='delete refused'):
        vault.forget()
    vault.cf.CFDataGetLength = lambda value: 65537
    with pytest.raises(ValueError, match='capacity'):
        vault.read()
    assert calls[-1] == 'release'


def test_broken_helper_stdin_close_preserves_owned_cancellation(tmp_path, monkeypatch):
    """A deterministic pipe-close failure must not mask the ownership refusal."""
    import xdog.agent.mcp.oauth_native_store as module

    launch = subprocess.Popen
    owned = []

    class BrokenClose:
        def __init__(self, stream):
            self.stream = stream

        def __getattr__(self, name):
            return getattr(self.stream, name)

        def close(self):
            self.stream.close()
            raise BrokenPipeError('owned helper already exited')

    def popen(args, **kwargs):
        child = launch([sys.executable, '-I', '-c', 'import sys; sys.stdin.read()'], **kwargs)
        child.stdin = BrokenClose(child.stdin)
        owned.append(child)
        return child

    monkeypatch.setattr(module.subprocess, 'Popen', popen)
    monkeypatch.setattr(module, 'sys', SimpleNamespace(platform='darwin', executable=sys.executable))
    store = NativeCredentialStore(tmp_path / 'private', backend='keychain')
    with pytest.raises(OAuthError, match='no plaintext or login fallback'):
        store._run_platform('save', b'{}\n', lambda: False)
    assert all(child.poll() is not None and child.stdin.closed and child.stdout.closed for child in owned)
