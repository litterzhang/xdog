def setup(api):
    async def retire(params, context):
        dispose()
        return 'TOOL-RETIRED'
    dispose = api.tool('target', 'Dispose own tool during actual execution', {'type': 'object'}, retire)
