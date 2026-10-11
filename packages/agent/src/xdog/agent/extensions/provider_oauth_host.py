"""Explicit host-side /extensions auth adapter; credentials stay in the worker.

The host supplies an owned dialog broker and lifetime predicate. This module never
opens a browser, calls a model, reads a token, or chooses a default auth operation.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import shlex
from collections.abc import Awaitable, Callable
from typing import Any

from xdog.agent.extensions.provider_oauth import public_oauth
from xdog.agent.extensions.provider_oauth_runtime import ACTIONS
from xdog.agent.extensions.provider_oauth_store import storage_config
from xdog.agent.mcp.schema import json_value

AUTH_HELP = ('/extensions auth [list|menu|login ID|refresh ID|logout ID|'
             'save ID BACKEND NAMESPACE|restore ID BACKEND NAMESPACE|forget ID BACKEND NAMESPACE]')
Ask = Callable[[str, dict[str, Any]], Awaitable[Any]]


def login_arguments(arguments: str) -> str:
    """Thin alias parser; no arbitrary action/default provider or extra tokens."""
    if not isinstance(arguments, str) or len(arguments.encode()) > 512:
        raise ValueError('Usage: /login [EXTENSION_PROVIDER_ID]; native provider auth uses xdog-ai')
    try:
        parts = shlex.split(arguments)
    except ValueError:
        parts = ['', '']
    if not parts:
        return 'menu'
    if (len(parts) != 1 or not parts[0].startswith('ext-') or len(parts[0].encode()) > 256
            or any(c.isspace() or ord(c) < 33 or ord(c) == 127 for c in parts[0])):
        raise ValueError('Usage: /login [EXTENSION_PROVIDER_ID]; native provider auth uses xdog-ai')
    return 'login ' + shlex.quote(parts[0])


def auth_catalog(runtime: Any) -> dict[str, Any]:
    source = runtime.provider_auth_catalog()
    if not isinstance(source, tuple) or len(source) > 512:
        raise ValueError('Provider authentication catalog unavailable')
    rows = []
    for item in source:
        if (not isinstance(item, dict) or set(item) != {'id', 'name', 'authenticated', 'expires', 'refresh_available'}
                or not isinstance(item['id'], str) or not 0 < len(item['id'].encode()) <= 256
                or any(ord(c) < 33 or ord(c) == 127 for c in item['id'])):
            raise ValueError('Invalid public provider authentication catalog')
        rows.append({'id': item['id'], **public_oauth({key: value for key, value in item.items() if key != 'id'})})
    if len({row['id'] for row in rows}) != len(rows):
        raise ValueError('Ambiguous provider authentication identity')
    return {'providers': rows, 'help': AUTH_HELP, 'notice': 'Status only; no login/refresh/storage action was started.'}


class ProviderAuthHost:
    def __init__(self, runtime: Any, ask: Ask, alive: Callable[[], bool], *, native_storage: bool = False) -> None:
        self.runtime, self.ask, self.alive = runtime, ask, alive
        self.native_storage = native_storage
        self._lease = ''
        self._provider = ''
        self._review_digest = ''
        self._busy = False

    def owns(self, dialog: dict[str, Any]) -> bool:
        try:
            return (self._busy and bool(self._lease) and self.alive()
                    and dialog.get('provider_auth_host') == self._lease
                    and isinstance(dialog.get('provider_auth'), dict)
                    and dialog['provider_auth'].get('provider_id') == self._provider
                    and dialog.get('provider_auth_digest') == self._review_digest
                    and hashlib.sha256(json_value(dialog['provider_auth'], maximum=32 * 1024).encode()).hexdigest()
                        == self._review_digest)
        except (ValueError, RuntimeError, TypeError):
            return False

    def owns_menu(self, dialog: dict[str, Any]) -> bool:
        return (self._busy and bool(self._lease) and self.alive()
                and dialog.get('provider_auth_menu') == self._lease)

    def revoke(self) -> None:
        self._lease = ''

    async def __call__(self, arguments: str) -> dict[str, Any]:
        if not isinstance(arguments, str) or len(arguments.encode()) > 16384:
            raise ValueError(AUTH_HELP)
        try:
            parts = shlex.split(arguments)
        except ValueError:
            raise ValueError(AUTH_HELP) from None
        if not parts or parts == ['list']:
            return auth_catalog(self.runtime)
        if self._busy or not self.alive():
            raise ValueError('Provider authentication requires the original idle host')
        if parts[0] not in ACTIONS | {'menu'}:
            raise ValueError(AUTH_HELP)
        self._busy, self._lease = True, secrets.token_hex(16)
        lease = self._lease
        def current() -> bool:
            return self._lease == lease and self.alive()
        try:
            if parts == ['menu']:
                rows = auth_catalog(self.runtime)['providers']
                if not rows:
                    return {'providers': [], 'notice': 'No configured extension provider offers authentication.'}
                page = 0
                chosen: Any = None
                for _ in range(64):
                    visible = rows[page * 28:(page + 1) * 28]
                    choices = ['Cancel', *[row['id'] for row in visible]]
                    if page:
                        choices.append('Previous page')
                    if (page + 1) * 28 < len(rows):
                        choices.append('Next page')
                    chosen = await self.ask('provider-auth', {'id': secrets.token_hex(16), 'kind': 'select',
                        'title': 'Extension provider authentication',
                        'message': f'Select a provider; no action yet. Page {page + 1}.', 'choices': choices,
                        'provider_auth_menu': lease})
                    if chosen == 'Cancel' or chosen is None or not current():
                        return {'ok': False}
                    if not isinstance(chosen, str) or chosen not in choices:
                        raise ValueError('Provider selection changed')
                    if chosen in ('Previous page', 'Next page'):
                        page += -1 if chosen == 'Previous page' else 1
                        continue
                    break
                else:
                    raise ValueError('Provider selection exceeded review-step limit')
                action = await self.ask(chosen, {'id': secrets.token_hex(16), 'kind': 'select',
                    'title': 'Choose an explicit authentication action',
                    'message': 'Storage requires explicit backend/namespace arguments, never a default.',
                    'choices': ['Cancel', 'login', 'refresh', 'logout'], 'provider_auth_menu': lease})
                if action == 'Cancel' or action is None or not current():
                    return {'ok': False}
                if action not in ('login', 'refresh', 'logout'):
                    raise ValueError('Invalid provider authentication menu action')
                parts = [action, chosen]
            action = parts[0]
            storing = action in {'save', 'restore', 'forget'}
            if len(parts) != (4 if storing else 2) or action not in ACTIONS:
                raise ValueError(AUTH_HELP)
            if storing and not self.native_storage:
                raise ValueError('Provider credential storage is native-only and needs separate explicit review')
            storage = storage_config({'backend': parts[2], 'namespace': parts[3]}) if storing else None
            identifier = parts[1]
            if identifier not in {row['id'] for row in auth_catalog(self.runtime)['providers']}:
                raise ValueError('Provider authentication identity is no longer available')
            self._provider = identifier

            async def review(request: dict[str, Any]) -> Any:
                if not current() or request.get('provider_id') != identifier or request.get('action') != action:
                    raise ValueError('Provider authentication review owner changed')
                body = json.loads(json_value(request, maximum=32 * 1024))
                phase = body.get('phase')
                if phase not in {'consent', 'on_auth', 'on_prompt'}:
                    raise ValueError('Unsupported provider authentication review phase')
                kind = 'input' if phase == 'on_prompt' else 'confirm'
                self._review_digest = hashlib.sha256(json_value(body, maximum=32 * 1024).encode()).hexdigest()
                dialog = {'id': secrets.token_hex(16), 'kind': kind, 'timeout': 300,
                    'title': 'Extension provider authentication · ' + body['name'],
                    'message': 'Review exact operation. No model/tool/permission grant or automatic browser action.',
                    'provider_auth': body, 'provider_auth_host': lease, 'provider_auth_digest': self._review_digest}
                answer = await self.ask(identifier, dialog)
                if not current() or not self.owns(dialog):
                    raise ValueError('Provider authentication view/data changed after review')
                self._review_digest = ''
                if kind == 'confirm':
                    return answer is True
                if answer is None:
                    return None
                if (not isinstance(answer, str) or not answer.strip() or len(answer.encode()) > 8192
                        or any(ord(c) < 32 or ord(c) == 127 for c in answer)):
                    raise ValueError('Invalid/canceled provider authentication input')
                return answer  # Private ephemeral reply only; never added to public result/snapshot.

            result = await self.runtime.provider_auth_control(identifier, action, ask=review,
                                                                alive=current, storage=storage)
            if not current() or not isinstance(result, dict) or type(result.get('ok')) is not bool:
                raise ValueError('Provider authentication operation owner/result changed')
            return {'ok': result['ok'], **public_oauth({key: value for key, value in result.items() if key != 'ok'})}
        finally:
            self._lease, self._provider, self._review_digest, self._busy = '', '', '', False
