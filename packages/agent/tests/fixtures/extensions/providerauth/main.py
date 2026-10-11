import asyncio
import os
from pathlib import Path

from xdog.agent.extensions.provider_auth import ProviderAuth


def setup(api):
    calls = resolutions = 0
    source = 'XDOG_UI044_TEST_KEY'
    models = [{'id': 'echo', 'context_window': 32000, 'max_tokens': 2048}]

    async def resolve():
        nonlocal resolutions
        resolutions += 1
        assert not api.has_ui
        assert await api.input('Auth cannot borrow interactive authority') is None
        mode = api.settings.get('mode', '')
        if mode == 'hold':
            Path(api.settings['marker']).write_text('entered')
            await asyncio.Event().wait()
        if mode == 'fail':
            raise ValueError(os.environ.get(source, 'private resolver error'))
        if mode == 'withdraw':
            api.unregister_provider('local')
        return os.environ.get(source)

    async def stream(model, context, options, credential):
        nonlocal calls
        calls += 1
        assert credential.value == os.environ[source]
        assert credential.value not in repr(credential)
        assert credential.kind == api.settings.get('kind', 'bearer')
        assert not api.has_ui
        if api.settings.get('mode') == 'callback_fail':
            raise ValueError(credential.value)
        yield {'type': 'text', 'text': 'AUTHENTICATED-OFFLINE-ANSWER'}
        yield {'type': 'done'}

    def register():
        binding = ProviderAuth(kind=api.settings.get('kind', 'bearer'), **(
            {'env': source} if api.settings.get('env') else {'resolver': resolve}))
        return api.register_provider('local', models, stream, auth=binding)

    old = register()

    async def count(args, context):
        return f'CALLS={calls};RESOLUTIONS={resolutions}'

    async def rotate(args, context):
        os.environ[source] = os.environ.get(source, 'test') + '-rotated'
        return 'ROTATED'

    async def replace(args, context):
        api.unregister_provider('local')
        register()
        old()
        return 'REPLACED'

    api.command('count', 'Inspect counts only', count)
    api.command('rotate', 'Rotate offline fixture credential', rotate)
    api.command('replace', 'Replace offline fixture registration', replace)
