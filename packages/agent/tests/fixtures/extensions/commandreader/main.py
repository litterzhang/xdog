import json


def setup(api):
    executed = 0
    async def unused(args, context):
        nonlocal executed
        executed += 1
        return 'A metadata lookup must not execute this callback'
    for index in range(25):
        api.command(f'item{index:02}', f'Catalog sample metadata {index}', unused)

    async def inspect(args, context):
        query = args or ''
        first = await api.get_command_catalog(query=query)
        if first is None:
            return 'COMMAND-MENU-UNAVAILABLE'
        rows = list(first['commands'])
        offset = first['next_offset']
        while offset is not None:
            following = await api.get_command_catalog(query=query, offset=offset, revision=first['revision'])
            assert following['status'] == 'ok'
            rows.extend(following['commands'])
            offset = following['next_offset']
        assert len(rows) == first['total']
        if rows:
            first['commands'][0]['description'] = 'FORGED-LOCAL-MUTATION'
            copy = await api.get_command_catalog(query=query)
            assert all(row['description'] != 'FORGED-LOCAL-MUTATION' for row in copy['commands'])
        result = {'session_id': first['session_id'], 'source': first['source'], 'count': len(rows),
                  'names': [row['name'] for row in rows], 'executed': executed}
        api.append_entry('menu', result)
        return 'COMMAND-MENU ' + json.dumps(result, sort_keys=True)
    api.command('inspect', 'Read all terminal-menu pages without executing anything', inspect)

    async def illegal(args, context):
        assert await api.get_command_catalog() is None
        return 'TOOL-MENU-UNAVAILABLE'
