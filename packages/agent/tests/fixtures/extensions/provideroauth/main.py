import asyncio
import time
from pathlib import Path

from xdog.agent.extensions.provider_oauth import ProviderOAuth

ACCESS = 'PRIVATE-OAUTH-ACCESS-SENTINEL'
REFRESH = 'PRIVATE-OAUTH-REFRESH-SENTINEL'
CODE = 'PRIVATE-OAUTH-CODE-SENTINEL'


def setup(api):
    counts = {'login': 0, 'refresh': 0, 'key': 0, 'models': 0, 'stream': 0}

    async def login(callbacks):
        counts['login'] += 1
        if api.settings.get('noisy_login'):
            print(ACCESS)
            api.append_entry('note', {'value': REFRESH})
        await callbacks.on_auth('https://auth.example.test/authorize?client_id=offline', 'Use the offline fixture code')
        code = await callbacks.on_prompt('Paste the provider authorization code')
        assert code == CODE
        if api.settings.get('hold_login'):
            Path(api.settings['marker']).write_text('entered')
            await asyncio.Event().wait()
        if api.settings.get('withdraw_login'):
            api.unregister_provider('local')
        return {'access': ACCESS, 'refresh': REFRESH,
                'expires': (time.time() - 1 if api.settings.get('expired') else time.time() + 600) * 1000,
                'account_id': 'private-account'}

    async def refresh(value):
        counts['refresh'] += 1
        assert value['refresh'] == REFRESH
        if api.settings.get('hold_refresh'):
            Path(api.settings['marker']).write_text('entered')
            await asyncio.Event().wait()
        if api.settings.get('bad_refresh'):
            raise ValueError(REFRESH)
        return {**value, 'access': ACCESS + '-rotated', 'refresh': REFRESH + '-rotated',
                'expires': (time.time() + 600) * 1000}

    def get_key(value):
        counts['key'] += 1
        return value['access']

    def modify(value, models):
        counts['models'] += 1
        if api.settings.get('empty_models'):
            return []
        if api.settings.get('bad_models'):
            return [{**models[0], 'secret': value['access']}]
        models[0]['context_window'] = 64000
        return models

    async def stream(model, context, options, credential):
        counts['stream'] += 1
        assert credential.value.startswith(ACCESS)
        yield {'type': 'text', 'text': 'OAUTH-OFFLINE-ANSWER'}
        yield {'type': 'done'}

    if api.settings.get('slow_store_cleanup'):
        from xdog.agent.mcp.oauth_native_store import NativeCredentialStore
        def native_run(self, action, resource, alive, payload=''):
            Path(api.settings['marker']).write_text('entered')
            while alive():
                time.sleep(.005)
            time.sleep(.4)  # Longer than the ordinary worker process grace.
            Path(api.settings['cleanup_marker']).write_text('closed')
            raise ValueError('private test helper cancellation')
        NativeCredentialStore._run = native_run

    api.register_provider('local', [{'id': 'echo', 'context_window': 32000, 'max_tokens': 2048}], stream,
        oauth=ProviderOAuth('Offline OAuth', login, refresh, get_key, modify))

    async def count(args, context):
        return ';'.join(f'{key}={value}' for key, value in counts.items())

    api.command('count', 'Inspect public callback counters', count)
