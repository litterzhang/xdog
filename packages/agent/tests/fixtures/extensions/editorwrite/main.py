import asyncio
from pathlib import Path


def setup(api):
    async def propose(scope, mode='ok'):
        # New proposals must never unlock old command-composer access.
        if scope != 'command':
            assert not hasattr(api, 'get_editor')
            assert not hasattr(api, 'set_editor')
        try:
            accepted = await api.request_editor_text('PROPOSED 界\nunsent', timeout=10)
            if api.settings.get('marker'):
                Path(api.settings['marker']).write_text(str(accepted))
            if mode == 'fail':
                raise ValueError('callback failed after review')
            if mode == 'error':
                return {'content': [{'type': 'text', 'text': 'business error'}], 'isError': True}
            return 'EDITOR-WRITE-' + str(accepted)
        finally:
            if api.settings.get('release'):
                async with asyncio.timeout(10):
                    while not Path(api.settings['release']).exists():
                        await asyncio.sleep(0.005)

    async def command(args, context):
        return await propose('command', args or 'ok')

    async def tool(args, context):
        return await propose('tool', args.get('mode', 'ok'))

    async def event(context):
        if api.settings.get('scope') == 'event':
            await propose('event')

    async def pre(name, arguments, context):
        if api.settings.get('scope') == 'pre':
            await propose('pre')
        return arguments

    async def post(name, arguments, result, context):
        if api.settings.get('scope') == 'post':
            await propose('post')
        return result

    async def input_hook(text, metadata, context):
        if api.settings.get('scope') == 'input':
            await propose('input')
        return {'action': 'continue'}

    async def transform(messages, context):
        if api.settings.get('scope') == 'context':
            await propose('context')
        return messages


    api.command('write', 'Propose editor text without reading/submitting', command)
    api.on('turn_start', event)
    api.before_tool(pre)
    api.after_tool(post)
    api.on_input(input_hook)
    api.context(transform)

    async def provider(model, context, options):
        assert not await api.request_editor_text('DENIED PROVIDER')
        yield {'type': 'text', 'text': 'PROVIDER-SCOPE-REFUSED'}
        yield {'type': 'done'}
    api.register_provider('offline', [{'id': 'scope', 'context_window': 32000, 'max_tokens': 2048}], provider)
