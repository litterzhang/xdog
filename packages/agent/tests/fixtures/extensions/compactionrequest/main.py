import json


def setup(api):
    async def command(args, context):
        options = json.loads(args or '{}')
        queued = await api.request_compaction(options.get('instructions', 'Keep COMPACTION-FOCUS'))
        browser = await api.open_sessions() if options.get('after_browser') else None
        if options.get('fail'):
            raise RuntimeError('Injected callback failure after review')
        return 'COMPACTION-QUEUED ' + json.dumps({'queued': queued, 'browser': browser})

    async def illegal(params, context):
        return 'COMPACTION-TOOL-' + str(await api.request_compaction('No borrowed command owner'))

    api.command('request', 'Request reviewed host compaction after callback success', command)
    api.tool('illegal', 'Tool callbacks cannot borrow parent command compaction', {'type': 'object'}, illegal)
