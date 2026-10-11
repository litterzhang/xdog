"""Host review bridge for explicitly requested, registration-owned provider OAuth."""
from __future__ import annotations

import asyncio
import contextvars
import hashlib
import json
import time
from collections.abc import Awaitable, Callable
from typing import Any

from xdog.agent.extensions.provider_oauth import authorization_prompt, authorization_url, public_oauth
from xdog.agent.extensions.provider_oauth_store import storage_config

AUTH_REVIEW: contextvars.ContextVar[Callable[[str, dict[str, Any]], Awaitable[Any]] | None] = contextvars.ContextVar(
    'extension_provider_auth_review', default=None)
ACTIONS = frozenset({'login', 'refresh', 'logout', 'save', 'restore', 'forget'})


def catalog(runtime: Any) -> tuple[dict[str, Any], ...]:
    if runtime._closed:
        return ()
    return tuple({'id': 'ext-' + extension + '-' + row['name'], **public_oauth(row['oauth']),
                  'authenticated': row['oauth']['authenticated'] and row['oauth']['expires'] > time.time() * 1000}
                 for extension, info in tuple(runtime._infos.items())
                 if extension not in runtime._failed | runtime._unloaded
                 for row in info.get('providers', []) if 'oauth' in row)


def _binding(runtime: Any, extension: str, name: str, oauth_name: str) -> str:
    directory = runtime.root / extension
    files = []
    for index, path in enumerate(directory.rglob('*')):
        if index >= 2048:
            raise ValueError('Provider credential source traversal exceeds entry budget')
        if path.suffix == '.py' or path.name == 'extension.yaml':
            files.append(path)
            if len(files) > 128:
                raise ValueError('Provider credential source binding exceeds file budget')
    files.sort()
    digest = hashlib.sha256(json.dumps({'path': str(directory.resolve()), 'provider': name, 'oauth': oauth_name,
        'settings': runtime.settings.get(extension, {})}, sort_keys=True, allow_nan=False).encode())
    total = 0
    for path in files:
        with path.open('rb') as source:
            data = source.read(8 * 1024 * 1024 + 1)
        total += len(data)
        if total > 8 * 1024 * 1024:
            raise ValueError('Provider credential source binding exceeds content budget')
        digest.update(str(path.relative_to(directory)).encode() + b'\0' + data + b'\0')
    return digest.hexdigest()


async def control(runtime: Any, identifier: str, action: str, *,
                  ask: Callable[[dict[str, Any]], Awaitable[Any]], alive: Callable[[], bool],
                  storage: dict[str, str] | None = None) -> dict[str, Any]:
    if not isinstance(action, str) or action not in ACTIONS or not callable(ask) or not callable(alive):
        raise ValueError('Invalid provider authentication action/review owner')
    selected = next(((extension, row) for extension, info in tuple(runtime._infos.items())
        if extension not in runtime._failed | runtime._unloaded
        for row in info.get('providers', []) if 'oauth' in row
        and identifier == 'ext-' + extension + '-' + row['name']), None)
    if selected is None or not alive() or runtime._closed:
        raise ValueError('Provider authentication selection/host is unavailable')
    extension, declaration = selected
    name, registration_id = declaration['name'], declaration['registration_id']
    client, generation = runtime._manager._clients.get(extension), runtime.generation
    if client is None:
        raise ValueError('Provider authentication worker is unavailable')
    reviewed_revision = client._provider_snapshot[0] if client._provider_snapshot is not None else -1
    storage_value = storage_config(storage) if action in {'save', 'restore', 'forget'} else None

    def current() -> bool:
        return (alive() and not runtime._closed and runtime.generation == generation
            and runtime._manager._clients.get(extension) is client
            and extension not in runtime._failed | runtime._unloaded
            and any(row['name'] == name and row.get('registration_id') == registration_id
                    for row in runtime._infos.get(extension, {}).get('providers', [])))

    common = {'provider_id': identifier, 'name': declaration['oauth']['name'], 'action': action}

    async def review(worker: str, prompt: dict[str, Any]) -> Any:
        challenge = prompt.get('provider_auth')
        if (worker != extension or not current() or not isinstance(challenge, dict)
                or challenge.get('provider') != name):
            raise ValueError('Foreign provider authentication challenge')
        phase = challenge.get('phase')
        if phase == 'on_auth' and prompt.get('kind') == 'confirm' and set(challenge) == {
                'provider', 'phase', 'url', 'instructions'}:
            authorization_url(challenge['url'], challenge['instructions'])
            request = {**common, 'phase': phase, 'url': challenge['url'], 'instructions': challenge['instructions']}
        elif (phase == 'on_prompt' and prompt.get('kind') == 'input'
                and set(challenge) == {'provider', 'phase', 'prompt'}):
            authorization_prompt(challenge['prompt'])
            request = {**common, 'phase': phase, 'prompt': challenge['prompt'], 'sensitive': True}
        else:
            raise ValueError('Unsupported provider authentication challenge')
        result = await ask(request)
        if not current():
            raise ValueError('Provider authentication owner changed after review')
        return result

    async def run() -> dict[str, Any]:
        status = public_oauth(declaration['oauth'])
        status['authenticated'] = status['authenticated'] and status['expires'] > time.time() * 1000
        consent = {**common, 'phase': 'consent', **status,
                   'notice': 'Explicit provider authentication only; no model/tool call or permission change. '
                       'Cancel/uncertain operations are not replayed. Logout clears memory, not saved credentials.'}
        if storage_value is not None:
            consent['storage'] = dict(storage_value)
        if await ask(consent) is not True or not current():
            return {'ok': False, **status}
        params: dict[str, Any] = {'provider': name, 'registration_id': registration_id, 'action': action}
        if storage_value is not None:
            params.update(storage=storage_value,
                          binding=await asyncio.to_thread(_binding, runtime, extension, name, common['name']))
        locked = [False]

        async def lock() -> None:
            await runtime._provider_lock.acquire()
            locked[0] = True

        token = AUTH_REVIEW.set(review)
        try:
            await runtime._manager._bridge(lock())
            if (not current() or client._provider_snapshot is None
                    or client._provider_snapshot[0] != reviewed_revision):
                raise ValueError('Provider authentication owner/credential revision changed before admission')
            client._provider_auth_active = True
            result = await runtime._manager._bridge(runtime._rpc(extension, 'xdog/provider_auth', params))
            if not current() or set(result) != {'ok', 'name', 'authenticated', 'expires', 'refresh_available'}:
                raise ValueError('Invalid provider authentication receipt/owner')
            public_oauth({key: value for key, value in result.items() if key != 'ok'})
            if result['ok'] is not True:
                raise ValueError('Invalid provider authentication receipt')
            return dict(result)
        finally:
            AUTH_REVIEW.reset(token)
            client._provider_auth_active = False
            if locked[0] and not runtime._manager._closed:
                async def unlock() -> None:
                    runtime._provider_lock.release()
                await runtime._manager._bridge(unlock())

    task = asyncio.create_task(run())
    try:
        async with asyncio.timeout(600):
            while not task.done():
                if not current() and extension not in runtime._failed:
                    task.cancel()
                    raise ValueError('Provider authentication owner was withdrawn')
                await asyncio.wait({task}, timeout=0.05)
            return task.result()
    except asyncio.CancelledError:
        raise
    except Exception:
        raise ValueError('Extension provider authentication failed or was canceled; no replay') from None
    finally:
        if not task.done():
            task.cancel()
        while not task.done():
            try:
                await asyncio.shield(task)
            except BaseException:
                pass
        if not task.cancelled():
            task.exception()


async def close_auth_client(client: Any) -> None:
    """Allow worker-owned native helpers to settle before the ordinary forced reap.

    The normal 200-ms process grace is shorter than a native helper's bounded
    cancellation cleanup. EOF first cancels worker callback tasks; shield the
    bounded grace against repeated caller cancellation, then use normal teardown.
    """
    async def cleanup() -> None:
        process = client.process
        if process is not None and process.returncode is None:
            if process.stdin is not None:
                process.stdin.close()
            try:
                await asyncio.wait_for(process.wait(), 12)
            except (TimeoutError, ProcessLookupError):
                pass
        from xdog.agent.mcp.stdio import StdioClient
        await StdioClient.close(client)

    task = asyncio.create_task(cleanup())
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
    task.result()
