import asyncio


def setup(api):
    calls = 0
    unsubscribe = None

    async def hook(data, metadata, context):
        nonlocal calls
        calls += 1
        if data == 'bad':
            return {'consume': 'true'}
        if data == 'hang':
            await asyncio.Event().wait()
        if data == 'drop':
            return {'consume': True}
        if data == 'quiet':
            unsubscribe()
            return {'consume': True}
        if await api.input('Must not borrow UI', timeout=0.1) is not None:
            raise ValueError('Borrowed interactive authority')
        transformed = (''.join('[' + key + ']' for key in data) if metadata['kind'] == 'key'
                       else '[' + data + ']')
        return {'data': transformed, 'consume': False}

    unsubscribe = api.on_terminal_input(hook)

    async def count(args, context):
        return str(calls)
    api.command('count', 'Inspect terminal callback count without a model', count)

    async def enable(args, context):
        nonlocal unsubscribe
        if not api.terminal_hooks:
            unsubscribe = api.on_terminal_input(hook)
        return 'Terminal hook enabled'
    api.command('enable', 'Re-register a removed terminal hook without restarting a worker', enable)
