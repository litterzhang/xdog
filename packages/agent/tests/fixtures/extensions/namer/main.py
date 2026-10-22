import json


def setup(api):
    async def inspect(args, context):
        return 'NAME-INSPECT ' + json.dumps({'name': await api.get_session_name()}, ensure_ascii=False)

    async def tool(args, context):
        name = await api.get_session_name()
        assert not hasattr(api, 'set_session_name')
        return 'NAME-TOOL ' + json.dumps({'name': name}, ensure_ascii=False)

    async def started(context):
        api.append_entry('name_read', {'scope': 'event', 'name': await api.get_session_name()})

    async def pre(name, params, context):
        api.append_entry('name_read', {'scope': 'pre', 'name': await api.get_session_name()})
        assert not hasattr(api, 'set_session_name')
        return params

    async def post(name, params, text, context):
        api.append_entry('name_read', {'scope': 'post', 'name': await api.get_session_name()})
        return text

    async def transform(messages, context):
        api.append_entry('name_read', {'scope': 'context', 'name': await api.get_session_name()})
        return messages

    api.command('inspect', 'Inspect the actual session name without a model call', inspect)
    api.on('session_start', started)

    api.before_tool(pre)
    api.after_tool(post)
    api.context(transform)
