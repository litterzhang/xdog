import asyncio
from pathlib import Path

from xdog.ai.types import ImageContent

PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5ioAAAAASUVORK5CYII='


def setup(api):
    async def propose(scope, mode='ok'):
        if scope != 'command':
            assert not hasattr(api, 'get_editor')
            assert not hasattr(api, 'set_editor')
        try:
            images = (ImageContent(data=PNG, mime_type='image/png'),) if api.settings.get('image') else ()
            accepted = await api.request_queued_input(' QUEUED 界\n/quit ',
                delivery=api.settings.get('delivery', 'steer'), images=images, timeout=10)
            if api.settings.get('marker'):
                Path(api.settings['marker']).write_text(str(accepted))
            if mode == 'fail':
                raise ValueError('callback failed after queue review')
            if mode == 'error':
                return {'content': [{'type': 'text', 'text': 'business error'}], 'isError': True}
            return 'QUEUE-' + str(accepted)
        finally:
            if api.settings.get('release'):
                async with asyncio.timeout(10):
                    while not Path(api.settings['release']).exists():
                        await asyncio.sleep(0.005)

    async def command(args, context):
        return await propose('command')

    async def tool(args, context):
        if api.settings.get('before_request'):
            Path(api.settings['before_request']).touch()
            while not Path(api.settings['allow_request']).exists():
                await asyncio.sleep(0.005)
        return await propose('tool', args.get('mode', 'ok'))

    async def event(context):
        if api.settings.get('scope') == 'event':
            await propose('event')

    async def pre(name, args, context):
        if api.settings.get('scope') == 'pre':
            await propose('pre')
        return args

    async def post(name, args, result, context):
        if api.settings.get('scope') == 'post':
            await propose('post')
        return result

    async def input_hook(text, metadata, context):
        if api.settings.get('scope') == 'input':
            await propose('input')
        return {'action': 'continue'}

    async def transform(messages, context):
        if api.settings.get('scope') == 'context':
            assert await propose('context') == 'QUEUE-False'
        return messages


    api.command('request', 'Idle command does not gain active-run queue authority', command)
    api.on('turn_start', event)
    api.before_tool(pre)
    api.after_tool(post)
    api.on_input(input_hook)
    api.context(transform)

    async def provider(model, context, options):
        assert not await api.request_queued_input('DENIED PROVIDER')
        yield {'type': 'text', 'text': 'PROVIDER-SCOPE-REFUSED'}
        yield {'type': 'done'}
    api.register_provider('offline', [{'id': 'scope', 'context_window': 32000, 'max_tokens': 2048}], provider)

    async def pending(args, context):
        import json
        first = await api.has_pending_messages()
        second = await api.has_pending_messages()
        return json.dumps([first, second])
    api.command('pending', 'Live own-Agent pending boolean, never message bodies', pending)
