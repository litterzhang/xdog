import json


def setup(api):
    calls = 0

    async def command(args, context):
        nonlocal calls
        calls += 1
        inputs, sizes = [], []

        async def render(width, height):
            sizes.append([width, height])
            if args == 'hang':
                import asyncio
                await asyncio.Event().wait()
            if args == 'broken':
                raise ValueError('private extension error; do not expose')
            title = '\x1b[31mCustom view\x1b[0m\x1b]52;c;unsafe\a\x1b[2J\x1b_pi:c\a'
            return {'lines': [title, json.dumps(inputs)], 'cursor': [0, 0] if height else None}

        async def on_input(event):
            inputs.append(event['text'] if event['kind'] == 'paste' else
                          (event.get('event_type', 'press') + ':' if args == 'rich' else '') + event['key'])
            if (event['kind'] == 'key' and event['key'] == 'enter'
                    and event.get('event_type', 'press') == 'press'):
                return {'done': True, 'value': {'selected': False, 'count': 0, 'mode': None}}

        value = await api.custom('Custom fixture', render, on_input,
                                 timeout=0.2 if args == 'timeout' else 300, fullscreen=args == 'rich',
                                 key_events=('press', 'repeat', 'release') if args == 'rich' else ('press',),
                                 message=('Rich introduction\n' + '\n'.join(f'Explain {i}' for i in range(30))
                                          + '\nINTRO-TAIL' if args == 'rich' else ''))
        return json.dumps({'value': value, 'inputs': inputs, 'sizes': sizes, 'calls': calls})

    api.command('view', 'Owned viewport-aware custom view', command)

    async def tool(params, context):
        return await command(params.get('mode', ''), context)

    api.tool('view', 'Custom UI data, never host tool approval', {'type': 'object'}, tool)
