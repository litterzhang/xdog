import json


def setup(api):
    async def select(args, context):
        current = await api.get_active_tools()
        if current is None:
            return 'ACTIVE-TOOLS-UNAVAILABLE'
        options = json.loads(args)
        accepted = await api.set_active_tools(options['names'], current['revision'])
        if options.get('fail'):
            raise ValueError('Callback failed after approval')
        return 'TOOLS-QUEUED ' + json.dumps({'accepted': accepted, 'before': current,
                                           'after': await api.get_active_tools()})
    async def illegal(args, context):
        current = await api.get_active_tools()
        inventory = await api.get_tool_inventory()
        if current is not None:
            assert inventory is not None and current['session_id'] == context['session_id']
            assert current['revision'] == inventory['selection_revision']
            assert set(inventory['active_tools']) <= set(current['active'])
        assert not await api.set_active_tools([], current['revision'] if current is not None else 0)
        return 'NO-HOST-TOOL-AUTHORITY'
    async def inspect(args, context):
        current = await api.get_active_tools()
        return 'ACTIVE-TOOL-COUNT-' + str(len(current['active'])) if current is not None else 'UNAVAILABLE'
    api.command('inspect', 'Inspect active selection without a model call', inspect)
    api.command('select', 'Request reviewed host active-tool selection', select)
    api.tool('illegal', 'Tools cannot grant themselves active-tool host authority', {'type': 'object'}, illegal)
