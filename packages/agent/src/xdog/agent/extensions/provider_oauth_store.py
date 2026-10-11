"""Native-vault-only private provider records using the existing owned helper transport."""
from __future__ import annotations

import asyncio
import json
import re
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from xdog.agent.extensions.provider_oauth import credentials
from xdog.agent.mcp.oauth_native_store import NativeCredentialStore


def storage_config(value: Any) -> dict[str, str]:
    if (not isinstance(value, dict) or set(value) != {'backend', 'namespace'}
            or value['backend'] not in ('secret-service', 'keychain', 'windows-credential')
            or not isinstance(value['namespace'], str) or not Path(value['namespace']).is_absolute()
            or len(value['namespace'].encode()) > 8192 or '\x00' in value['namespace']):
        raise ValueError('Provider credential storage requires an explicit native backend/absolute namespace')
    return dict(value)


async def operate(action: str, params: dict[str, Any], value: dict[str, Any] | None,
                  alive: Callable[[], bool]) -> dict[str, Any] | None:
    if action not in {'save', 'restore', 'forget'}:
        raise ValueError('Invalid provider native storage operation')
    config = storage_config(params.get('storage'))
    binding = params.get('binding')
    if not isinstance(binding, str) or re.fullmatch('[a-f0-9]{64}', binding) is None:
        raise ValueError('Provider credential source/profile binding is required')
    store = NativeCredentialStore(Path(config['namespace']) / 'extension-provider-oauth', backend=config['backend'])
    # This identifier only indexes the vault. It is never fetched or used for OAuth discovery.
    resource = 'https://xdog-provider.invalid/' + binding
    canceled = threading.Event()

    def current() -> bool:
        return not canceled.is_set() and alive()

    def run() -> dict[str, Any] | None:
        if action == 'save':
            if value is None:
                raise ValueError('No provider credential to save')
            payload = json.dumps({'version': 1, 'binding': binding, 'credentials': credentials(value)},
                                 ensure_ascii=True)
            if len(payload.encode()) > 128 * 1024:
                raise ValueError('Provider credential record exceeds native storage limit')
            store._run('save', resource, current, payload)
            return None
        if action == 'forget':
            store._run('forget', resource, current)
            return None
        stored = store._run('read', resource, current).get('payload')
        if not isinstance(stored, str) or len(stored.encode()) > 128 * 1024:
            raise ValueError('Invalid saved provider credential data')
        def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, item in pairs:
                if key in result:
                    raise ValueError('Duplicate saved provider credential field')
                result[key] = item
            return result
        decoded = json.loads(stored, object_pairs_hook=unique)
        if (not isinstance(decoded, dict) or set(decoded) != {'version', 'binding', 'credentials'}
                or type(decoded['version']) is not int or decoded['version'] != 1 or decoded['binding'] != binding):
            raise ValueError('Saved provider credential source/profile changed')
        return credentials(decoded['credentials'])

    task = asyncio.create_task(asyncio.to_thread(run))
    try:
        return await asyncio.shield(task)
    finally:
        canceled.set()
        # Reap the existing owned helper before releasing registration/session ownership.
        while not task.done():
            try:
                await asyncio.shield(task)
            except BaseException:
                continue
        if not task.cancelled():
            task.exception()  # Retrieve errors on the canceled caller path; never log private payloads.
