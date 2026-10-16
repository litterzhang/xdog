"""Explicit private local OAuth snapshots, not automatic login or an OS sandbox.

Plaintext credentials are saved ONLY after host consent. File ownership/mode,
no-follow directory-relative I/O, bounds and atomic replacement are mandatory;
unsupported platforms fail closed instead of claiming equivalent ACL protection.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import secrets
import stat
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, cast

from xdog.agent.mcp.oauth import OAuthConfig, OAuthError, OAuthOwner, OAuthReview, endpoint, validate_review
from xdog.agent.mcp.oauth_tokens import _token

_LIMIT = 64 * 1024
_REVIEW_FIELDS = frozenset({'review_id', 'issuer', 'resource', 'client_id', 'scopes', 'authorization_endpoint',
    'token_endpoint', 'tls_trust', 'registration', 'registration_endpoint', 'client_name', 'callback_port',
    'client_assigned', 'token_auth_method', 'client_secret_env', 'notice'})


@dataclass(frozen=True, slots=True)
class SavedCredential:
    resource: str
    profile: str
    review: dict[str, Any]
    saved_at: float
    expires_at: float
    access: str = field(repr=False)
    refresh: str = field(repr=False)

    def __post_init__(self) -> None:
        validate_review(self.review)

    def public(self, operation: str) -> dict[str, Any]:
        return cast(dict[str, Any], json.loads(json.dumps({**self.review, 'operation': operation,
                'saved_at': self.saved_at,
                'expires_at': self.expires_at, 'refresh_available': bool(self.refresh),
                'storage_notice': 'PRIVATE LOCAL PLAINTEXT credential file; not encrypted/keychain storage. '
                                  'Explicit restore only; no connect, network refresh or tool permission.'})))


def profile_binding(resource: str, config: OAuthConfig) -> str:
    config.validate_public()
    value = {'resource': endpoint(resource, https=False), 'config': asdict(config)}
    if config.ca_file:
        path = Path(config.ca_file).expanduser()
        with path.open('rb') as stream:
            content = stream.read(1024 * 1024 + 1)
        if len(content) > 1024 * 1024:
            raise OAuthError('Configured CA file exceeds credential binding limit')
        value['ca_digest'] = hashlib.sha256(content).hexdigest()
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def capture(owner: OAuthOwner) -> SavedCredential:
    if owner._closed or owner._grant is None or not (owner._token or owner._refresh):
        raise OAuthError('No completed resource-bound credential grant to save')
    owner.config.validate_public()
    validate_review(owner._grant.to_dict())
    now = time.time()
    return SavedCredential(owner.resource, profile_binding(owner.resource, owner.config), owner._grant.to_dict(),
                           now, now + max(0, owner._expires - time.monotonic()),
                           _token(owner._token, required=bool(owner._token)) if owner._token else '',
                           _token(owner._refresh, required=False) if owner._refresh else '')


def restore(owner: OAuthOwner, value: SavedCredential) -> None:
    review = validate_review(value.review)
    owner.config.validate_public()
    if (owner._closed or value.resource != owner.resource
            or value.profile != profile_binding(owner.resource, owner.config)):
        raise OAuthError('Saved credential resource/profile binding changed')
    if (review['issuer'] != owner.config.issuer or review['resource'] != owner.resource
            or not set(review['scopes']) <= set(owner.config.scopes)
            or review.get('registration', 'pre-registered') != owner.config.registration
            or review.get('token_auth_method', 'none') != owner.config.token_auth_method
            or review.get('client_secret_env', '') != owner.config.client_secret_env
            or review['client_id'] != owner.config.client_id):
        raise OAuthError('Saved credential grant no longer matches configured consent')
    remaining = min(3600, max(0, value.expires_at - time.time()))
    if not remaining and not value.refresh:
        raise OAuthError('Saved access credential expired and has no refresh grant')
    owner._token = value.access if remaining else ''
    owner._refresh = value.refresh
    owner._expires = time.monotonic() + remaining
    owner._grant = OAuthReview(review['review_id'], review['issuer'], review['resource'], review['client_id'],
        tuple(review['scopes']), review['authorization_endpoint'], review['token_endpoint'],
        tls_trust=review.get('tls_trust', 'system'), registration=review.get('registration', 'pre-registered'),
        registration_endpoint=review.get('registration_endpoint', ''), client_name=review.get('client_name', 'XDOG'),
        callback_port=review.get('callback_port', 0), token_auth_method=review.get('token_auth_method', 'none'),
        client_secret_env=review.get('client_secret_env', ''))


def decode_credential(payload: bytes, resource: str) -> SavedCredential:
    if len(payload) > _LIMIT:
        raise OAuthError('Credential record exceeds storage capacity')
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result = {}
        for key, value in pairs:
            if key in result:
                raise OAuthError('Duplicate credential record field')
            result[key] = value
        return result
    data = json.loads(payload, object_pairs_hook=unique)
    if (not isinstance(data, dict) or set(data) != {'version', 'resource', 'profile', 'review', 'saved_at',
            'expires_at', 'access', 'refresh'} or type(data['version']) is not int or data['version'] != 1
            or data['resource'] != resource or not isinstance(data['profile'], str)
            or len(data['profile']) != 64 or any(char not in '0123456789abcdef' for char in data['profile'])
            or type(data['access']) is not str or type(data['refresh']) is not str):
        raise OAuthError('Invalid credential record binding/format')
    for key in ('saved_at', 'expires_at'):
        if type(data[key]) not in (int, float) or not math.isfinite(data[key]):
            raise OAuthError('Invalid credential expiry encoding')
    if not 0 < data['saved_at'] <= time.time() + 60 or not 0 <= data['expires_at'] - data['saved_at'] <= 3601:
        raise OAuthError('Saved credential clock/lifetime is unsafe')
    if not isinstance(data['review'], dict) or set(data['review']) != _REVIEW_FIELDS:
        raise OAuthError('Credential grant has unknown/missing public fields')
    review = validate_review(data['review'])
    access = _token(data['access'], required=True) if data['access'] else ''
    refresh = _token(data['refresh'], required=False) if data['refresh'] else ''
    if not (access or refresh):
        raise OAuthError('Saved credential record has no grant')
    return SavedCredential(resource, data['profile'], review, data['saved_at'], data['expires_at'],
                           access, refresh)


class CredentialStore:
    def __init__(self, directory: Path) -> None:
        self.directory = directory.expanduser().absolute()

    def public(self, value: SavedCredential, operation: str) -> dict[str, Any]:
        return {**value.public(operation), 'storage_backend': 'file', 'storage_path': str(self.directory)}

    def _open(self, *, create: bool) -> int:
        if os.name != 'posix' or not hasattr(os, 'O_NOFOLLOW') or not hasattr(os, 'getuid'):
            raise OAuthError('Private credential persistence requires verified local POSIX file protection')
        for part in (self.directory, *self.directory.parents):
            if part.is_symlink():
                raise OAuthError('Credential directory path contains a symlink')
        if create:
            self.directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        info = os.fstat(fd)
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
            os.close(fd)
            raise OAuthError('Credential directory must be owned by this user with mode 0700')
        return fd

    @staticmethod
    def _name(resource: str) -> str:
        return hashlib.sha256(endpoint(resource, https=False).encode()).hexdigest() + '.json'

    @staticmethod
    def _private(info: os.stat_result) -> None:
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) & 0o077 or info.st_nlink != 1 or info.st_size > _LIMIT):
            raise OAuthError('Credential file owner/mode/type/size is unsafe')

    def save(self, value: SavedCredential, alive: Callable[[], bool]) -> None:
        validate_review(value.review)
        fd = self._open(create=True)
        name, temporary = self._name(value.resource), secrets.token_hex(16) + '.tmp'
        try:
            try:
                self._private(os.stat(name, dir_fd=fd, follow_symlinks=False))
            except FileNotFoundError:
                pass
            payload = json.dumps({'version': 1, **asdict(value)}, sort_keys=True).encode()
            if len(payload) > _LIMIT or not alive():
                raise OAuthError('Credential save quota/owner changed')
            out = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
            try:
                with os.fdopen(out, 'wb') as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                if not alive():
                    raise OAuthError('Credential save owner changed before commit')
                current, opened = self.directory.lstat(), os.fstat(fd)
                if (current.st_ino, current.st_dev) != (opened.st_ino, opened.st_dev):
                    raise OAuthError('Credential directory changed before commit')
                if any(part.is_symlink() for part in (self.directory, *self.directory.parents)):
                    raise OAuthError('Credential directory path changed before commit')
                os.replace(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
                os.fsync(fd)
            finally:
                try:
                    os.unlink(temporary, dir_fd=fd)
                except FileNotFoundError:
                    pass
        except (OSError, UnicodeError):
            raise OAuthError('Private credential file write failed; inspect local storage, no retry') from None
        finally:
            os.close(fd)

    def read(self, resource: str, alive: Callable[[], bool] = lambda: True) -> SavedCredential:
        if not alive():
            raise OAuthError('Credential read owner changed')
        try:
            fd = self._open(create=False)
        except OSError:
            raise OAuthError('Saved credential file is unavailable; no login fallback') from None
        try:
            file = os.open(self._name(resource), os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
            with os.fdopen(file, 'rb') as stream:
                self._private(os.fstat(stream.fileno()))
                payload = stream.read(_LIMIT + 1)
            if not alive():
                raise OAuthError('Credential read owner changed after storage read')
            return decode_credential(payload, resource)
        except (OSError, ValueError, TypeError, UnicodeError, KeyError):
            raise OAuthError('Saved credential is unavailable/unsafe; no login or network fallback') from None
        finally:
            os.close(fd)

    def forget(self, resource: str, alive: Callable[[], bool] = lambda: True) -> None:
        try:
            fd = self._open(create=False)
        except FileNotFoundError:
            return
        try:
            name = self._name(resource)
            try:
                self._private(os.stat(name, dir_fd=fd, follow_symlinks=False))
                if not alive():
                    raise OAuthError('Credential erase owner changed before commit')
                os.unlink(name, dir_fd=fd)
                os.fsync(fd)
            except FileNotFoundError:
                pass
        finally:
            os.close(fd)
