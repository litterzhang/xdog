import json


def setup(api):
    async def inspect(scope, context):
        model = await api.get_model()
        usage = await api.get_context_usage()
        value = {'scope': scope, 'child_id': context.get('child_id', ''), 'model': model, 'usage': usage}
        # Read-only snapshots do not acquire command/editor/navigation authority.
        if scope != 'command':
            assert await api.get_session() is None
            assert not hasattr(api, 'open_sessions')
            assert not hasattr(api, 'navigate_session')
        api.append_entry('inspection', value)
        return value

    async def command(args, context):
        return 'CALLBACK-INSPECTION ' + json.dumps(await inspect('command', context), sort_keys=True)

    async def tool(params, context):
        return 'CALLBACK-INSPECTION ' + json.dumps(await inspect('tool', context), sort_keys=True)

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

    api.command('inspect', 'Read invocation-owned model/context without UI or model work', command)
    api.on('session_start', event)
    api.on('turn_start', event)
    api.before_tool(pre)
    api.after_tool(post)
    api.context(transform)
