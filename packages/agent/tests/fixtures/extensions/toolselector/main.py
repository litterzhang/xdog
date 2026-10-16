

def setup(api):
    async def illegal(args, context):
        current = await api.get_active_tools()
        inventory = await api.get_tool_inventory()
        if current is not None:
            assert inventory is not None and current['session_id'] == context['session_id']
            assert current['revision'] == inventory['selection_revision']
            assert set(inventory['active_tools']) <= set(current['active'])
        assert not hasattr(api, 'set_active_tools')
        return 'NO-HOST-TOOL-AUTHORITY'
    async def inspect(args, context):
        current = await api.get_active_tools()
        return 'ACTIVE-TOOL-COUNT-' + str(len(current['active'])) if current is not None else 'UNAVAILABLE'
    api.command('inspect', 'Inspect active selection without a model call', inspect)
    api.tool('illegal', 'Tools cannot grant themselves active-tool host authority', {'type': 'object'}, illegal)
