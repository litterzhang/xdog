def setup(api):
    active, retired = [], []
    calls = 0

    async def target(args, context):
        nonlocal calls
        calls += 1
        return 'LIVE-COMMAND-ORIGINAL-' + str(calls)

    async def replacement(args, context):
        nonlocal calls
        calls += 1
        return 'LIVE-COMMAND-REPLACED-' + str(calls)

    async def enable(args, context):
        for dispose in active:
            dispose()
        retired.extend(active)
        active[:] = [api.shortcut('target', 'f6', 'Dynamic target', target)]
        return 'COMMAND-ENABLED'

    async def replace(args, context):
        active.append(api.command('target', 'Replaced target', replacement, replace=True))
        return 'COMMAND-REPLACED'

    async def old(args, context):
        for dispose in retired:
            dispose()
        return 'OLD-COMMAND-DISPOSERS-CALLED'

    async def disable(args, context):
        for dispose in active:
            dispose()
            dispose()
        active.clear()
        return 'COMMAND-DISPOSED'

    api.command('enable', 'Add a live command and shortcut', enable)
    api.command('replace', 'Explicitly replace the current command callback', replace)
    api.command('old', 'Call retired disposers without removing current registrations', old)
    api.command('disable', 'Dispose the owned command and shortcut', disable)
