"""Worker-private extension OAuth callbacks and atomic credential/model publication.

This is a callback lifecycle, not an OAuth HTTP client. No login, refresh, browser
or storage operation runs merely because a model catalog is read or a turn starts.
"""
from __future__ import annotations

import copy
import inspect
import json
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from xdog.agent.extensions.provider_auth import ProviderCredential, _failure
from xdog.agent.mcp.schema import json_value


def credentials(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or not {'access', 'refresh', 'expires'} <= value.keys():
        raise ValueError('Invalid private provider OAuth credential record')
    try:
        result = json.loads(json_value(value, maximum=64 * 1024))
    except Exception:
        raise ValueError('Invalid private provider OAuth credential data') from None
    for key in ('access', 'refresh'):
        token = result[key]
        if not isinstance(token, str) or (not token and key == 'access'):
            raise ValueError('Invalid private provider OAuth token')
        if token:
            ProviderCredential('bearer', token)
    expires = result['expires']
    if type(expires) not in (int, float) or not 0 < expires <= 2**53 or not math.isfinite(expires):
        raise ValueError('Invalid provider OAuth expiration (epoch milliseconds required)')
    return dict(result)


def public_oauth(value: Any) -> dict[str, Any]:
    if (not isinstance(value, dict) or set(value) != {'name', 'authenticated', 'expires', 'refresh_available'}
            or not isinstance(value['name'], str) or not value['name'].strip() or len(value['name'].encode()) > 128
            or any(ord(char) < 32 or 127 <= ord(char) < 160 for char in value['name'])
            or type(value['authenticated']) is not bool or type(value['refresh_available']) is not bool
            or type(value['expires']) not in (int, float) or not 0 <= value['expires'] <= 2**53
            or not math.isfinite(value['expires'])):
        raise ValueError('Invalid public provider OAuth metadata')
    return dict(value)


async def invoke(callback: Callable[..., Any], *args: Any) -> Any:
    result = callback(*args)
    return await result if inspect.isawaitable(result) else result


@dataclass(frozen=True)
class ProviderOAuth:
    name: str
    login: Callable[..., Any] = field(repr=False)
    refresh_token: Callable[..., Any] = field(repr=False)
    get_api_key: Callable[..., Any] = field(repr=False)
    modify_models: Callable[..., Any] | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        public_oauth({'name': self.name, 'authenticated': False, 'expires': 0, 'refresh_available': False})
        if (not all(callable(callback) for callback in (self.login, self.refresh_token, self.get_api_key))
                or self.modify_models is not None and not callable(self.modify_models)):
            raise ValueError('Provider OAuth callbacks must be callable')


def authorization_url(url: Any, instructions: Any) -> None:
    parsed = urlsplit(url) if isinstance(url, str) else None
    if (parsed is None or not url.isascii() or len(url.encode()) > 8192
            or parsed.scheme != 'https' or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or any(ord(char) < 33 or ord(char) == 127 for char in url)
            or not isinstance(instructions, str) or len(instructions.encode()) > 4000):
        raise ValueError('Invalid provider authorization URL/instructions')
    parsed.port  # Reject malformed ports without contacting or resolving the host.


def authorization_prompt(prompt: Any) -> None:
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt.encode()) > 4000:
        raise ValueError('Invalid provider authorization prompt')


class OAuthCallbacks:
    def __init__(self, api: Any, provider: str, name: str, current: Callable[[], bool]) -> None:
        self.api, self.provider, self.name, self.current = api, provider, name, current
        self.steps = 0

    async def _ask(self, kind: str, phase: str, message: str, **fields: Any) -> Any:
        self.steps += 1
        if self.steps > 16 or not self.current():
            raise ValueError('Provider login step budget/owner changed')
        value = await self.api._ask(kind, self.name + ' sign-in', message,
            provider_auth={'provider': self.provider, 'phase': phase, **fields}, timeout=300)
        if not self.current():
            raise ValueError('Provider login owner changed after review')
        return value

    async def on_auth(self, url: str, instructions: str = '') -> None:
        authorization_url(url, instructions)
        accepted = await self._ask('confirm', 'on_auth', instructions, url=url, instructions=instructions)
        if accepted is not True:
            raise ValueError('Provider authorization was canceled')

    async def on_prompt(self, prompt: str) -> str:
        authorization_prompt(prompt)
        value = await self._ask('input', 'on_prompt', prompt, prompt=prompt)
        if (not isinstance(value, str) or not value.strip() or len(value.encode()) > 8192
                or any(ord(char) < 32 or ord(char) == 127 for char in value)):
            raise ValueError('Provider authorization code was canceled/invalid')
        return value


class OAuthBinding:
    """Stored only in the API's private callback slot; public info is a separate DTO."""
    def __init__(self, api: Any, info: dict[str, Any], callback: Callable[..., Any], oauth: ProviderOAuth,
                 current: Callable[[], bool]) -> None:
        self.api, self.info, self.callback, self.oauth, self.current = api, info, callback, oauth, current
        self.base_models = copy.deepcopy(info['models'])
        self._credentials: dict[str, Any] | None = None
        self._busy = False

    def status(self) -> dict[str, Any]:
        value = self._credentials
        return {'name': self.oauth.name, 'authenticated': bool(value and value['expires'] > time.time() * 1000),
                'expires': value['expires'] if value else 0, 'refresh_available': bool(value and value['refresh'])}

    async def __call__(self, model: dict[str, Any], context: dict[str, Any], options: dict[str, Any]) -> Any:
        value = self._credentials
        if self._busy or not self.current() or value is None or value['expires'] <= time.time() * 1000:
            return _failure()
        try:
            token = await invoke(self.oauth.get_api_key, copy.deepcopy(value))
            selected = ProviderCredential('bearer', token)
        except Exception:
            return _failure()
        if not self.current() or self._credentials is not value:
            raise ValueError('Provider credentials changed before model admission')
        if value['expires'] <= time.time() * 1000:
            return _failure()  # Async key resolution cannot extend a grant or trigger implicit refresh.
        return await invoke(self.callback, model, context, options, selected)

    async def _publish(self, value: dict[str, Any] | None) -> None:
        from xdog.agent.extensions.providers import registration
        models = copy.deepcopy(self.base_models)
        if value is not None and self.oauth.modify_models is not None:
            models = await invoke(self.oauth.modify_models, copy.deepcopy(value), models)
        normalized = registration(self.info['name'], models, allow_empty=True)['models']
        if not self.current():
            raise ValueError('Provider registration changed before credential publication')
        self.api._advance_provider_revision()
        self._credentials = value
        self.info['models'], self.info['oauth'] = normalized, self.status()

    async def control(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        if self._busy or not self.current():
            raise ValueError('Provider authentication is busy or retired')
        self._busy = True
        try:
            if action == 'login':
                value = credentials(await invoke(self.oauth.login,
                    OAuthCallbacks(self.api, self.info['name'], self.oauth.name, self.current)))
                await self._publish(value)
            elif action == 'refresh':
                current_credentials = self._credentials
                if current_credentials is None or not current_credentials['refresh']:
                    raise ValueError('Provider refresh credential is unavailable')
                try:
                    updated = credentials(await invoke(self.oauth.refresh_token, copy.deepcopy(current_credentials)))
                    await self._publish(updated)
                except BaseException:
                    # A remote refresh may rotate the grant before failing. Do not
                    # reuse or silently replay the now-uncertain old credentials.
                    if self.current():
                        await self._publish(None)
                    raise
            elif action == 'logout':
                await self._publish(None)
            elif action in ('save', 'restore', 'forget'):
                from xdog.agent.extensions.provider_oauth_store import operate
                loaded = await operate(action, params, self._credentials, self.current)
                if action == 'restore':
                    await self._publish(loaded)
            else:
                raise ValueError('Unknown provider auth action')
            if not self.current():
                raise ValueError('Provider registration changed during authentication')
            return {'ok': True, **self.status()}
        finally:
            self._busy = False
