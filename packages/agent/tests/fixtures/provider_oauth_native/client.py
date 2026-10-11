"""Offline provider callbacks plus a real encrypted vault on an isolated test bus."""
import asyncio
import json
import os
import shutil
import subprocess
from pathlib import Path

from xdog.agent.extensions.providers import ExtensionProviderStream
from xdog.agent.extensions.runtime import ExtensionRuntime
from xdog.ai.types import Context, StreamOptions

root = Path(os.environ['XDOG_NATIVE_TEST_ROOT'])
extensions = root / 'extensions'
shutil.copytree(Path(os.environ['XDOG_PROVIDER_OAUTH_FIXTURE']), extensions / 'provideroauth')
provider, alias = 'ext-provideroauth-local', 'ext-provideroauth-local/echo'
storage = {'backend': 'secret-service', 'namespace': str(root / 'namespace')}


async def ask(request):
    assert all(secret not in repr(request) for secret in ('PRIVATE-OAUTH-ACCESS', 'PRIVATE-OAUTH-REFRESH'))
    return 'PRIVATE-OAUTH-CODE-SENTINEL' if request['phase'] == 'on_prompt' else True


async def start():
    runtime = ExtensionRuntime(['provideroauth'], extensions, root)
    await runtime.initialize()
    return runtime


async def action(runtime, operation, selected=storage):
    return await runtime.provider_auth_control(provider, operation, ask=ask, alive=lambda: True, storage=selected)


async def run():
    runtime = await start()
    try:
        await action(runtime, 'login')
        await action(runtime, 'save')
        assert not Path(storage['namespace']).exists()
    finally:
        await runtime.aclose()
    # Source changes and a foreign namespace cannot restore the original credential.
    file = extensions / 'provideroauth/main.py'
    original = file.read_bytes()
    file.write_bytes(original + b'\n# Different extension source binding\n')
    runtime = await start()
    try:
        try:
            await action(runtime, 'restore')
        except ValueError:
            pass
        else:
            raise AssertionError('Changed source restored credentials')
    finally:
        await runtime.aclose()
        file.write_bytes(original)
    runtime = await start()
    try:
        try:
            await action(runtime, 'restore', {**storage, 'namespace': str(root / 'foreign')})
        except ValueError:
            pass
        else:
            raise AssertionError('Foreign vault namespace restored credentials')
    finally:
        await runtime.aclose()
    runtime = await start()
    try:
        assert not runtime.provider_auth_catalog()[0]['authenticated']
        assert await runtime.command('ext-provideroauth-count', '') == 'login=0;refresh=0;key=0;models=0;stream=0'
        await action(runtime, 'restore')
        assert runtime.provider_auth_catalog()[0]['authenticated']
        assert runtime.provider_model(alias).context_window == 64000
        await action(runtime, 'refresh')
        await action(runtime, 'save')
        await action(runtime, 'logout')
        await action(runtime, 'restore')
        adapter = ExtensionProviderStream(runtime, lambda *args: (_ for _ in ()).throw(AssertionError('no fallback')))
        result = await adapter(alias, Context(), StreamOptions()).result()
        assert result.content[0].text == 'OAUTH-OFFLINE-ANSWER'
        assert await runtime.command('ext-provideroauth-count', '') == 'login=0;refresh=1;key=1;models=3;stream=1'
        assert not Path(storage['namespace']).exists()
        assert 'PRIVATE-OAUTH-' not in repr(runtime._infos)
        await action(runtime, 'forget')
        await action(runtime, 'logout')
        try:
            await action(runtime, 'restore')
        except ValueError:
            pass
        else:
            raise AssertionError('Forgotten credential reappeared')
    finally:
        await runtime.aclose()
    # A real locked vault refuses restore, without prompting/unlocking or fallback.
    runtime = await start()
    try:
        await action(runtime, 'login')
        await action(runtime, 'save')
    finally:
        await runtime.aclose()
    lock = subprocess.run(['gdbus', 'call', '--session', '--dest', 'org.freedesktop.secrets',
        '--object-path', '/org/freedesktop/secrets', '--method', 'org.freedesktop.Secret.Service.Lock',
        "['/org/freedesktop/secrets/collection/login']"], capture_output=True, timeout=3)
    assert lock.returncode == 0
    runtime = await start()
    try:
        try:
            await action(runtime, 'restore')
        except ValueError:
            pass
        else:
            raise AssertionError('Locked provider credential was restored')
        assert not Path(storage['namespace']).exists()
    finally:
        await runtime.aclose()


asyncio.run(run())
print(json.dumps({'provider_roundtrip': True, 'source_namespace_binding': True,
                  'private_restart_refresh': True, 'forgotten_refused': True, 'locked_refused': True}))
