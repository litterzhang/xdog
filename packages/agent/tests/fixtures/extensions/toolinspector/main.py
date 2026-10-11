import json


def setup(api):
    commands = 0
    async def inspect(scope, context):
        first = await api.get_tool_inventory()
        if first is None:
            result = {'scope': scope, 'available': False}
        else:
            rows = list(first['tools'])
            active = list(first['active_tools']) if 'active_tools' in first else None
            offset = first['next_offset']
            while offset is not None:
                following = await api.get_tool_inventory(offset=offset, revision=first['revision'])
                assert following['status'] == 'ok'
                rows.extend(following['tools'])
                if active is not None:
                    active.extend(following['active_tools'])
                offset = following['next_offset']
            names = [row['name'] for row in rows]
            result = {key: first[key]
                      for key in ('session_id', 'owner_id', 'model', 'api_enabled', 'source', 'revision')}
            result.update(scope=scope, available=True, names=names, count=first['total'])
            if active is not None:
                result.update(active_names=active, selection_mode=first['selection_mode'],
                              selection_revision=first['selection_revision'])
            assert len(names) == first['total']
            if scope == 'command':
                # Returned schema metadata is copied, including false/zero/null.
                wanted = await api.get_tool_inventory(query=context.get('child_id', '') or 'native_probe')
                result['selected'] = wanted['tools']
                if first['tools']:
                    first['tools'][0]['parameters']['FORGED'] = True
                    again = await api.get_tool_inventory()
                    assert 'FORGED' not in again['tools'][0]['parameters']
            else:
                api.append_entry('inventory', result)
        return result

    async def command(args, context):
        nonlocal commands
        commands += 1
        value = await inspect('command', context)
        if args.strip() == 'count':
            value['command_count'] = commands
        return 'TOOL-INVENTORY ' + json.dumps(value, sort_keys=True)

    async def tool(params, context):
        return 'TOOL-INVENTORY ' + json.dumps(await inspect('tool', context), sort_keys=True)

    async def event(context):
        await inspect('event', context)

    async def pre(name, arguments, context):
        await inspect('pre', context)
        return arguments

    async def post(name, arguments, result, context):
        await inspect('post', context)
        return result

    async def transform(messages, context):
        await inspect('context', context)
        return messages

    api.command('inspect', 'Read owning configured tools, never permissions', command)
    api.tool('inspect', 'Read invocation tools, never execute a listed tool', {'type': 'object'}, tool)
    api.on('session_start', event)
    api.on('turn_start', event)
    api.before_tool(pre)
    api.after_tool(post)
    api.context(transform)
