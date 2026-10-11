def setup(api):
    active, retired = [], []
    calls = 0
    schema = {'type': 'object', 'properties': {'value': {'type': 'string'}}, 'required': ['value']}

    async def original(params, context):
        nonlocal calls
        calls += 1
        return 'LIVE-TOOL-ORIGINAL-' + params['value']

    async def replacement(params, context):
        nonlocal calls
        calls += 1
        return 'LIVE-TOOL-REPLACED-' + str(params['value'])

    async def enable(args, context):
        active.append(api.tool('target', 'Original dynamic tool', schema, original))
        return 'TOOL-ENABLED'

    async def replace(args, context):
        retired.extend(active)
        active[:] = [api.tool('target', 'Replacement dynamic tool', {
            'type': 'object', 'properties': {'value': {'type': 'integer'}}, 'required': ['value']},
            replacement, replace=True)]
        return 'TOOL-REPLACED'

    async def old(args, context):
        for dispose in retired:
            dispose()
        return 'OLD-TOOL-DISPOSERS-CALLED'

    async def disable(args, context):
        for dispose in active:
            dispose()
            dispose()
        active.clear()
        return 'TOOL-DISPOSED'

    async def count(args, context):
        return str(calls)

    api.command('enable', 'Add a live tool with fresh registration identity', enable)
    api.command('replace', 'Replace callback/schema behind the alias explicitly', replace)
    api.command('old', 'Old disposers cannot remove replacement tools', old)
    api.command('disable', 'Dispose only owned tool registration', disable)
    api.command('count', 'Inspect actual execution count', count)

    async def upgrade(params, context):
        upgrade_dispose()
        active.append(api.tool('target', 'Added from actual tool callback', {
            'type': 'object', 'properties': {'value': {'type': 'integer'}}, 'required': ['value']}, replacement))
        return 'LIVE-TOOL-INVENTORY-CHANGED'

    upgrade_dispose = None
    async def chain(args, context):
        nonlocal upgrade_dispose
        upgrade_dispose = api.tool('upgrade', 'Replace inventory inside the same tool loop',
                                   {'type': 'object'}, upgrade)
        return 'TOOL-CHAIN-READY'
    api.command('chain', 'Register upgrade for a same-turn inventory test', chain)
