"""Pinned xiaohongshu-mcp HTTP protocol; no Agent, channel routing or retry loop."""
from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlsplit

import httpx


class XhsApi:
    def __init__(self, base_url: str, *, token_env: str = 'XHS_API_TOKEN',
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        target = urlsplit(base_url)
        if (target.scheme != 'http' or target.hostname not in {'localhost', '127.0.0.1', '::1'}
                or target.username or target.password or target.query or target.fragment
                or target.path not in {'', '/'}):
            raise ValueError('XHS backend must be an explicit local loopback HTTP service')
        token = os.environ.get(token_env, '')
        self._http = httpx.AsyncClient(base_url=base_url.rstrip('/'), timeout=120, follow_redirects=False,
            trust_env=False, headers={'Authorization': 'Bearer ' + token} if token else {}, transport=transport)

    async def close(self) -> None:
        await self._http.aclose()

    async def request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        async with self._http.stream(method, '/api/v1/' + path, **kwargs) as response:
            response.raise_for_status()
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) > 2 * 1024 * 1024:
                    raise ValueError('XHS response exceeds capacity')
        import json
        raw = json.loads(data)
        if not isinstance(raw, dict) or raw.get('success') is not True or not isinstance(raw.get('data'), dict):
            raise ValueError('XHS backend did not confirm a valid response')
        value = raw['data']
        if set(value) == {'data'}:
            value = value['data']
        if not isinstance(value, dict):
            raise ValueError('XHS backend response data must be an object')
        return value

    async def identity(self) -> tuple[str, str]:
        from xdog.claw.core.identity.models import label
        data = await self.request('GET', 'login/status')
        if data.get('is_logged_in') is not True:
            raise ValueError('XHS backend needs an explicit account login')
        return label(data.get('user_id'), maximum=256), label(data.get('username', ''), empty=True)

    async def notifications(self) -> list[dict[str, Any]]:
        data = await self.request('POST', 'notifications/list', json={'tab': 'mentions', 'limit': 200})
        items = data.get('items')
        if not isinstance(items, list) or len(items) > 200 or any(not isinstance(item, dict) for item in items):
            raise ValueError('Invalid XHS notification list')
        return items

    async def post(self, feed_id: str, xsec_token: str) -> dict[str, Any]:
        from xdog.claw.core.identity.models import label
        data = await self.request('POST', 'feeds/detail', json={
            'feed_id': feed_id, 'xsec_token': xsec_token, 'load_all_comments': False})
        detail = data.get('data')
        note = detail.get('note') if isinstance(detail, dict) else None
        if (data.get('feed_id') != feed_id or not isinstance(note, dict)
                or note.get('noteId') != feed_id):
            raise ValueError('XHS post response does not match the original post')
        # Explicit public fields only: no backend tokens, comments or account diagnostics.
        title = label(note.get('title', ''), empty=True, maximum=16 * 1024)
        body = label(note.get('desc', ''), empty=True, maximum=64 * 1024)
        return {'feed_id': feed_id, 'title': title, 'text': body, 'untrusted': True}
