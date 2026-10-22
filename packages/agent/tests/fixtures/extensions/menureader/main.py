import json


def setup(api):
    executed = 0

    async def unused(args, context):
        nonlocal executed
        executed += 1
        return 'NEVER RUN FROM MENU READ'
    api.command('unused', 'Metadata is not execution permission', unused)

    async def probe(scope, context):
        if scope != 'command':
            assert not hasattr(api, 'get_editor') and not hasattr(api, 'open_sessions')
        first = await api.get_command_catalog(timeout=5)
        rows = [] if first is None else list(first['commands'])
        if first is not None:
            offset = first['next_offset']
            while offset is not None:
                page = await api.get_command_catalog(offset=offset, revision=first['revision'], timeout=5)
                assert page is not None and page['status'] == 'ok'
                rows.extend(page['commands'])
                offset = page['next_offset']
            first['commands'].clear()
            again = await api.get_command_catalog(timeout=5)
            assert again is not None and len(again['commands']) == min(32, len(rows))
        result = {'scope': scope, 'available': first is not None, 'session_id': context.get('session_id'),
                  'child_id': context.get('child_id', ''), 'names': [row['name'] for row in rows],
                  'executed': executed}
        api.append_entry('menu_probe', result)
        return json.dumps(result, sort_keys=True)

    async def command(args, context):
        return await probe('command', context)

    async def tool(args, context):
        return await probe('tool', context)

    async def event(context):
        if api.settings.get('scope') == 'event':
            await probe('event', context)

    async def pre(name, arguments, context):
        if api.settings.get('scope') == 'pre':
            await probe('pre', context)
        return arguments

    async def post(name, arguments, result, context):
        if api.settings.get('scope') == 'post':
            await probe('post', context)
        return result

    async def input_hook(text, metadata, context):
        if api.settings.get('scope') == 'input':
            await probe('input', context)
        return {'action': 'continue'}

    async def transform(messages, context):
        if api.settings.get('scope') == 'context':
            await probe('context', context)
        return messages


    async def provider(model, context, options):
        assert await api.get_command_catalog() is None
        yield {'type': 'text', 'text': 'PROVIDER-MENU-REFUSED'}
        yield {'type': 'done'}

    api.command('inspect', 'Read original host menu metadata', command)
    api.on('turn_start', event)
    api.before_tool(pre)
    api.after_tool(post)
    api.on_input(input_hook)
    api.context(transform)
    api.register_provider('offline', [{'id': 'scope', 'context_window': 32000, 'max_tokens': 2048}], provider)
