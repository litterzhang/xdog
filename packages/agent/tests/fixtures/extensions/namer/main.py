import asyncio
import json
from pathlib import Path


def setup(api):
    async def inspect(args, context):
        return 'NAME-INSPECT ' + json.dumps({'name': await api.get_session_name()}, ensure_ascii=False)

    async def rename(args, context):
        options = json.loads(args)
        before = await api.get_session_name()
        accepted = await api.set_session_name(options['name'])
        after = await api.get_session_name()
        if options.get('fail'):
            raise ValueError('Name callback failed after review')
        if options.get('hold') and accepted:
            Path(api.settings['marker']).write_text('queued')
            async with asyncio.timeout(10):
                while not Path(api.settings['release']).exists():
                    await asyncio.sleep(0.01)
        return 'NAME-QUEUED ' + json.dumps({'accepted': accepted, 'before': before, 'after': after}, ensure_ascii=False)

    async def tool(args, context):
        name = await api.get_session_name()
        assert not await api.set_session_name('TOOL-CANNOT-RENAME')
        return 'NAME-TOOL ' + json.dumps({'name': name}, ensure_ascii=False)

    async def started(context):
        api.append_entry('name_read', {'scope': 'event', 'name': await api.get_session_name()})

    async def pre(name, params, context):
        api.append_entry('name_read', {'scope': 'pre', 'name': await api.get_session_name()})
        assert not await api.set_session_name('HOOK-CANNOT-RENAME')
        return params

    async def post(name, params, text, context):
        api.append_entry('name_read', {'scope': 'post', 'name': await api.get_session_name()})
        return text

    async def transform(messages, context):
        api.append_entry('name_read', {'scope': 'context', 'name': await api.get_session_name()})
        return messages

    api.command('inspect', 'Inspect the actual session name without a model call', inspect)
    api.command('rename', 'Review a deferred owner-bound session name change', rename)
    api.tool('inspect', 'Read invocation-owned session name; never mutate it', {'type': 'object'}, tool)
    api.on('session_start', started)

    api.before_tool(pre)
    api.after_tool(post)
    api.context(transform)
