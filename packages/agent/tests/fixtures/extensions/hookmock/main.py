import json


def setup(api):
    active, retired = [], []
    counts = {'event': 0, 'pre': 0, 'post': 0, 'context': 0, 'input': 0}

    async def event(context):
        counts['event'] += 1

    async def pre(name, arguments, context):
        counts['pre'] += 1
        return arguments

    async def post(name, arguments, value, context):
        counts['post'] += 1
        return value

    async def context(messages, metadata):
        counts['context'] += 1
        return messages

    async def input(text, metadata, context):
        counts['input'] += 1
        return 'LISTENER-' + text

    async def enable(args, context):
        for dispose in active:
            dispose()
        retired.extend(active)
        active[:] = [api.on('turn_start', event), api.before_tool(pre), api.after_tool(post),
                     api.context(globals_context), api.on_input(input)]
        return 'HOOKS-ENABLED'

    globals_context = context

    async def old(args, context):
        for dispose in retired:
            dispose()
        return 'OLD-HOOKS-DISPOSED'

    async def disable(args, context):
        for dispose in active:
            dispose()
            dispose()
        active.clear()
        return 'HOOKS-DISABLED'

    async def status(args, context):
        return json.dumps(counts, sort_keys=True)

    api.command('enable', 'Enable owned hook listeners without worker restart', enable)
    api.command('old', 'Call retired disposers without removing current listeners', old)
    api.command('disable', 'Dispose listeners idempotently', disable)
    api.command('status', 'Inspect hook callback counts', status)
