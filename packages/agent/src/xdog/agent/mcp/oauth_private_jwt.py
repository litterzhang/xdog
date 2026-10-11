"""Owned POSIX/OpenSSL RS256 client assertions; private PEM travels only over stdin."""
from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import secrets
import shutil
import tempfile
import time
from typing import BinaryIO, TypeVar

from xdog.agent.mcp.oauth import OAuthError, client_identifier, endpoint

T = TypeVar('T')


async def _settle(task: asyncio.Task[T]) -> T:
    """Cleanup-only wait: repeated cancellation cannot abandon an owned handle/process."""
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
    return task.result()


def _key(variable: str) -> bytes:
    value = os.environ.get(variable, '')
    if (not value or len(value.encode()) > 65536 or 'ENCRYPTED' in value
            or any(ord(char) < 32 and char not in '\r\n\t' or ord(char) > 126 for char in value)
            or not value.lstrip().startswith(('-----BEGIN PRIVATE KEY-----', '-----BEGIN RSA PRIVATE KEY-----'))):
        raise OAuthError('Configured OAuth RSA private-key environment value is missing/unsafe')
    return value.encode('ascii')


def _program() -> str:
    program = shutil.which('openssl')
    if os.name != 'posix' or program is None or not os.path.exists('/dev/stdin') or not os.path.isdir('/dev/fd'):
        raise OAuthError('Private-key JWT requires the explicit POSIX/OpenSSL signer; no fallback')
    return program


async def _run(args: list[str], key: bytes, *, descriptors: tuple[int, ...] = ()) -> bytes:
    process = None
    task = None
    starting = asyncio.create_task(asyncio.create_subprocess_exec(_program(), *args, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, pass_fds=descriptors, env={"LC_ALL": "C"}))
    try:
        process = await asyncio.shield(starting)
        task = asyncio.create_task(process.communicate(key))
        async with asyncio.timeout(5):
            output, _errors = await asyncio.shield(task)
        if process.returncode != 0 or len(output) > 65536:
            raise OAuthError('Configured private-key signer rejected the key/algorithm; no fallback')
        return output
    except (OSError, TimeoutError):
        raise OAuthError('Private-key signer unavailable/timed out; no token request or fallback') from None
    finally:
        if process is None:
            try:
                process = await _settle(starting)
            except (OSError, RuntimeError):
                pass
        if process is not None and process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        if task is not None:
            async def drain() -> None:
                await asyncio.gather(task, return_exceptions=True)
            await _settle(asyncio.create_task(drain()))
        if process is not None:
            await _settle(asyncio.create_task(process.wait()))


async def _validate_key(value: bytes) -> None:
    public = await _run(['pkey', '-text_pub', '-noout', '-passin', 'pass:'], value)
    match = re.match(rb'Public-Key: \(([0-9]+) bit\)', public)
    if match is None or not 2048 <= int(match[1]) <= 8192 or b'Modulus:' not in public or b'Exponent:' not in public:
        raise OAuthError('Private-key JWT requires an unencrypted 2048-8192-bit RSA key; no algorithm fallback')


async def validate_key(variable: str) -> None:
    await _validate_key(_key(variable))


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode('ascii').rstrip('=')


async def assertion(variable: str, client_id: str, token_endpoint: str) -> str:
    client_identifier(client_id)
    audience = endpoint(token_endpoint)
    key = _key(variable)
    await _validate_key(key)
    now = int(time.time())
    if not 0 <= now <= 2**53 - 61:
        raise OAuthError('Invalid client assertion clock')
    claims = _b64(json.dumps({'iss': client_id, 'sub': client_id, 'aud': audience, 'iat': now,
        'exp': now + 60, 'jti': secrets.token_urlsafe(24)}, ensure_ascii=True, allow_nan=False,
        separators=(',', ':')).encode('ascii'))
    signing = (_b64(b'{"alg":"RS256","typ":"JWT"}') + '.' + claims).encode('ascii')
    file: BinaryIO | None = None
    def create() -> BinaryIO:
        result = tempfile.TemporaryFile()
        try:
            result.write(signing)
            result.flush()
            result.seek(0)
            return result
        except BaseException:
            result.close()
            raise
    creating = asyncio.create_task(asyncio.to_thread(create))
    try:
        file = await asyncio.shield(creating)
        signature = await _run(['dgst', '-sha256', '-sign', '/dev/stdin', '-passin', 'pass:',
            '-sigopt', 'rsa_padding_mode:pkcs1', '/dev/fd/' + str(file.fileno())], key,
            descriptors=(file.fileno(),))
        if not 256 <= len(signature) <= 1024:
            raise OAuthError('Invalid RSA signer output; no token request')
        return signing.decode('ascii') + '.' + _b64(signature)
    finally:
        if file is None:
            try:
                file = await _settle(creating)
            except Exception:
                pass
        if file is not None:
            file.close()  # The temporary file contains only unsigned public claims, never the private PEM.
