import json


def setup(api):
    async def command(args, context):
        options = json.loads(args or '{}')
        queued = await api.request_context_message('note', 'RETAINED-CUSTOM-CONTENT',
            display=options.get('display', True), details={'opaque': 'PRIVATE-NOT-MODEL'}, timeout=10)
        if options.get('fail'):
            raise ValueError('Injected callback failure after context review')
        return 'CONTEXT-QUEUED ' + json.dumps(queued)

    api.command('request', 'Review retained context without starting a turn', command)
