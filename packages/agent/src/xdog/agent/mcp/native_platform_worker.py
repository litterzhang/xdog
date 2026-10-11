"""Isolated Keychain/WinCred native calls. Stdin only, no shell, login or file fallback.

This helper intentionally has no XDOG imports, so native libraries load only in
an explicitly selected owned child. Native platform execution is separately audited.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import sys
import uuid
from typing import Any, cast

LIMIT = 64 * 1024
CHUNK = 2048  # CREDENTIALW blob limit is 2560; preserve bounded full credential records.


class WindowsVault:
    def __init__(self, binding: str) -> None:
        from ctypes import wintypes as w
        class FileTime(ctypes.Structure):
            _fields_ = [('low', ctypes.c_uint32), ('high', ctypes.c_uint32)]
        class Credential(ctypes.Structure):
            _fields_ = [('Flags', ctypes.c_uint32), ('Type', ctypes.c_uint32), ('TargetName', w.LPWSTR),
                        ('Comment', w.LPWSTR), ('LastWritten', FileTime), ('CredentialBlobSize', ctypes.c_uint32),
                        ('CredentialBlob', ctypes.POINTER(ctypes.c_ubyte)), ('Persist', ctypes.c_uint32),
                        ('AttributeCount', ctypes.c_uint32), ('Attributes', ctypes.c_void_p),
                        ('TargetAlias', w.LPWSTR), ('UserName', w.LPWSTR)]
        self.credential = Credential
        self.prefix = 'XDOG/MCP/' + binding
        # Explicit system DLL name, WinDLL secure system32 loading flag.
        self.api = ctypes.WinDLL('advapi32.dll', use_last_error=True, winmode=0x800)  # type: ignore[attr-defined]
        self.api.CredReadW.argtypes = [w.LPCWSTR, ctypes.c_uint32, ctypes.c_uint32,
                                      ctypes.POINTER(ctypes.POINTER(Credential))]
        self.api.CredReadW.restype = ctypes.c_int32
        self.api.CredWriteW.argtypes = [ctypes.POINTER(Credential), ctypes.c_uint32]
        self.api.CredWriteW.restype = ctypes.c_int32
        self.api.CredDeleteW.argtypes = [w.LPCWSTR, ctypes.c_uint32, ctypes.c_uint32]
        self.api.CredDeleteW.restype = ctypes.c_int32
        self.api.CredFree.argtypes = [ctypes.c_void_p]
        self.api.CredFree.restype = None
        self.kernel = ctypes.WinDLL('kernel32.dll', use_last_error=True, winmode=0x800)  # type: ignore[attr-defined]
        self.kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int32, w.LPCWSTR]
        self.kernel.CreateMutexW.restype = ctypes.c_void_p
        self.kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        self.kernel.WaitForSingleObject.restype = ctypes.c_uint32
        self.kernel.ReleaseMutex.argtypes = [ctypes.c_void_p]
        self.kernel.ReleaseMutex.restype = ctypes.c_int32
        self.kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        self.kernel.CloseHandle.restype = ctypes.c_int32
        name = 'Local\\XDOG-MCP-' + hashlib.sha256(binding.encode()).hexdigest()
        self.mutex = self.kernel.CreateMutexW(None, False, name)
        if not self.mutex:
            raise ValueError('Native vault mutex unavailable')
        if self.kernel.WaitForSingleObject(self.mutex, 3000) not in (0, 0x80):
            self.kernel.CloseHandle(self.mutex)
            self.mutex = None
            raise ValueError('Native vault binding busy; no admission')

    def get(self, key: str) -> bytes | None:
        pointer = ctypes.POINTER(self.credential)()
        if not self.api.CredReadW(key, 1, 0, ctypes.byref(pointer)):
            if ctypes.get_last_error() == 1168:  # type: ignore[attr-defined]  # ERROR_NOT_FOUND only.
                return None
            raise ValueError('Native credential read refused')
        try:
            row = pointer.contents
            if (row.Type != 1 or row.TargetName != key or row.Persist != 2
                    or row.CredentialBlobSize > 2560 or (row.CredentialBlobSize and not row.CredentialBlob)):
                raise ValueError('Invalid native credential')
            return ctypes.string_at(row.CredentialBlob, row.CredentialBlobSize)
        finally:
            self.api.CredFree(pointer)

    def put(self, key: str, payload: bytes) -> None:
        if not payload or len(payload) > 2560:
            raise ValueError('Native credential blob exceeds capacity')
        buffer = (ctypes.c_ubyte * len(payload)).from_buffer_copy(payload)
        row = self.credential(Type=1, TargetName=key, Comment='XDOG reviewed MCP credential',
                              CredentialBlobSize=len(payload), CredentialBlob=buffer,
                              Persist=2, UserName='XDOG')  # CRED_PERSIST_LOCAL_MACHINE, current user vault.
        try:
            if not self.api.CredWriteW(ctypes.byref(row), 0):
                raise ValueError('Native credential write refused')
        finally:
            ctypes.memset(buffer, 0, len(payload))

    def delete(self, key: str) -> None:
        if not self.api.CredDeleteW(key, 1, 0) and ctypes.get_last_error() != 1168:  # type: ignore[attr-defined]
            raise ValueError('Native credential delete refused')

    def close(self) -> None:
        if self.mutex:
            self.kernel.ReleaseMutex(self.mutex)
            self.kernel.CloseHandle(self.mutex)
            self.mutex = None


class ChunkedCredential:
    """Immutable staged WinCred chunks; manifest publication commits a whole record.

    Missing/corrupt chunks are refusal, never partial credential restoration.
    Crash-created orphan chunks are inert; no unreviewed vault sweeping.
    """
    def __init__(self, vault: Any) -> None:
        self.vault = vault
        self.key = cast(str, vault.prefix)

    def manifest(self) -> dict[str, Any] | None:
        body = self.vault.get(self.key)
        if body is None:
            return None
        value = json.loads(body)
        if (not isinstance(value, dict) or set(value) != {'version', 'generation', 'bytes', 'sha256', 'chunks'}
                or type(value['version']) is not int or value['version'] != 1
                or not isinstance(value['generation'], str) or len(value['generation']) != 32
                or any(c not in '0123456789abcdef' for c in value['generation'])
                or type(value['bytes']) is not int or not 0 < value['bytes'] <= LIMIT
                or type(value['chunks']) is not int or value['chunks'] != (value['bytes'] + CHUNK - 1) // CHUNK
                or not isinstance(value['sha256'], str) or len(value['sha256']) != 64
                or any(c not in '0123456789abcdef' for c in value['sha256'])):
            raise ValueError('Invalid native manifest')
        return value

    def chunk_key(self, generation: str, index: int) -> str:
        return self.key + '/' + generation + '/' + str(index)

    def read(self) -> bytes | None:
        manifest = self.manifest()
        if manifest is None:
            return None
        blocks = []
        for index in range(manifest['chunks']):
            block = self.vault.get(self.chunk_key(manifest['generation'], index))
            expected = min(CHUNK, manifest['bytes'] - index * CHUNK)
            if not isinstance(block, bytes) or len(block) != expected:
                raise ValueError('Native chunk missing/unsafe')
            blocks.append(block)
        body = b''.join(blocks)
        if hashlib.sha256(body).hexdigest() != manifest['sha256'] or self.manifest() != manifest:
            raise ValueError('Native credential changed/corrupt during read')
        return body

    def save(self, body: bytes) -> None:
        if not body or len(body) > LIMIT:
            raise ValueError('Native record exceeds capacity')
        previous = self.manifest()
        generation = uuid.uuid4().hex
        staged = []
        published = False
        try:
            for index, offset in enumerate(range(0, len(body), CHUNK)):
                key = self.chunk_key(generation, index)
                # Include a potentially committed failure in owned cleanup.
                staged.append(key)
                self.vault.put(key, body[offset:offset + CHUNK])
            manifest = {'version': 1, 'generation': generation, 'bytes': len(body),
                        'sha256': hashlib.sha256(body).hexdigest(), 'chunks': len(staged)}
            if self.manifest() != previous:
                raise ValueError('Native credential changed before manifest publication')
            # Set before write: a failed native call can have uncertain effects.
            published = True
            self.vault.put(self.key, json.dumps(manifest, sort_keys=True).encode())
            if previous:
                for index in range(previous['chunks']):
                    self.vault.delete(self.chunk_key(previous['generation'], index))
        finally:
            if not published:
                for key in staged:
                    self.vault.delete(key)

    def forget(self) -> None:
        manifest = self.manifest()
        if manifest:
            # Revoke admission first. Leftover chunks cannot restore by themselves.
            self.vault.delete(self.key)
            for index in range(manifest['chunks']):
                self.vault.delete(self.chunk_key(manifest['generation'], index))


class MacVault:
    """Security.framework generic password, noninteractive SecItem operations."""
    def __init__(self, binding: str) -> None:
        self.binding = binding
        self.cf = ctypes.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        self.sec = ctypes.CDLL('/System/Library/Frameworks/Security.framework/Security')
        ptr = ctypes.c_void_p
        self.cf.CFStringCreateWithBytes.argtypes = [ptr, ptr, ctypes.c_long, ctypes.c_uint32, ctypes.c_ubyte]
        self.cf.CFStringCreateWithBytes.restype = ptr
        self.cf.CFDataCreate.argtypes = [ptr, ptr, ctypes.c_long]
        self.cf.CFDataCreate.restype = ptr
        self.cf.CFDictionaryCreate.argtypes = [ptr, ptr, ptr, ctypes.c_long, ptr, ptr]
        self.cf.CFDictionaryCreate.restype = ptr
        self.cf.CFDataGetLength.argtypes = [ptr]
        self.cf.CFDataGetLength.restype = ctypes.c_long
        self.cf.CFDataGetBytePtr.argtypes = [ptr]
        self.cf.CFDataGetBytePtr.restype = ptr
        self.cf.CFGetTypeID.argtypes = [ptr]
        self.cf.CFGetTypeID.restype = ctypes.c_ulong
        self.cf.CFDataGetTypeID.restype = ctypes.c_ulong
        self.cf.CFRelease.argtypes = [ptr]
        self.cf.CFRelease.restype = None
        for name in ('SecItemCopyMatching', 'SecItemAdd'):
            method = getattr(self.sec, name)
            method.argtypes = [ptr, ctypes.POINTER(ptr)]
            method.restype = ctypes.c_int32
        self.sec.SecItemUpdate.argtypes = [ptr, ptr]
        self.sec.SecItemUpdate.restype = ctypes.c_int32
        self.sec.SecItemDelete.argtypes = [ptr]
        self.sec.SecItemDelete.restype = ctypes.c_int32
        self.sec.SecKeychainSetUserInteractionAllowed.argtypes = [ctypes.c_ubyte]
        self.sec.SecKeychainSetUserInteractionAllowed.restype = ctypes.c_int32
        if self.sec.SecKeychainSetUserInteractionAllowed(False) != 0:
            raise ValueError('Native keychain noninteractive mode unavailable')
        self.refs: list[int] = []

    def constant(self, name: str, *, cf: bool = False) -> Any:
        return ctypes.c_void_p.in_dll(self.cf if cf else self.sec, name).value

    def data(self, body: bytes, *, string: bool = False) -> Any:
        buffer = ctypes.create_string_buffer(body)
        pointer = ctypes.cast(buffer, ctypes.c_void_p)
        result = (self.cf.CFStringCreateWithBytes(None, pointer, len(body), 0x08000100, False) if string else
                  self.cf.CFDataCreate(None, pointer, len(body)))
        if not result:
            raise ValueError('Native allocation refused')
        self.refs.append(result)
        return result

    def dictionary(self, pairs: dict[str, Any]) -> Any:
        keys = (ctypes.c_void_p * len(pairs))(*(self.constant(key) for key in pairs))
        values = (ctypes.c_void_p * len(pairs))(*pairs.values())
        # All referenced values stay owned until close; no callback struct ABI copies.
        result = self.cf.CFDictionaryCreate(None, keys, values, len(pairs), None, None)
        if not result:
            raise ValueError('Native dictionary refused')
        self.refs.append(result)
        return result

    def query(self, *, read: bool = False) -> Any:
        pairs = {'kSecClass': self.constant('kSecClassGenericPassword'),
                 'kSecAttrService': self.data(b'org.xdog.McpCredentials', string=True),
                 'kSecAttrAccount': self.data(self.binding.encode(), string=True),
                 'kSecUseAuthenticationUI': self.constant('kSecUseAuthenticationUIFail')}
        if read:
            pairs.update(kSecReturnData=self.constant('kCFBooleanTrue', cf=True),
                         kSecMatchLimit=self.constant('kSecMatchLimitOne'))
        return self.dictionary(pairs)

    def read(self) -> bytes | None:
        result = ctypes.c_void_p()
        status = self.sec.SecItemCopyMatching(self.query(read=True), ctypes.byref(result))
        if status == -25300:  # errSecItemNotFound only, not locked/denied.
            return None
        if status != 0 or not result.value:
            raise ValueError('Native keychain unavailable/locked/denied')
        try:
            if self.cf.CFGetTypeID(result) != self.cf.CFDataGetTypeID():
                raise ValueError('Native keychain result type unsafe')
            size = self.cf.CFDataGetLength(result)
            if not 0 < size <= LIMIT:
                raise ValueError('Native keychain record exceeds capacity')
            return ctypes.string_at(self.cf.CFDataGetBytePtr(result), size)
        finally:
            self.cf.CFRelease(result)

    def save(self, body: bytes) -> None:
        if not body or len(body) > LIMIT:
            raise ValueError('Native keychain record exceeds capacity')
        data = self.data(body)
        status = self.sec.SecItemUpdate(self.query(), self.dictionary({'kSecValueData': data}))
        if status == -25300:
            pairs = {'kSecClass': self.constant('kSecClassGenericPassword'),
                     'kSecAttrService': self.data(b'org.xdog.McpCredentials', string=True),
                     'kSecAttrAccount': self.data(self.binding.encode(), string=True),
                     'kSecUseAuthenticationUI': self.constant('kSecUseAuthenticationUIFail'), 'kSecValueData': data}
            status = self.sec.SecItemAdd(self.dictionary(pairs), None)
        if status != 0:
            raise ValueError('Native keychain write refused; no unlock/UI')

    def forget(self) -> None:
        if self.sec.SecItemDelete(self.query()) not in (0, -25300):
            raise ValueError('Native keychain delete refused')

    def close(self) -> None:
        for ref in reversed(self.refs):
            self.cf.CFRelease(ref)
        self.refs.clear()


def main() -> None:
    raw = sys.stdin.buffer.readline(256 * 1024 + 1)
    if len(raw) > 256 * 1024 or not raw.endswith(b'\n'):
        raise ValueError('Invalid bounded request')
    request = json.loads(raw)
    if (not isinstance(request, dict) or set(request) != {'action', 'attributes', 'payload'}
            or request['action'] not in {'read', 'save', 'forget'} or not isinstance(request['payload'], str)):
        raise ValueError('Invalid native request')
    attrs = request['attributes']
    if (not isinstance(attrs, dict) or set(attrs) != {'xdg:schema', 'xdog.namespace', 'xdog.resource'}
            or attrs['xdg:schema'] != 'org.xdog.McpCredentials'
            or any(not isinstance(attrs[key], str) or len(attrs[key]) != 64
                   or any(c not in '0123456789abcdef' for c in attrs[key])
                   for key in ('xdog.namespace', 'xdog.resource'))):
        raise ValueError('Invalid native binding')
    binding = attrs['xdog.namespace'] + '/' + attrs['xdog.resource']
    vault: Any
    store: Any
    if sys.platform == 'darwin':
        vault = MacVault(binding)
        store = vault
    elif sys.platform == 'win32':
        vault = WindowsVault(binding)
        store = ChunkedCredential(vault)
    else:
        raise ValueError('Native platform unavailable; no fallback')
    try:
        action = request['action']
        if action == 'read':
            payload = store.read()
            if payload is None:
                raise ValueError('No saved credential')
            print(json.dumps({'ok': True, 'payload': payload.decode('utf-8')}, ensure_ascii=True), flush=True)
            return
        # Preflight readable existing record; never unlock/create a login session.
        store.read()
        payload = request['payload'].encode()
        if action == 'save' and not 0 < len(payload) <= LIMIT:
            raise ValueError('Invalid native record size')
        print(json.dumps({'ready': True}), flush=True)
        commit = sys.stdin.buffer.readline(64)
        if commit != b'{"commit":true}\n':
            raise ValueError('Native admission revoked')
        if action == 'save':
            store.save(payload)
        else:
            store.forget()
        print(json.dumps({'ok': True}), flush=True)
    finally:
        vault.close()


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print(json.dumps({'ok': False, 'error': 'Native storage unavailable/locked/unsafe; no fallback'}), flush=True)
        sys.exit(1)
