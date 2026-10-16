"""Explicit native Secret Service storage, with bounded owned helper and no plaintext fallback."""
from __future__ import annotations

import hashlib
import json
import os
import selectors
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import asdict
from pathlib import Path
from typing import Any

from xdog.agent.mcp.oauth import OAuthError, endpoint
from xdog.agent.mcp.oauth_store import SavedCredential, decode_credential

HELPER_TIMEOUT = 10.0


def storage_backend(value: Any) -> str:
    if not isinstance(value, str) or value not in {'file', 'secret-service'}:
        raise OAuthError('Unsupported credential backend; select file or secret-service explicitly, no fallback')
    return value


class NativeCredentialStore:
    def __init__(self, directory: Path, *, backend: str = 'secret-service') -> None:
        self.backend = storage_backend(backend)
        if self.backend != 'secret-service' or not sys.platform.startswith('linux'):
            raise OAuthError('Selected native storage unavailable on this platform; no plaintext fallback')
        self.directory = directory.expanduser().absolute()  # Namespace only: no local token files.
        self.namespace = hashlib.sha256(str(self.directory).encode()).hexdigest()

    def public(self, value: SavedCredential, operation: str) -> dict[str, Any]:
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
            # Reaping the helper can break a buffered write. Cleanup must not
            # mask its safe public failure or prevent the other pipe closing.
            with suppress(OSError):
                process.stdin.close()
            process.stdout.close()

    def save(self, value: SavedCredential, alive: Callable[[], bool]) -> None:
        payload = json.dumps({'version': 1, **asdict(value)}, sort_keys=True)
        if len(payload.encode()) > 64 * 1024:
            raise OAuthError('Native credential record exceeds capacity')
        # Frozen records still contain a mutable review dict. Recheck the exact
        # serialized snapshot before admitting any vault helper I/O.
        decode_credential(payload.encode(), value.resource)
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
