import json

from xdog.ai.types import ImageContent

PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5ioAAAAASUVORK5CYII="


def setup(api):
    async def command(args, context):
        options = json.loads(args or '{}')
        images = (ImageContent(data=PNG, mime_type='image/png'),) if options.get('images') else ()
        queued = await api.request_message(options.get('text', '/quit'), images=images)
        browser = await api.open_sessions() if options.get('after_browser') else None
        if options.get('fail'):
            raise RuntimeError('Injected callback failure after review')
        return 'MESSAGE-QUEUED ' + json.dumps({'queued': queued, 'browser': browser})

    async def illegal(params, context):
        return 'MESSAGE-TOOL-' + str(await api.request_message('No borrowed command owner'))

    api.command('request', 'Request reviewed host message after callback success', command)
