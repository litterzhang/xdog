import json


def setup(api):
    calls = 0

    async def command(args, context):
        nonlocal calls
        calls += 1
        values = []

        async def render(width, height):
            return {'lines': ['NESTED-PARENT', json.dumps(values)], 'cursor': [0, 0] if height else None}

        async def child_render(width, height):
            return ['NESTED-CUSTOM-CHILD', json.dumps(values)]

        async def child_input(event):
            if event['kind'] == 'key' and event['key'] == 'n':
                values.append(await api.input('Grandchild input', default='grandchild'))
            if event['kind'] == 'key' and event['key'] == 'enter':
                return {'done': True, 'value': {'child': False}}

        async def on_input(event):
            if event['kind'] == 'key' and event['key'] == 'n':
                # Must not gain command-editor/session authority merely because
                # this custom view was opened by a command.
                if await api.get_editor() is not None or await api.get_session() is not None:
                    raise ValueError('Borrowed command authority')
                if args == 'custom':
                    values.append(await api.custom('Nested custom', child_render, child_input))
                else:
                    values.append(await api.input('Nested input', default='seed',
                                                 timeout=0.1 if args == 'timeout' else 300))
                    values.append(await api.confirm('Nested confirm', 'Not tool approval'))
            if event['kind'] == 'key' and event['key'] == 'enter':
                return {'done': True, 'value': {'values': values}}

        value = await api.custom('Nested parent', render, on_input, fullscreen=args == 'full')
        return json.dumps({'calls': calls, 'value': value, 'values': values})

    api.command('view', 'Owned nested dialogs in a custom view', command)

    async def tool(params, context):
        return await command(params.get('mode', ''), context)
