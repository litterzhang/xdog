"""Explicit native Secret Service storage, with bounded owned helper and no plaintext fallback."""
from __future__ import annotations

import hashlib
import json
import os
import queue
import selectors
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from xdog.agent.mcp.oauth import OAuthError, endpoint
from xdog.agent.mcp.oauth_store import SavedCredential, decode_credential

HELPER_TIMEOUT = 10.0


def storage_backend(value: Any) -> str:
    if not isinstance(value, str) or value not in {'file', 'secret-service', 'keychain', 'windows-credential'}:
        raise OAuthError('Unknown credential backend; select file, secret-service, keychain or '
                         'windows-credential explicitly, no fallback')
    return value


class NativeCredentialStore:
    def __init__(self, directory: Path, *, backend: str = 'secret-service') -> None:
        self.backend = storage_backend(backend)
        expected = {'secret-service': sys.platform.startswith('linux'),
                    'keychain': sys.platform == 'darwin', 'windows-credential': sys.platform == 'win32'}
        if not expected.get(self.backend, False):
            raise OAuthError('Selected native storage unavailable on this platform; no plaintext fallback')
        self.directory = directory.expanduser().absolute()  # Namespace only: no local token files.
        self.namespace = hashlib.sha256(str(self.directory).encode()).hexdigest()

    def public(self, value: SavedCredential, operation: str) -> dict[str, Any]:
        if self.backend != 'secret-service':
            title = 'macOS Keychain' if self.backend == 'keychain' else 'Windows Credential Manager'
            return {**value.public(operation), 'storage_backend': self.backend,
                    'storage_path': title + ' namespace ' + self.namespace,
                    'storage_notice': 'NATIVE ' + title + ' vault; no local plaintext credential file. '
                        'Explicit reviewed save/restore/forget only; no unlock/login, browser, network or tool grant. '
                        'Native access refusal has no file fallback; at-rest protection belongs to the OS vault, '
                        'not an OS sandbox.'}
        return {**value.public(operation), 'storage_backend': self.backend,
                'storage_path': 'Native Secret Service namespace ' + self.namespace,
                'storage_notice': 'NATIVE SECRET SERVICE vault; no local plaintext credential file. '
                    'Requires an already running, unlocked default collection and encrypted service session. '
                    'No automatic unlock/prompt/start, login, token POST, connection or tool grant. '
                    'At-rest protection belongs to the selected native service; this is not an OS sandbox.'}

    def _run(self, action: str, resource: str, alive: Callable[[], bool], payload: str = '') -> dict[str, Any]:
        if not alive():
            raise OAuthError('Native credential owner changed before startup')
        attributes = {'xdg:schema': 'org.xdog.McpCredentials', 'xdog.namespace': self.namespace,
                      'xdog.resource': hashlib.sha256(endpoint(resource, https=False).encode()).hexdigest()}
        request = (json.dumps({'action': action, 'attributes': attributes, 'payload': payload},
                              ensure_ascii=True) + '\n').encode()
        if len(request) > 256 * 1024:
            raise OAuthError('Native credential helper request exceeds capacity')
        if self.backend != 'secret-service':
            return self._run_platform(action, request, alive)
        helper = Path(__file__).with_name('native_store_worker.py')
        try:
            process = subprocess.Popen(['/usr/bin/python3', '-I', str(helper)], stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError:
            raise OAuthError('Native storage dependencies unavailable; no plaintext fallback') from None
        assert process.stdin is not None and process.stdout is not None
        selector = selectors.DefaultSelector()
        buffer = bytearray()
        deadline = time.monotonic() + HELPER_TIMEOUT
        outgoing = memoryview(request)
        admitted = action == 'read'
        try:
            os.set_blocking(process.stdin.fileno(), False)
            os.set_blocking(process.stdout.fileno(), False)
            selector.register(process.stdin, selectors.EVENT_WRITE)
            selector.register(process.stdout, selectors.EVENT_READ)
            while time.monotonic() < deadline:
                if not alive():
                    raise OAuthError('Native credential owner changed; admitted effects uncertain, no replay')
                for key, _event in selector.select(0.05):
                    if key.fileobj is process.stdin:
                        if outgoing:
                            try:
                                size = os.write(process.stdin.fileno(), outgoing)
                            except BlockingIOError:
                                continue
                            outgoing = outgoing[size:]
                        if not outgoing:
                            selector.unregister(process.stdin)
                    else:
                        try:
                            block = os.read(process.stdout.fileno(), 8192)
                        except BlockingIOError:
                            continue
                        if not block:
                            raise OAuthError('Native credential helper ended without a valid receipt')
                        buffer.extend(block)
                        if len(buffer) > 256 * 1024:
                            raise OAuthError('Native credential helper response exceeds capacity')
                        while b'\n' in buffer:
                            line, _, tail = buffer.partition(b'\n')
                            buffer = bytearray(tail)
                            message = json.loads(line)
                            if not isinstance(message, dict):
                                raise OAuthError('Invalid native credential helper response')
                            if message == {'ready': True} and not admitted:
                                if not alive():
                                    raise OAuthError('Native credential owner changed before admission')
                                outgoing = memoryview(b'{"commit":true}\n')
                                admitted = True
                                selector.register(process.stdin, selectors.EVENT_WRITE)
                                continue
                            if message.get('ok') is not True or not admitted:
                                raise OAuthError('Native storage unavailable/locked/unsafe; no plaintext fallback')
                            if not alive():
                                raise OAuthError('Native operation owner changed after receipt; effects uncertain')
                            process.wait(timeout=max(0.1, deadline - time.monotonic()))
                            if process.returncode != 0:
                                raise OAuthError('Native operation failed after receipt; inspect vault before retrying')
                            return message
            raise OAuthError('Native credential helper timed out; effects uncertain, not replayed')
        except (OSError, ValueError, UnicodeError, subprocess.TimeoutExpired) as error:
            raise OAuthError('Native credential operation failed (' + type(error).__name__
                             + '); no plaintext or login fallback') from None
        finally:
            selector.close()
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=1)
            process.stdin.close()
            process.stdout.close()

    def _run_platform(self, action: str, request: bytes, alive: Callable[[], bool]) -> dict[str, Any]:
        """Pipe threads work on Windows too; POSIX selectors cannot watch Win32 pipes.

        Only this explicitly owned helper is terminated; blocked pipe threads exit
        after it is reaped. Bounded receipts, no shell or secrets in argv/env/files.
        """
        helper = Path(__file__).with_name('native_platform_worker.py')
        try:
            process = subprocess.Popen([sys.executable, '-I', str(helper)], stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        except OSError:
            raise OAuthError('Native storage helper unavailable; no plaintext fallback') from None
        assert process.stdin is not None and process.stdout is not None
        stdin, stdout = process.stdin, process.stdout
        incoming: queue.Queue[bytes | None] = queue.Queue(maxsize=4)
        outgoing: queue.Queue[bytes | None] = queue.Queue(maxsize=2)
        stopped = threading.Event()
        def reader() -> None:
            try:
                while not stopped.is_set():
                    line = stdout.readline(256 * 1024 + 1)
                    while not stopped.is_set():
                        try:
                            incoming.put(line or None, timeout=0.05)
                            break
                        except queue.Full:
                            continue
                    if not line or len(line) > 256 * 1024:
                        return
            except (OSError, ValueError):
                pass
        def writer() -> None:
            try:
                while not stopped.is_set():
                    try:
                        data = outgoing.get(timeout=0.05)
                    except queue.Empty:
                        continue
                    if data is None:
                        return
                    stdin.write(data)
                    stdin.flush()
            except (OSError, ValueError):
                pass
        threads = [threading.Thread(target=reader, daemon=True, name='xdog-native-vault-reader'),
                   threading.Thread(target=writer, daemon=True, name='xdog-native-vault-writer')]
        deadline = time.monotonic() + HELPER_TIMEOUT
        admitted = action == 'read'
        try:
            for thread in threads:
                thread.start()
            outgoing.put_nowait(request)
            while time.monotonic() < deadline:
                if not alive():
                    raise OAuthError('Native credential owner changed; effects uncertain, no replay')
                try:
                    line = incoming.get(timeout=min(0.05, max(0.001, deadline - time.monotonic())))
                except queue.Empty:
                    continue
                if line is None or len(line) > 256 * 1024 or not line.endswith(b'\n'):
                    raise OAuthError('Native credential helper receipt missing/oversized')
                message = json.loads(line)
                if message == {'ready': True} and not admitted:
                    if not alive():
                        raise OAuthError('Native credential owner changed before admission')
                    outgoing.put_nowait(b'{"commit":true}\n')
                    admitted = True
                    continue
                if (not isinstance(message, dict) or message.get('ok') is not True or not admitted
                        or set(message) != ({'ok', 'payload'} if action == 'read' else {'ok'})):
                    raise OAuthError('Native storage unavailable/unsafe; no plaintext fallback')
                if not alive():
                    raise OAuthError('Native operation owner changed after receipt; effects uncertain')
                process.wait(timeout=max(0.1, deadline - time.monotonic()))
                if process.returncode != 0:
                    raise OAuthError('Native operation failed after receipt; inspect vault before retrying')
                if not alive():
                    raise OAuthError('Native credential owner changed during helper exit; effects uncertain')
                return message
            raise OAuthError('Native credential helper timed out; effects uncertain, not replayed')
        except (OSError, ValueError, UnicodeError, queue.Full, subprocess.TimeoutExpired):
            raise OAuthError('Native credential helper failed; no plaintext or login fallback') from None
        finally:
            stopped.set()
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=1)
            for thread in threads:
                if thread.ident is not None:
                    thread.join(1)
            process.stdin.close()
            process.stdout.close()

    def save(self, value: SavedCredential, alive: Callable[[], bool]) -> None:
        payload = json.dumps({'version': 1, **asdict(value)}, sort_keys=True)
        if len(payload.encode()) > 64 * 1024:
            raise OAuthError('Native credential record exceeds capacity')
        self._run('save', value.resource, alive, payload)

    def read(self, resource: str, alive: Callable[[], bool] = lambda: True) -> SavedCredential:
        value = self._run('read', resource, alive).get('payload')
        if not isinstance(value, str):
            raise OAuthError('Native saved credential missing/invalid; no fallback')
        try:
            return decode_credential(value.encode(), resource)
        except (ValueError, TypeError, KeyError, UnicodeError):
            raise OAuthError('Native saved credential is invalid/unsafe; no login or file fallback') from None

    def forget(self, resource: str, alive: Callable[[], bool] = lambda: True) -> None:
        self._run('forget', resource, alive)
