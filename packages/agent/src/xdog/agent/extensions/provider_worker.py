"""Single owned provider generator in a trusted worker, with independent checksummed input."""
from __future__ import annotations

import copy
import inspect
import uuid
from typing import Any

from xdog.agent.extensions.api import ExtensionAPI, invoke
from xdog.agent.extensions.providers import wire_event
from xdog.agent.extensions.transfer import ContextTransfer


class ProviderCalls:
    def __init__(self) -> None:
        self.transfer = ContextTransfer()
        self._starting = False
        self._authenticating = False
        self._closed = False
        self.calls: dict[str, Any] = {}
        self.counts: dict[str, tuple[int, int]] = {}

    async def request(self, api: ExtensionAPI, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if self._closed:
            raise ValueError('Provider worker is closed')
        if method == 'xdog/provider_begin':
            if self._starting or self._authenticating or self.calls or self.transfer.file is not None:
                raise ValueError('Provider worker already owns a stream/input transfer')
            self.transfer.begin(params)
            return {}
        if method == 'xdog/provider_chunk':
            self.transfer.chunk(params)
            return {}
        if method == 'xdog/provider_start':
            if self._starting or self._authenticating or self.calls:
                raise ValueError('Provider worker already owns a stream admission')
            try:
                request = self.transfer.finish(params)
            finally:
                self.transfer.close()
            name = params.get('provider')
            registration = api.providers.get(name) if isinstance(name, str) else None
            if (not isinstance(name, str) or registration is None or not isinstance(request, dict)
                    or set(request) != {'context', 'options'}
                    or not isinstance(request['context'], dict) or not isinstance(request['options'], dict)):
                raise ValueError('Provider input/registration is invalid')
            info, callback = registration
            if params.get('registration_id') != info.get('registration_id'):
                raise ValueError('Provider registration changed before stream admission')
            model = next((row for row in info['models'] if row['id'] == params.get('model')), None)
            if model is None:
                raise ValueError('Provider model is not registered')
            self._starting = True
            try:
                iterator = await invoke(callback, copy.deepcopy(model), request['context'], request['options'])
                token = uuid.uuid4().hex
                self.calls[token], self.counts[token] = iterator, (0, 0)
                if (not hasattr(iterator, '__aiter__') or not hasattr(iterator, '__anext__')
                        or self._closed or api.providers.get(name) is not registration):
                    await self.close_call(token)
                    raise ValueError('Provider iterator/registration changed before stream admission')
                return {'id': token}
            finally:
                self._starting = False
        if method == 'xdog/provider_next':
            token = params.get('id', '')
            if not isinstance(token, str):
                raise ValueError('Invalid provider stream identity')
            iterator = self.calls.get(token)
            if iterator is None:
                raise ValueError('Provider stream owner changed')
            try:
                try:
                    event = wire_event(await iterator.__anext__())
                except StopAsyncIteration:
                    raise ValueError('Provider stopped without an explicit completion') from None
                count, size = self.counts[token]
                import json
                count, size = count + 1, size + len(json.dumps(event).encode())
                if count > 4096 or size > 8 * 1024 * 1024:
                    raise ValueError('Provider output budget exceeded')
                self.counts[token] = count, size
            except BaseException:
                await self.close_call(token)
                raise
            if event['type'] in {'done', 'error'}:
                await self.close_call(token)
            return {'event': event}
        if method == 'xdog/provider_close':
            token = params.get('id', '')
            if not isinstance(token, str):
                raise ValueError('Invalid provider close identity')
            await self.close_call(token)
            return {}
        raise ValueError('Unknown provider stream operation')

    async def authenticate(self, api: ExtensionAPI, params: dict[str, Any]) -> dict[str, Any]:
        from xdog.agent.extensions.provider_oauth import OAuthBinding
        name = params.get('provider')
        entry = api.providers.get(name) if isinstance(name, str) else None
        if (self._closed or self._starting or self._authenticating or self.calls or self.transfer.file is not None
                or entry is None or not isinstance(entry[1], OAuthBinding)
                or params.get('registration_id') != entry[0].get('registration_id')):
            raise ValueError('Provider authentication owner is unavailable/changed')
        self._authenticating = True
        try:
            return await entry[1].control(params.get('action', ''), params)
        finally:
            self._authenticating = False

    async def close_call(self, token: str) -> None:
        iterator = self.calls.pop(token, None)
        self.counts.pop(token, None)
        close = getattr(iterator, 'aclose', None)
        if callable(close):
            result = close()
            if not inspect.isawaitable(result):
                raise ValueError('Provider iterator aclose must return an awaitable')
            await result

    async def close(self) -> None:
        self._closed = True
        self.transfer.close()
        for token in tuple(self.calls):
            await self.close_call(token)
